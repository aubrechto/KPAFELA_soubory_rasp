"""Convert all songs and upload every finished bundle to all ESP devices."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from queue import Empty, Queue

import paho.mqtt.client as mqtt

BASE_DIR = Path(__file__).resolve().parent.parent
SONGS_DIR = BASE_DIR / "songs"
OUTPUT_DIR = BASE_DIR / "Data" / "songs"
INSTRUMENTS = ("guitar", "bass", "drums")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("kapfela.upload")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert songs, rebuild playlist and upload them to every ESP."
    )
    parser.add_argument("--mqtt-host", default="192.168.50.1")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--mqtt-user")
    parser.add_argument("--mqtt-password")
    parser.add_argument("--chunk-size", type=int, default=1024)
    parser.add_argument("--chunk-delay", type=float, default=0.01,
                        help="pauza mezi chunky (s), aby ESP stihlo MQTT zpracovat")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=2,
                        help="kolikrat zopakovat upload jedne skladby po timeoutu/chybe")
    parser.add_argument("--skip-convert", action="store_true")
    parser.add_argument("--instruments", nargs="+", choices=INSTRUMENTS,
                        default=list(INSTRUMENTS))
    return parser.parse_args()


def run_conversion(skip: bool) -> None:
    if not skip:
        subprocess.run(
            [
                sys.executable,
                str(BASE_DIR / "tools" / "generate_songs.py"),
                "-s",
                str(SONGS_DIR / "*"),
                "-o",
                str(OUTPUT_DIR),
            ],
            cwd=BASE_DIR,
            check=True,
        )
    subprocess.run(
        [sys.executable, str(BASE_DIR / "tools" / "sync_playlist.py")],
        cwd=BASE_DIR,
        check=True,
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as song_file:
        for chunk in iter(lambda: song_file.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class UploadClient:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"kapfela-uploader-{int(time.time())}",
        )
        if args.mqtt_user:
            self.client.username_pw_set(args.mqtt_user, args.mqtt_password or "")
        self.events: Queue[tuple[str, str, str, str, int, int, bool]] = Queue()
        self.connected = threading.Event()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code == 0:
            client.subscribe("kapfela/instrument/+/status", qos=1)
            self.connected.set()
        else:
            logger.error("MQTT connection failed: %s", reason_code)

    def _on_message(self, client, userdata, message) -> None:
        try:
            data = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        event = data.get("event")
        song_id = data.get("song_id")
        upload_id = data.get("upload_id")
        if event in ("upload_started", "upload_chunk_ack", "upload_chunk_error",
                     "upload_finished", "upload_error") and song_id and upload_id:
            instrument = message.topic.split("/")[2]
            self.events.put((instrument, event, song_id, upload_id,
                             int(data.get("chunk_index", -1)),
                             int(data.get("expected_index", -1)),
                             bool(data.get("success", False))))

    def connect(self) -> None:
        self.client.connect(self.args.mqtt_host, self.args.mqtt_port, keepalive=30)
        self.client.loop_start()
        if not self.connected.wait(self.args.timeout):
            raise RuntimeError("MQTT broker se nepripojil v danem timeoutu")
        logger.info("MQTT connected to %s:%s", self.args.mqtt_host, self.args.mqtt_port)

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def _wait_upload_event(self, instrument: str, song_id: str, upload_id: str,
                           expected_event: str, timeout: float,
                           chunk_index: int | None = None) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                received = self.events.get(
                    timeout=max(0.1, deadline - time.monotonic())
                )
            except Empty:
                break
            (received_instrument, event, received_song, received_upload,
             received_index, expected_index, success) = received
            if (received_instrument != instrument or received_song != song_id or
                    received_upload != upload_id):
                continue
            if event in ("upload_error", "upload_chunk_error"):
                raise RuntimeError(
                    f"ESP {instrument} odmítlo upload {song_id}: {event}, "
                    f"chunk={received_index}, expected={expected_index}"
                )
            if event != expected_event:
                continue
            if chunk_index is not None and received_index != chunk_index:
                continue
            if not success:
                raise RuntimeError(
                    f"ESP {instrument} nepotvrdilo {expected_event} "
                    f"pro {song_id}, chunk {received_index}"
                )
            return
        raise TimeoutError(
            f"ESP {instrument} nepotvrdilo {expected_event} pro {song_id}"
        )

    def _publish_and_wait(self, instrument: str, song_id: str, upload_id: str,
                          topic: str, payload: bytes | str, expected_event: str,
                          chunk_index: int | None = None) -> None:
        for attempt in range(self.args.retries + 1):
            info = self.client.publish(topic, payload, qos=1)
            info.wait_for_publish(timeout=self.args.timeout)
            if not info.is_published():
                raise TimeoutError(f"MQTT broker nepotvrdil zpravu na {topic}")
            try:
                self._wait_upload_event(
                    instrument, song_id, upload_id, expected_event,
                    self.args.timeout, chunk_index,
                )
                return
            except TimeoutError:
                if attempt >= self.args.retries:
                    raise
                logger.warning(
                    "%s %s: chybi %s (pokus %d/%d), opakuji stejnou zpravu",
                    song_id, instrument, expected_event, attempt + 1,
                    self.args.retries,
                )
        raise TimeoutError(f"ESP {instrument} nepotvrdilo {expected_event}")

    def upload(self, instrument: str, song_id: str, path: Path) -> None:
        size = path.stat().st_size
        total_chunks = (size + self.args.chunk_size - 1) // self.args.chunk_size
        upload_id = uuid.uuid4().hex
        topic = f"kapfela/song/{instrument}/upload"
        start = {
            "command": "upload_start",
            "song_id": song_id,
            "upload_id": upload_id,
            "size": size,
            "sha256": sha256(path),
            "total_chunks": total_chunks,
            "chunk_size": self.args.chunk_size,
        }
        self._publish_and_wait(instrument, song_id, upload_id, topic,
                               json.dumps(start), "upload_started")
        with path.open("rb") as song_file:
            for index in range(total_chunks):
                chunk = song_file.read(self.args.chunk_size)
                chunk_topic = f"{topic}/chunk/{upload_id}/{index}"
                self._publish_and_wait(
                    instrument, song_id, upload_id, chunk_topic, chunk,
                    "upload_chunk_ack", index,
                )
                if self.args.chunk_delay > 0:
                    time.sleep(self.args.chunk_delay)
                if (index + 1) % 25 == 0 or index + 1 == total_chunks:
                    logger.info("%s %s: chunk %d/%d", song_id, instrument,
                                index + 1, total_chunks)
        finish = {"command": "upload_finish", "song_id": song_id,
                  "upload_id": upload_id,
                  "total_chunks": total_chunks}
        self._publish_and_wait(instrument, song_id, upload_id, topic,
                               json.dumps(finish), "upload_finished")
        logger.info("ESP %s confirmed %s", instrument, song_id)


def main() -> int:
    args = parse_args()
    if args.chunk_size <= 0 or args.chunk_size > 1200:
        raise ValueError("chunk-size musi byt v rozsahu 1 az 1200")
    run_conversion(args.skip_convert)
    files = sorted(OUTPUT_DIR.glob("*.msg"))
    if not files:
        raise RuntimeError(f"V {OUTPUT_DIR} nejsou zadne hotove .msg soubory")

    uploader = UploadClient(args)
    try:
        uploader.connect()
        for path in files:
            song_id = path.stem
            for instrument in args.instruments:
                attempt = 0
                while True:
                    try:
                        uploader.upload(instrument, song_id, path)
                        break
                    except (TimeoutError, RuntimeError):
                        if attempt >= args.retries:
                            raise
                        attempt += 1
                        logger.warning(
                            "%s %s: upload selhal, zkousim znovu (%d/%d)",
                            song_id, instrument, attempt, args.retries,
                        )
        logger.info("Hotovo: %d skladeb odeslano na %d ESP", len(files),
                    len(args.instruments))
    finally:
        uploader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

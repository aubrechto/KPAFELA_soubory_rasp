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
    parser.add_argument("--timeout", type=float, default=30.0)
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
        self.events: Queue[tuple[str, str, bool]] = Queue()
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
        if event in ("upload_finished", "upload_error") and song_id:
            instrument = message.topic.split("/")[2]
            self.events.put((instrument, song_id, event == "upload_finished"))

    def connect(self) -> None:
        self.client.connect(self.args.mqtt_host, self.args.mqtt_port, keepalive=30)
        self.client.loop_start()
        if not self.connected.wait(self.args.timeout):
            raise RuntimeError("MQTT broker se nepripojil v danem timeoutu")
        logger.info("MQTT connected to %s:%s", self.args.mqtt_host, self.args.mqtt_port)

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def upload(self, instrument: str, song_id: str, path: Path) -> None:
        size = path.stat().st_size
        total_chunks = (size + self.args.chunk_size - 1) // self.args.chunk_size
        topic = f"kapfela/song/{instrument}/upload"
        start = {
            "command": "upload_start",
            "song_id": song_id,
            "size": size,
            "sha256": sha256(path),
            "total_chunks": total_chunks,
            "chunk_size": self.args.chunk_size,
        }
        self.client.publish(topic, json.dumps(start), qos=1).wait_for_publish()
        with path.open("rb") as song_file:
            for index in range(total_chunks):
                chunk = song_file.read(self.args.chunk_size)
                self.client.publish(topic, chunk, qos=1).wait_for_publish()
                if (index + 1) % 25 == 0 or index + 1 == total_chunks:
                    logger.info("%s %s: chunk %d/%d", song_id, instrument,
                                index + 1, total_chunks)
        finish = {"command": "upload_finish", "song_id": song_id,
                  "total_chunks": total_chunks}
        self.client.publish(topic, json.dumps(finish), qos=1).wait_for_publish()
        deadline = time.monotonic() + self.args.timeout
        while time.monotonic() < deadline:
            try:
                received_instrument, received_song, success = self.events.get(
                    timeout=max(0.1, deadline - time.monotonic())
                )
            except Empty:
                break
            if received_instrument == instrument and received_song == song_id:
                if not success:
                    raise RuntimeError(f"ESP {instrument} odmítlo skladbu {song_id}")
                logger.info("ESP %s confirmed %s", instrument, song_id)
                return
        raise TimeoutError(f"ESP {instrument} nepotvrdilo skladbu {song_id}")


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
                uploader.upload(instrument, song_id, path)
        logger.info("Hotovo: %d skladeb odeslano na %d ESP", len(files),
                    len(args.instruments))
    finally:
        uploader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

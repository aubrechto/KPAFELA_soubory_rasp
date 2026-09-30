"""MQTT bridge to the ESP microcontrollers with a simulation fallback.

When a broker is reachable the manager publishes commands on the
``kapfela/...`` topics and listens for status messages coming back from the
ESP devices. When no broker is available (for example in the v0 preview) it
transparently switches to simulation mode: commands are logged and a
synthetic status acknowledgement is generated so the UI stays fully usable.
"""
from __future__ import annotations

import json
import hashlib
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable

import paho.mqtt.client as mqtt

logger = logging.getLogger("kapfela.mqtt")

BROKER_HOST = os.environ.get("MQTT_HOST", "localhost")
BROKER_PORT = int(os.environ.get("MQTT_PORT", "1883"))

TOPIC_ROOT = "kapfela"
TOPIC_PLAYER = f"{TOPIC_ROOT}/player"
TOPIC_INSTRUMENT = f"{TOPIC_ROOT}/instrument"
TOPIC_SONG = f"{TOPIC_ROOT}/song"
INSTRUMENT_NAMES = ("guitar", "bass", "drums")
COMMAND_ACK_TIMEOUT = 1.5
COMMAND_ACK_RETRIES = 2
DEVICE_STATUS_MAX_AGE = 90.0

StatusHandler = Callable[[str, dict[str, Any]], None]

MappingHandler = Callable[[str, dict[str, Any]], None]


ConnectionHandler = Callable[[], None]


class MqttManager:
    def __init__(
        self,
        on_status: StatusHandler | None = None,
        on_connection: ConnectionHandler | None = None,
        on_mapping: MappingHandler | None = None,
    ) -> None:
        self.on_status = on_status
        self.on_connection = on_connection
        self.on_mapping = on_mapping
        self.connected = False
        self.simulation = False
        self._client: mqtt.Client | None = None
        self._lock = threading.Lock()
        self._command_lock = threading.Lock()
        self._ack_lock = threading.Lock()
        self._pending_command_acks: dict[str, dict[str, Any]] = {}
        self._upload_event_queues: dict[str, Queue[dict[str, Any]]] = {}
        self._last_device_status: dict[str, float] = {}

    def _notify_connection(self) -> None:
        if self.on_connection:
            self.on_connection()

    # -------------------------------------------------------------- lifecycle
    def start(self) -> None:
        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id="kapfela-controller",
            )
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            client.connect(BROKER_HOST, BROKER_PORT, keepalive=30)
            client.loop_start()
            self._client = client
            logger.info("Connecting to MQTT broker at %s:%s", BROKER_HOST, BROKER_PORT)
        except Exception as exc:  # noqa: BLE001 - broker optional in preview
            self.simulation = True
            self.connected = False
            logger.warning(
                "MQTT broker unavailable (%s) - running in simulation mode", exc
            )
            self._notify_connection()
            threading.Thread(target=self._retry_loop, daemon=True).start()

    def _retry_loop(self) -> None:
        time.sleep(5)
        while self._client is None and not self.connected:
            logger.info("Retrying MQTT connection to %s:%s", BROKER_HOST, BROKER_PORT)
            try:
                self.start()
            except Exception as exc:  # noqa: BLE001
                logger.warning("MQTT retry failed: %s", exc)
            if not self.connected:
                time.sleep(5)

    def stop(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def connection_info(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "simulation": self.simulation,
            "host": BROKER_HOST,
            "port": BROKER_PORT,
        }

    # --------------------------------------------------------------- publish
    def publish_player(self, command: str, payload: dict[str, Any] | None = None) -> None:
        data = {"command": command, **(payload or {})}
        self._publish(TOPIC_PLAYER, data)

    def publish_instrument(self, name: str, command: str) -> None:
        self._publish(f"{TOPIC_INSTRUMENT}/{name}", {"command": command})

    def publish_all_instruments(self, command: str) -> None:
        self._publish(TOPIC_INSTRUMENT, {"command": command})

    def publish_player_wait(self, command: str,
                            payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = {"command": command, **(payload or {})}
        return self._publish_command_wait(TOPIC_PLAYER, data,
                                          self._active_instruments())

    def publish_instrument_wait(self, name: str, command: str) -> dict[str, Any]:
        return self._publish_command_wait(
            f"{TOPIC_INSTRUMENT}/{name}", {"command": command}, {name}
        )

    def publish_all_instruments_wait(self, command: str) -> dict[str, Any]:
        return self._publish_command_wait(TOPIC_INSTRUMENT, {"command": command},
                                          self._active_instruments())

    def _active_instruments(self) -> set[str]:
        now = time.monotonic()
        with self._ack_lock:
            active = {
                name for name, received in self._last_device_status.items()
                if now - received <= DEVICE_STATUS_MAX_AGE
            }
        return active or set(INSTRUMENT_NAMES)

    def _publish_command_wait(self, topic: str, data: dict[str, Any],
                              expected: set[str]) -> dict[str, Any]:
        command_id = uuid.uuid4().hex
        if not self.connected or self._client is None:
            return {"command_id": command_id, "success": False,
                    "acknowledged": {}, "missing": sorted(expected),
                    "reason": "mqtt_disconnected"}

        pending = {
            "expected": set(expected),
            "acknowledged": {},
            "event": threading.Event(),
        }
        with self._command_lock:
            with self._ack_lock:
                self._pending_command_acks[command_id] = pending
            message = {**data, "command_id": command_id}
            try:
                for attempt in range(COMMAND_ACK_RETRIES + 1):
                    self._publish(topic, message)
                    if pending["event"].wait(COMMAND_ACK_TIMEOUT):
                        break
                    if attempt < COMMAND_ACK_RETRIES:
                        logger.warning(
                            "Command %s not acknowledged by %s; retry %d/%d",
                            command_id, sorted(expected), attempt + 1,
                            COMMAND_ACK_RETRIES,
                        )
            finally:
                with self._ack_lock:
                    self._pending_command_acks.pop(command_id, None)

        acknowledged = dict(pending["acknowledged"])
        missing = sorted(set(expected) - acknowledged.keys())
        return {
            "command_id": command_id,
            "success": not missing and all(acknowledged.values()),
            "acknowledged": acknowledged,
            "missing": missing,
        }

    def publish_config(self, name: str, config_data: dict[str, Any]) -> None:
        self._publish(f"{TOPIC_ROOT}/config/{name}", config_data)

    def publish_time(self, name: str, t0_ms: Any = None) -> None:
        """Push the Pi's current clock to one ESP so it can (re)sync.

        The ESP applies this epoch directly instead of pulling it itself via
        NTP, so every instrument ends up synchronized to the same source.
        When ``t0_ms`` (the ESP's own millis() at request time) is echoed
        back, the ESP can measure the round-trip and compensate for network
        latency instead of taking the epoch at face value.
        """
        payload: dict[str, Any] = {"epoch": time.time()}
        if t0_ms is not None:
            payload["t0_ms"] = t0_ms
        self._publish(f"{TOPIC_INSTRUMENT}/{name}/time", payload)

    def publish_instruments_config(self, config_data: dict[str, Any]) -> None:
        """Publish each instrument's config on its own topic.

        The full instruments JSON is larger than the ESP MQTT buffer, so each
        instrument receives only its own section on kapfela/instrument/<name>.
        """
        for name, instrument_config in config_data.items():
            if isinstance(instrument_config, dict):
                self._publish(f"{TOPIC_INSTRUMENT}/{name}", {name: instrument_config})

    def publish_song(self, instrument: str, song_id: str, path: str | Path,
                     chunk_size: int = 1024) -> bool:
        """Upload one already-converted track file to an ESP in MQTT chunks."""
        if self._client is None or not self.connected:
            self.simulation = True
            logger.info("SIM  -> song upload skipped: %s", path)
            return False
        if chunk_size <= 0 or chunk_size > 1200:
            raise ValueError("chunk_size must be between 1 and 1200")

        song_path = Path(path)
        if not song_path.is_file():
            raise FileNotFoundError(song_path)

        size = song_path.stat().st_size
        total_chunks = (size + chunk_size - 1) // chunk_size
        upload_id = uuid.uuid4().hex
        topic = f"{TOPIC_SONG}/{instrument}/upload"
        events: Queue[dict[str, Any]] = Queue()
        with self._ack_lock:
            self._upload_event_queues[upload_id] = events
        try:
            self._publish_upload_and_wait(topic, {
            "command": "upload_start",
            "song_id": song_id,
            "upload_id": upload_id,
            "size": size,
            "sha256": _file_sha256(song_path),
            "total_chunks": total_chunks,
            "chunk_size": chunk_size,
            }, events, upload_id, "upload_started")

            with song_path.open("rb") as song_file:
                for index in range(total_chunks):
                    chunk = song_file.read(chunk_size)
                    chunk_topic = f"{topic}/chunk/{upload_id}/{index}"
                    self._publish_upload_and_wait(
                        chunk_topic, chunk, events, upload_id,
                        "upload_chunk_ack", index,
                    )
                    logger.info("MQTT -> %s chunk %s/%s", topic, index + 1,
                                total_chunks)

            self._publish_upload_and_wait(topic, {
                "command": "upload_finish",
                "song_id": song_id,
                "upload_id": upload_id,
                "total_chunks": total_chunks,
            }, events, upload_id, "upload_finished")
        finally:
            with self._ack_lock:
                self._upload_event_queues.pop(upload_id, None)
        return True

    def _publish_upload_and_wait(
        self, topic: str, payload: dict[str, Any] | bytes,
        events: Queue[dict[str, Any]], upload_id: str, expected_event: str,
        chunk_index: int | None = None,
    ) -> None:
        message: str | bytes = (
            json.dumps(payload) if isinstance(payload, dict) else payload
        )
        for attempt in range(COMMAND_ACK_RETRIES + 1):
            if self._client is None or not self.connected:
                raise RuntimeError("MQTT disconnected during song upload")
            info = self._client.publish(topic, message, qos=1)
            try:
                info.wait_for_publish(timeout=30)
            except (RuntimeError, ValueError):
                pass

            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                try:
                    event = events.get(timeout=max(0.1, deadline - time.monotonic()))
                except Empty:
                    break
                if event.get("upload_id") != upload_id:
                    continue
                name = event.get("event")
                if name in ("upload_error", "upload_chunk_error"):
                    raise RuntimeError(
                        f"ESP {event.get('instrument')} upload failed: {event}"
                    )
                if name != expected_event:
                    continue
                if (chunk_index is not None and
                        event.get("chunk_index") != chunk_index):
                    continue
                if event.get("success") is True:
                    return
                raise RuntimeError(f"ESP upload acknowledgement failed: {event}")
            if attempt < COMMAND_ACK_RETRIES:
                logger.warning("Upload event %s missing; retry %d/%d",
                               expected_event, attempt + 1, COMMAND_ACK_RETRIES)
        raise TimeoutError(f"ESP did not acknowledge {expected_event} for {upload_id}")

    def _publish(self, topic: str, data: dict[str, Any]) -> None:
        message = json.dumps(data)
        if self._client is not None and self.connected:
            self._client.publish(topic, message, qos=1)
            logger.info("MQTT -> %s %s", topic, message)
        else:
            self.simulation = True
            logger.info("SIM  -> %s %s", topic, message)

    # -------------------------------------------------------------- callbacks
    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code == 0:
            self.connected = True
            self.simulation = False
            client.subscribe(f"{TOPIC_ROOT}/#", qos=1)
            logger.info("MQTT connected and subscribed to %s/#", TOPIC_ROOT)
        else:
            self.connected = False
            self.simulation = True
            logger.warning("MQTT connect failed: %s", reason_code)
        self._notify_connection()

    def _on_disconnect(self, client, userdata, *args) -> None:
        self.connected = False
        self.simulation = True
        logger.warning("MQTT disconnected - simulation mode active")
        self._notify_connection()

    def _on_message(self, client, userdata, msg) -> None:
        # ESP asking to (re)sync its clock: kapfela/instrument/<name>/time_request
        if msg.topic.endswith("/time_request"):
            parts = msg.topic.split("/")
            if len(parts) >= 4 and parts[1] == "instrument":
                try:
                    data = json.loads(msg.payload.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    data = {}
                self.publish_time(parts[2], data.get("t0_ms"))
            return
        # Pin/servo mapping published by an ESP: kapfela/instrument/<name>/mapping
        if msg.topic.endswith("/mapping"):
            parts = msg.topic.split("/")
            if len(parts) >= 4 and parts[1] == "instrument" and self.on_mapping:
                try:
                    data = json.loads(msg.payload.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return
                self.on_mapping(parts[2], data)
            return
        # Only react to status topics coming back from the ESP devices.
        if not msg.topic.endswith("/status"):
            return
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        parts = msg.topic.split("/")
        if len(parts) >= 3 and parts[1] == "instrument" and parts[2] in INSTRUMENT_NAMES:
            with self._ack_lock:
                self._last_device_status[parts[2]] = time.monotonic()
            if data.get("event") == "command_ack":
                command_id = data.get("command_id")
                if isinstance(command_id, str):
                    with self._ack_lock:
                        pending = self._pending_command_acks.get(command_id)
                        if pending is not None and parts[2] in pending["expected"]:
                            pending["acknowledged"][parts[2]] = bool(
                                data.get("success", False)
                            )
                            if pending["expected"] <= pending["acknowledged"].keys():
                                pending["event"].set()
            upload_id = data.get("upload_id")
            if isinstance(upload_id, str):
                with self._ack_lock:
                    events = self._upload_event_queues.get(upload_id)
                    if events is not None:
                        events.put({**data, "instrument": parts[2]})
        if self.on_status:
            self.on_status(msg.topic, data)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as song_file:
        for chunk in iter(lambda: song_file.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

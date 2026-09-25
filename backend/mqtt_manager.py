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
from pathlib import Path
from typing import Any, Callable

import paho.mqtt.client as mqtt

logger = logging.getLogger("kapfela.mqtt")

BROKER_HOST = os.environ.get("MQTT_HOST", "localhost")
BROKER_PORT = int(os.environ.get("MQTT_PORT", "1883"))

TOPIC_ROOT = "kapfela"
TOPIC_PLAYER = f"{TOPIC_ROOT}/player"
TOPIC_INSTRUMENT = f"{TOPIC_ROOT}/instrument"
TOPIC_SONG = f"{TOPIC_ROOT}/song"

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
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")

        song_path = Path(path)
        if not song_path.is_file():
            raise FileNotFoundError(song_path)

        size = song_path.stat().st_size
        total_chunks = (size + chunk_size - 1) // chunk_size
        topic = f"{TOPIC_SONG}/{instrument}/upload"
        self._publish(topic, {
            "command": "upload_start",
            "song_id": song_id,
            "size": size,
            "sha256": _file_sha256(song_path),
            "total_chunks": total_chunks,
            "chunk_size": chunk_size,
        })

        with song_path.open("rb") as song_file:
            for index in range(total_chunks):
                chunk = song_file.read(chunk_size)
                info = self._client.publish(topic, chunk, qos=1)
                info.wait_for_publish()
                logger.info("MQTT -> %s chunk %s/%s", topic, index + 1,
                            total_chunks)

        self._publish(topic, {
            "command": "upload_finish",
            "song_id": song_id,
            "total_chunks": total_chunks,
        })
        return True

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
        if self.on_status:
            self.on_status(msg.topic, data)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as song_file:
        for chunk in iter(lambda: song_file.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

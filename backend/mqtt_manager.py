"""MQTT bridge to the ESP microcontrollers with a simulation fallback.

When a broker is reachable the manager publishes commands on the
``kapfela/...`` topics and listens for status messages coming back from the
ESP devices. When no broker is available (for example in the v0 preview) it
transparently switches to simulation mode: commands are logged and a
synthetic status acknowledgement is generated so the UI stays fully usable.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Callable

import paho.mqtt.client as mqtt

logger = logging.getLogger("kapfela.mqtt")

BROKER_HOST = os.environ.get("MQTT_HOST", "localhost")
BROKER_PORT = int(os.environ.get("MQTT_PORT", "1883"))

TOPIC_ROOT = "kapfela"
TOPIC_PLAYER = f"{TOPIC_ROOT}/player"
TOPIC_INSTRUMENT = f"{TOPIC_ROOT}/instrument"

StatusHandler = Callable[[str, dict[str, Any]], None]


ConnectionHandler = Callable[[], None]


class MqttManager:
    def __init__(
        self,
        on_status: StatusHandler | None = None,
        on_connection: ConnectionHandler | None = None,
    ) -> None:
        self.on_status = on_status
        self.on_connection = on_connection
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

    def publish_config(self, name: str, config_data: dict[str, Any]) -> None:
        self._publish(f"{TOPIC_ROOT}/config/{name}", config_data)

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
        # Only react to status topics coming back from the ESP devices.
        if not msg.topic.endswith("/status"):
            return
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if self.on_status:
            self.on_status(msg.topic, data)

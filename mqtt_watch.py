#!/usr/bin/env python3
"""Simple MQTT monitor for KAP{F}ELA.

Run this from the Raspberry shell or from the web UI terminal to watch MQTT traffic.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

import paho.mqtt.client as mqtt


DEFAULT_HOST = "localhost"
DEFAULT_PORT = 1883
DEFAULT_TOPIC = "kapfela/#"


def format_payload(payload: bytes) -> str:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload.hex()

    try:
        parsed = json.loads(text)
        return json.dumps(parsed, indent=2, ensure_ascii=False)
    except ValueError:
        return text


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print(f"[connected] {userdata['host']}:{userdata['port']} -> {userdata['topic']}")
        client.subscribe(userdata["topic"], qos=1)
    else:
        print(f"[connect failed] reason={reason_code}")


def on_disconnect(client, userdata, rc):
    print(f"[disconnected] rc={rc}")


def on_message(client, userdata, msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = format_payload(msg.payload)
    print(f"\n[{ts}] {msg.topic}")
    print(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MQTT monitor for KAP{F}ELA")
    parser.add_argument("-H", "--host", default=DEFAULT_HOST, help="MQTT broker host")
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_PORT, help="MQTT broker port")
    parser.add_argument("-t", "--topic", default=DEFAULT_TOPIC, help="MQTT topic filter")
    parser.add_argument("--client-id", default="kapfela-mqtt-watch", help="MQTT client id")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    userdata = {"host": args.host, "port": args.port, "topic": args.topic}

    client = mqtt.Client(client_id=args.client_id)
    client.user_data_set(userdata)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    try:
        client.connect(args.host, args.port, keepalive=30)
    except Exception as exc:
        print(f"Failed to connect to MQTT broker {args.host}:{args.port}: {exc}")
        return 1

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\nStopped by user")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

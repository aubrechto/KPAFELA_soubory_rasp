#!/usr/bin/env python3
"""MQTT subscriber for the KAP{F}ELA web terminal.

Run this from the Raspberry shell or the web terminal to watch live MQTT traffic.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

import paho.mqtt.client as mqtt


def format_payload(payload: bytes) -> str:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload.hex()

    try:
        data = json.loads(text)
        return json.dumps(data, indent=2, ensure_ascii=False)
    except ValueError:
        return text


def on_connect(client, userdata, flags, reason_code, properties=None) -> None:
    if reason_code == 0:
        print(f"[connected] {userdata['host']}:{userdata['port']} subscribed to {userdata['topic']}", flush=True)
        client.subscribe(userdata["topic"], qos=1)
    else:
        print(f"[connect failed] reason={reason_code}", flush=True)


def on_disconnect(client, userdata, rc) -> None:
    print(f"[disconnected] rc={rc}", flush=True)


def on_message(client, userdata, msg) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    output = format_payload(msg.payload)
    # Print header and then payload line-by-line with immediate flush so the
    # web terminal UI can append and autoscroll for every printed line.
    print(f"\n[{ts}] {msg.topic}", flush=True)
    for line in str(output).splitlines():
        print(line, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MQTT subscriber for KAP{F}ELA")
    parser.add_argument("-H", "--host", default="localhost", help="MQTT broker host")
    parser.add_argument("-p", "--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("-t", "--topic", default="kapfela/#", help="MQTT topic filter")
    parser.add_argument("--client-id", default="kapfela-terminal-subscriber", help="MQTT client id")
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
        print(f"Failed to connect to MQTT broker {args.host}:{args.port}: {exc}", flush=True)
        return 1

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\nStopped by user", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

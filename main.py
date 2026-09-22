"""KAP{F}ELA robotic band controller - FastAPI application.

Serves the Spotify-inspired dashboard, exposes a REST + WebSocket API and
bridges commands to the ESP microcontrollers over MQTT (with a simulation
fallback when no broker is reachable).
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import pty
import secrets
import select
import shlex
import shutil
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import config
from backend.mqtt_manager import MqttManager
from backend.state import INSTRUMENTS, StateManager

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("kapfela")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
SONGS_DIR = BASE_DIR / "Data" / "songs"

state = StateManager()


class ConnectionManager:
    """Tracks connected dashboards and broadcasts JSON messages to them."""

    def __init__(self) -> None:
        self.active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in list(self.active):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


class ShellSession:
    def __init__(self, send: Callable[[str], None]) -> None:
        self._master_fd, self._slave_fd = pty.openpty()
        self._proc = subprocess.Popen(
            [os.environ.get("SHELL", "/bin/bash")],
            stdin=self._slave_fd,
            stdout=self._slave_fd,
            stderr=self._slave_fd,
            close_fds=True,
        )
        self._send = send
        self._alive = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        while self._alive:
            try:
                r, _, _ = select.select([self._master_fd], [], [], 0.1)
                if not r:
                    if self._proc.poll() is not None:
                        break
                    continue
                data = os.read(self._master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            self._send(data.decode("utf-8", errors="replace"))
        self._alive = False

    def write(self, data: str) -> None:
        if not self._alive:
            return
        os.write(self._master_fd, data.encode("utf-8", errors="replace"))

    def close(self) -> None:
        self._alive = False
        try:
            self._proc.terminate()
            self._proc.wait(timeout=1)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        for fd in (self._master_fd, self._slave_fd):
            try:
                os.close(fd)
            except OSError:
                pass


manager = ConnectionManager()

TERMINAL_TOKEN_COOKIE = "terminal_token"
TERMINAL_TOKEN_TTL = 3600
TERMINAL_COMMAND_HINTS = [
    "cd",
    "ls",
    "pwd",
    "cat",
    "tail",
    "less",
    "nano",
    "vim",
    "python",
    "python3",
    "sudo",
    "systemctl",
    "journalctl",
    "htop",
    "top",
    "git",
    "ping",
    "curl",
    "wget",
    "mkdir",
    "rm",
    "mv",
    "cp",
    "chmod",
    "chown",
    "ifconfig",
    "ip",
    "df",
    "du",
    "echo",
    "date",
    "whoami",
    "uname",
    "reboot",
    "shutdown",
]
terminal_sessions: dict[str, float] = {}

# Minimum clock drift (seconds) before the dashboard is allowed to set the
# system time, and the minimum interval between two accepted syncs.
TIME_SYNC_MIN_DELTA = 2.0
TIME_SYNC_MIN_INTERVAL = 300.0
_last_time_sync = 0.0


def _terminal_password() -> str:
    settings = config.load("settings")
    password = settings.get("terminal_password")
    if isinstance(password, str) and password.strip():
        return password
    return os.environ.get("TERMINAL_PASSWORD", "kapfela")


def _cleanup_terminal_sessions() -> None:
    now = time.time()
    expired = [token for token, expires in terminal_sessions.items() if expires < now]
    for token in expired:
        terminal_sessions.pop(token, None)


def _issue_terminal_token() -> str:
    _cleanup_terminal_sessions()
    token = secrets.token_urlsafe(32)
    terminal_sessions[token] = time.time() + TERMINAL_TOKEN_TTL
    return token


def _verify_terminal_token(token: str | None) -> bool:
    if not token:
        return False
    _cleanup_terminal_sessions()
    expires = terminal_sessions.get(token)
    return bool(expires and expires > time.time())


def _complete_terminal_suggestions(prefix: str) -> list[str]:
    if not prefix:
        return []
    if os.name == "posix" and shutil.which("bash"):
        try:
            quoted = shlex.quote(prefix)
            result = subprocess.run(
                ["bash", "-lc", f"compgen -A command -- {quoted}"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            suggestions = [line for line in result.stdout.splitlines() if line.startswith(prefix)]
            return sorted(set(suggestions))[:30]
        except Exception:
            pass
    return [cmd for cmd in TERMINAL_COMMAND_HINTS if cmd.startswith(prefix)]


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Merge source into target recursively (dicts only), in place."""
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value
    return target


def _handle_mqtt_mapping(name: str, data: dict[str, Any]) -> None:
    """Store a pin/servo mapping published by an ESP and notify dashboards."""
    if name not in INSTRUMENTS:
        return
    incoming = data.get(name, data)
    if not isinstance(incoming, dict):
        return
    instruments = config.load("instruments")
    _deep_merge(instruments.setdefault(name, {}), incoming)
    saved = config.save("instruments", instruments)
    logger.info("Mapping from ESP '%s' stored to instruments config", name)
    if _loop is not None:
        asyncio.run_coroutine_threadsafe(
            manager.broadcast(
                {"type": "instruments_config", "instruments_config": saved}
            ),
            _loop,
        )


def _handle_mqtt_status(topic: str, data: dict[str, Any]) -> None:
    """Apply an ESP status message coming back over MQTT to local state."""
    changed = False
    if topic.endswith("player/status"):
        status = data.get("status")
        if status in ("playing", "paused", "stopped"):
            state.status = status
            changed = True
    else:
        # kapfela/instrument/<name>/status
        parts = topic.split("/")
        if len(parts) >= 3:
            name = parts[2]
            if state.set_instrument(name, data.get("status", "")):
                changed = True
            for key in ("time", "timestamp", "ntp_time"):
                if key in data and state.set_instrument_time(name, data[key]):
                    changed = True
                if key in data:
                    break
    if changed and _loop is not None:
        asyncio.run_coroutine_threadsafe(
            manager.broadcast(state.snapshot()), _loop
        )


def _handle_mqtt_connection() -> None:
    if not mqtt.connected:
        # Bez brokeru se na ESP nedostane nic -> ukazat je jako vypnute.
        state.mark_instruments_off()
    if _loop is not None:
        snapshot = state.snapshot()
        snapshot["mqtt"] = mqtt.connection_info()
        asyncio.run_coroutine_threadsafe(
            manager.broadcast(snapshot),
            _loop,
        )
    if mqtt.connected:
        mqtt.publish_instruments_config(config.load("instruments"))


mqtt = MqttManager(
    on_status=_handle_mqtt_status,
    on_connection=_handle_mqtt_connection,
    on_mapping=_handle_mqtt_mapping,
)
_loop: asyncio.AbstractEventLoop | None = None


async def _ticker() -> None:
    """Advance simulated playback once per second and broadcast changes."""
    while True:
        await asyncio.sleep(1.0)
        if state.tick(1.0):
            await manager.broadcast(state.snapshot())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()
    mqtt.start()

    # Publish the current instruments config at startup once the broker is connected.
    if mqtt.connected:
        mqtt.publish_instruments_config(config.load("instruments"))
    else:
        for _ in range(60):
            await asyncio.sleep(0.5)
            if mqtt.connected:
                mqtt.publish_instruments_config(config.load("instruments"))
                break

    task = asyncio.create_task(_ticker())
    logger.info("KAP{F}ELA controller ready")
    yield
    task.cancel()
    mqtt.stop()


app = FastAPI(title="KAP{F}ELA Controller", lifespan=lifespan)


# --------------------------------------------------------------------- pages
@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# ------------------------------------------------------------------ REST API
@app.get("/api/state")
async def get_state() -> JSONResponse:
    snap = state.snapshot()
    snap["mqtt"] = mqtt.connection_info()
    return JSONResponse(snap)


@app.get("/api/playlist")
async def get_playlist() -> JSONResponse:
    return JSONResponse(config.load("playlist"))


@app.post("/api/songs/{song_id}/upload")
async def upload_song(song_id: str) -> JSONResponse:
    """Upload an existing converted song file to all three ESP devices."""
    song_path = SONGS_DIR / f"{song_id}.msg"
    if not song_path.is_file():
        return JSONResponse({"error": "converted song not found"}, status_code=404)

    results = {}
    for instrument in INSTRUMENTS:
        results[instrument] = await asyncio.to_thread(
            mqtt.publish_song, instrument, song_id, song_path
        )
    return JSONResponse({"song_id": song_id, "uploaded": results})


@app.post("/api/player/{command}")
async def player_command(command: str, body: dict[str, Any] | None = None) -> JSONResponse:
    body = body or {}
    if command == "play":
        state.play()
        mqtt.publish_player("play", {"song": state.current})
    elif command == "pause":
        state.pause()
        mqtt.publish_player("pause")
    elif command == "stop":
        state.stop()
        mqtt.publish_player("stop")
    elif command == "next":
        state.next()
        mqtt.publish_player("play", {"song": state.current})
    elif command == "prev":
        state.prev()
        mqtt.publish_player("play", {"song": state.current})
    elif command == "select":
        state.select(int(body.get("index", 0)))
        mqtt.publish_player("play", {"song": state.current})
    elif command == "seek":
        state.seek(float(body.get("position", 0)))
    elif command == "queue":
        if not state.add_to_queue(str(body.get("id", ""))):
            return JSONResponse({"error": "unknown song"}, status_code=404)
    elif command == "play-song":
        if not state.play_song(str(body.get("id", ""))):
            return JSONResponse({"error": "unknown song"}, status_code=404)
        mqtt.publish_player("play", {"song": state.current})
    else:
        return JSONResponse({"error": "unknown command"}, status_code=400)
    await manager.broadcast(state.snapshot())
    return JSONResponse(state.snapshot())


@app.post("/api/instrument/{name}/{command}")
async def instrument_command(name: str, command: str) -> JSONResponse:
    if name not in INSTRUMENTS:
        return JSONResponse({"error": "unknown instrument"}, status_code=404)
    mapping = {"play": "playing", "stop": "idle", "off": "off", "on": "idle",
               "reboot": "idle"}
    status = mapping.get(command)
    if status is None:
        return JSONResponse({"error": "unknown command"}, status_code=400)
    state.set_instrument(name, status)
    mqtt.publish_instrument(name, command)
    await manager.broadcast(state.snapshot())
    return JSONResponse(state.snapshot())


@app.get("/api/settings")
async def get_settings() -> JSONResponse:
    return JSONResponse(config.load("settings"))


@app.put("/api/settings")
async def put_settings(body: dict[str, Any]) -> JSONResponse:
    current = config.load("settings")
    current.update(body)
    saved = config.save("settings", current)
    mqtt.publish_config("settings", saved)
    return JSONResponse(saved)


@app.post("/api/time/sync")
async def time_sync(body: dict[str, Any]) -> JSONResponse:
    """Set the Pi's system clock from the dashboard browser.

    Used when the Pi has no internet connection: the notebook/tablet that
    opened the dashboard provides the correct time, which the Pi then
    serves to the ESP devices over NTP (chrony 'local stratum 10').
    """
    global _last_time_sync
    try:
        client_time = float(body.get("time", 0))
    except (TypeError, ValueError):
        return JSONResponse({"error": "invalid time"}, status_code=400)
    now = time.time()
    delta = client_time - now
    if abs(delta) < TIME_SYNC_MIN_DELTA:
        return JSONResponse({"synced": False, "reason": "in-sync", "delta": round(delta, 3)})
    if now - _last_time_sync < TIME_SYNC_MIN_INTERVAL:
        return JSONResponse({"synced": False, "reason": "throttled", "delta": round(delta, 3)})
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["sudo", "-n", "date", "-s", f"@{client_time:.3f}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("time sync failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
    if result.returncode != 0:
        logger.warning("time sync failed: %s", result.stderr.strip())
        return JSONResponse(
            {"error": result.stderr.strip() or "date command failed"},
            status_code=500,
        )
    _last_time_sync = time.time()
    logger.info("System time synced from dashboard (delta %.1f s)", delta)
    snap = state.snapshot()
    snap["mqtt"] = mqtt.connection_info()
    await manager.broadcast(snap)
    return JSONResponse({"synced": True, "delta": round(delta, 3)})


@app.post("/api/terminal/login")
async def terminal_login(body: dict[str, Any]) -> JSONResponse:
    password = str(body.get("password", ""))
    expected = _terminal_password()
    if not hmac.compare_digest(password, expected):
        return JSONResponse({"error": "invalid credentials"}, status_code=401)
    token = _issue_terminal_token()
    return JSONResponse({"token": token})


@app.get("/api/terminal/status")
async def terminal_status(request: Request) -> JSONResponse:
    token = request.cookies.get(TERMINAL_TOKEN_COOKIE)
    return JSONResponse({"authorized": _verify_terminal_token(token)})


@app.get("/api/instruments")
async def get_instruments() -> JSONResponse:
    return JSONResponse(config.load("instruments"))


@app.post("/api/instruments/test")
async def test_instruments() -> JSONResponse:
    mqtt.publish_all_instruments("test")
    return JSONResponse({"ok": True, "command": "test"})


@app.post("/api/terminal/complete")
async def terminal_complete(request: Request, body: dict[str, Any]) -> JSONResponse:
    token = request.cookies.get(TERMINAL_TOKEN_COOKIE)
    if not _verify_terminal_token(token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    prefix = str(body.get("prefix", ""))
    hints = _complete_terminal_suggestions(prefix)
    return JSONResponse({"hints": hints})


@app.put("/api/instruments")
async def put_instruments(body: dict[str, Any]) -> JSONResponse:
    saved = config.save("instruments", body)
    mqtt.publish_instruments_config(saved)
    return JSONResponse(saved)


# ----------------------------------------------------------------- websocket
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        snap = state.snapshot()
        snap["mqtt"] = mqtt.connection_info()
        await ws.send_json(snap)
        while True:
            # Keep the socket open; the client does not need to send data.
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:  # noqa: BLE001
        manager.disconnect(ws)


@app.websocket("/ws/terminal")
async def websocket_terminal(ws: WebSocket) -> None:
    token = ws.cookies.get(TERMINAL_TOKEN_COOKIE)
    if not _verify_terminal_token(token):
        await ws.close(code=1008, reason="Unauthorized")
        return

    await ws.accept()
    loop = asyncio.get_running_loop()

    def send_output(data: str) -> None:
        asyncio.run_coroutine_threadsafe(
            ws.send_json({"type": "terminal", "output": data}), loop
        )

    session = ShellSession(send_output)
    try:
        while True:
            text = await ws.receive_text()
            session.write(text)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        session.close()


# Static assets (js, css, images). Mounted last so it does not shadow routes.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

"""In-memory application state for the KAP{F}ELA controller.

Holds the current player state, the dynamic song queue, and the live
status of each instrument. All mutations return the fields that changed so
callers can broadcast minimal updates over the WebSocket.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from . import config

INSTRUMENTS = ("guitar", "bass", "drums")

# Instrument status values reported to / from the ESP devices.
PLAYING = "playing"
PAUSED = "paused"
IDLE = "idle"
OFF = "off"


class StateManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.status = "stopped"  # stopped | playing | paused
        self.position = 0.0  # seconds into the current song
        self.library: list[dict[str, Any]] = []
        self.queue: list[dict[str, Any]] = []
        self.index = 0
        self.instruments: dict[str, str] = {name: OFF for name in INSTRUMENTS}
        self.instrument_times: dict[str, Any] = {}
        self.instrument_time_received: dict[str, float] = {}
        self.reload_queue()

    # ------------------------------------------------------------------ queue
    def reload_queue(self) -> None:
        with self._lock:
            playlist = config.load("playlist")
            self.library = list(playlist.get("songs", []))
            self.queue = list(self.library)
            if self.index >= len(self.queue):
                self.index = 0
            self.position = 0.0

    def _find_in_library(self, song_id: str) -> dict[str, Any] | None:
        return next((s for s in self.library if s.get("id") == song_id), None)

    def add_to_queue(self, song_id: str) -> bool:
        """Append a library song to the end of the queue."""
        with self._lock:
            song = self._find_in_library(song_id)
            if not song:
                return False
            self.queue.append(dict(song))
            return True

    def play_song(self, song_id: str) -> bool:
        """Play a library song now: reuse its queue slot or append, then start."""
        with self._lock:
            song = self._find_in_library(song_id)
            if not song:
                return False
            idx = next(
                (i for i, s in enumerate(self.queue) if s.get("id") == song_id),
                None,
            )
            if idx is None:
                self.queue.append(dict(song))
                idx = len(self.queue) - 1
            self.index = idx
            self.position = 0.0
            self.status = "playing"
            self._sync_instruments()
            return True

    @property
    def current(self) -> dict[str, Any] | None:
        if not self.queue:
            return None
        return self.queue[self.index % len(self.queue)]

    # ---------------------------------------------------------------- controls
    def _sync_instruments(self) -> None:
        """Mirror the band's instruments onto the transport state.

        When the player is playing, every instrument that has not been
        explicitly switched OFF follows along and reports PLAYING. When the
        player is paused or stopped, those instruments fall back to IDLE.
        """
        playing = self.status == "playing"
        paused = self.status == "paused"
        for name in INSTRUMENTS:
            if self.instruments[name] == OFF:
                continue
            self.instruments[name] = (
                PLAYING if playing else (PAUSED if paused else IDLE)
            )

    def play(self) -> None:
        with self._lock:
            if self.queue:
                self.status = "playing"
                self._sync_instruments()

    def pause(self) -> None:
        with self._lock:
            if self.status == "playing":
                self.status = "paused"
                self._sync_instruments()

    def stop(self) -> None:
        with self._lock:
            self.status = "stopped"
            self.position = 0.0
            self._sync_instruments()

    def select(self, index: int) -> None:
        with self._lock:
            if self.queue:
                self.index = index % len(self.queue)
                self.position = 0.0
                self.status = "playing"
                self._sync_instruments()

    def next(self) -> None:
        with self._lock:
            if self.queue:
                self.index = (self.index + 1) % len(self.queue)
                self.position = 0.0
                self._sync_instruments()

    def prev(self) -> None:
        with self._lock:
            if self.queue:
                self.index = (self.index - 1) % len(self.queue)
                self.position = 0.0
                self._sync_instruments()

    def seek(self, position: float) -> None:
        with self._lock:
            cur = self.current
            if cur:
                self.position = max(0.0, min(position, float(cur["duration"])))

    # ------------------------------------------------------------ instruments
    def set_instrument(self, name: str, status: str) -> bool:
        with self._lock:
            if name in self.instruments and status in (PLAYING, PAUSED, IDLE, OFF):
                self.instruments[name] = status
                return True
            return False

    def mark_instruments_off(self) -> bool:
        """Force every instrument to OFF (e.g. when the MQTT broker drops)."""
        with self._lock:
            changed = any(status != OFF for status in self.instruments.values())
            for name in INSTRUMENTS:
                self.instruments[name] = OFF
            return changed

    def set_instrument_time(self, name: str, value: Any) -> bool:
        with self._lock:
            if name not in self.instruments or not isinstance(value, (str, int, float)):
                return False
            self.instrument_times[name] = value
            self.instrument_time_received[name] = time.time()
            return True

    # -------------------------------------------------------------- simulation
    def tick(self, dt: float) -> bool:
        """Advance playback by ``dt`` seconds. Returns True if state changed."""
        with self._lock:
            if self.status != "playing":
                return False
            cur = self.current
            if not cur:
                return False
            self.position += dt
            if self.position >= float(cur["duration"]):
                self.next()
            return True

    # ------------------------------------------------------------- serializing
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            cur = self.current
            now = time.time()
            # Numeric instrument times are extrapolated by the age of the
            # last MQTT status so the dashboard shows live clock values.
            effective_times: dict[str, Any] = {}
            for name, value in self.instrument_times.items():
                if isinstance(value, (int, float)):
                    received = self.instrument_time_received.get(name, now)
                    effective_times[name] = round(value + (now - received), 3)
                else:
                    effective_times[name] = value
            skew = {
                name: round(value - now, 3)
                for name, value in effective_times.items()
                if isinstance(value, (int, float))
            }
            max_skew = max((abs(v) for v in skew.values()), default=0.0)
            return {
                "type": "state",
                "server_time": now,
                "player": {
                    "status": self.status,
                    "position": round(self.position, 1),
                    "index": self.index,
                    "current": cur,
                },
                "queue": self.queue,
                "library": self.library,
                "instruments": dict(self.instruments),
                "instrument_times": effective_times,
                "time_sync": {
                    "skew": skew,
                    "max_skew": round(max_skew, 3),
                    "in_sync": bool(skew) and max_skew <= 0.5,
                },
            }

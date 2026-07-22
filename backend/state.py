"""In-memory application state for the KAP{F}ELA controller.

Holds the current player state, the dynamic song queue, and the live
status of each instrument. All mutations return the fields that changed so
callers can broadcast minimal updates over the WebSocket.
"""
from __future__ import annotations

import threading
from typing import Any

from . import config

INSTRUMENTS = ("guitar", "bass", "drums")

# Instrument status values reported to / from the ESP devices.
PLAYING = "playing"
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
        self.instruments: dict[str, str] = {name: IDLE for name in INSTRUMENTS}
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
        for name in INSTRUMENTS:
            if self.instruments[name] == OFF:
                continue
            self.instruments[name] = PLAYING if playing else IDLE

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
            if name in self.instruments and status in (PLAYING, IDLE, OFF):
                self.instruments[name] = status
                return True
            return False

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
            return {
                "type": "state",
                "player": {
                    "status": self.status,
                    "position": round(self.position, 1),
                    "index": self.index,
                    "current": cur,
                },
                "queue": self.queue,
                "library": self.library,
                "instruments": dict(self.instruments),
            }

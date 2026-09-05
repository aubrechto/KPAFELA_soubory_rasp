"""Build the Raspberry playlist from converted song metadata."""
from __future__ import annotations

import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
SONGS_DIR = BASE_DIR / "Data" / "songs"
PLAYLIST_PATH = BASE_DIR / "Data" / "playlist.json"


def load_song_entries() -> list[dict[str, object]]:
    entries = []
    for metadata_path in sorted(SONGS_DIR.glob("*.json")):
        messagepack_path = metadata_path.with_suffix(".msg")
        if not messagepack_path.exists():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        entries.append(
            {
                "id": metadata_path.stem,
                "file": messagepack_path.name,
                "title": metadata["title"],
                "artist": metadata["artist"],
                "duration": metadata["duration"],
                "tempo": metadata["tempo"],
                "measures": metadata["measures"],
                "instruments": metadata["instruments"],
            }
        )
    return entries


def main() -> None:
    playlist = {"songs": load_song_entries()}
    PLAYLIST_PATH.write_text(
        json.dumps(playlist, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(playlist['songs'])} songs to {PLAYLIST_PATH}")


if __name__ == "__main__":
    main()

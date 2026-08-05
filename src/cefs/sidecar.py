"""The per-roll ``roll.json`` written beside the scans.

Stock, developer, date: things the file cannot tell you, and which are gone six
months later unless written down while shooting. So it is rewritten after every
capture, which forces two rules.

Never lose a recorded frame -- each write re-reads and appends, so a restart or
a second session mid-roll cannot truncate the log. And never let it break a
capture: it is metadata about scans, and failures are logged, not raised.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SIDECAR_NAME = "roll.json"

#: Bumped only on an incompatible change; readers should tolerate unknown keys.
SCHEMA = 1


@dataclass
class RollMetadata:
    """What a person knows about a roll that the files cannot say."""

    roll: str = "Roll001"
    stock: str = ""
    developer: str = ""
    notes: str = ""
    #: Free text on purpose: "summer 2019" is an honest answer for a found roll.
    date: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def read(path: Path) -> dict:
    """Load a sidecar, or return an empty one. Never raises."""
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Could not read %s (%s); it will not be overwritten.", path, exc)
        # Not an empty dict: that reads as a fresh roll and the file gets
        # replaced, discarding a log a human might have salvaged by hand.
        return {"_unreadable": True}
    return loaded if isinstance(loaded, dict) else {"_unreadable": True}


def record_capture(
    path: Path,
    metadata: RollMetadata,
    frame: int,
    files: list[str],
    positives: list[str] | None = None,
    when: datetime | None = None,
) -> bool:
    """Append one frame, creating the sidecar if needed.

    ``metadata`` is rewritten every time, so a stock typed in halfway through
    the roll still lands. ``files`` are names relative to the sidecar's folder.
    Returns False if the existing file was left alone.
    """
    existing = read(path)
    if existing.get("_unreadable"):
        return False

    frames = existing.get("frames")
    if not isinstance(frames, list):
        frames = []

    entry = {
        "frame": frame,
        "captured": (when or datetime.now()).isoformat(timespec="seconds"),
        "files": list(files),
        "positives": list(positives or []),
    }
    # Re-shooting replaces the entry: you re-shot because the first was wrong.
    frames = [f for f in frames if not (isinstance(f, dict) and f.get("frame") == frame)]
    frames.append(entry)
    frames.sort(key=lambda f: f.get("frame", 0))

    document = {
        "schema": SCHEMA,
        **metadata.as_dict(),
        "updated": (when or datetime.now()).isoformat(timespec="seconds"),
        "frames": frames,
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write beside then replace, so a crash mid-write leaves the old file.
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        logger.warning("Could not write %s: %s", path, exc)
        return False
    return True

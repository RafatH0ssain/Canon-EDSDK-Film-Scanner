"""The per-roll ``roll.json`` written beside the scans.

What a negative is depends on things the file cannot tell you: the stock, how
it was developed, when it was shot. Six months later that context is gone
unless it was written down at the time, so it is written down at the time.

The sidecar lives in the same folder as the frames it describes, and is
rewritten after every capture. Three rules follow from it being written while
you are still shooting:

- **Never lose a frame already recorded.** Each write re-reads the file first
  and appends. A crash, a restarted server, a second session on the same roll:
  none of them may truncate the log.
- **A broken sidecar must not break the capture.** It is metadata about scans;
  the scans themselves are the thing that matters. Failures are logged and
  swallowed by the caller.
- **Never write it where the frames are not.** The path comes from the same
  render as the frames, so a roll spread over two folders gets two sidecars,
  each describing what is actually next to it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SIDECAR_NAME = "roll.json"

#: Bumped only if the shape changes incompatibly. Readers should tolerate
#: unknown keys rather than depend on this.
SCHEMA = 1


@dataclass
class RollMetadata:
    """What a person knows about a roll that the files cannot say."""

    roll: str = "Roll001"
    stock: str = ""
    developer: str = ""
    notes: str = ""
    #: ISO date the roll was shot or developed. Free text on purpose -- "1998"
    #: and "summer 2019" are honest answers for a found roll.
    date: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def sidecar_path(first_frame: Path) -> Path:
    """Where the sidecar for a frame at this path belongs: beside it."""
    return first_frame.parent / SIDECAR_NAME


def read(path: Path) -> dict:
    """Load a sidecar, or return an empty one. Never raises."""
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Could not read %s (%s); it will not be overwritten.", path, exc)
        # Signalling unreadable rather than empty matters: an empty dict would
        # be treated as a fresh roll and the file replaced, throwing away a
        # frame log that a human might still be able to salvage by hand.
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
    """Append one frame to the roll's sidecar, creating it if needed.

    Args:
        path: The sidecar itself, from :func:`sidecar_path`.
        metadata: Roll-level fields, refreshed on every write so a stock typed
            in halfway through the roll still lands.
        frame: Frame number.
        files: Names of the captures, relative to the sidecar's folder.
        positives: Names of any developed positives.
        when: Defaults to now.

    Returns:
        Whether it was written. False means the existing file was left alone.
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
    # Re-shooting a frame replaces its entry rather than adding a second one --
    # you re-shoot because the first attempt was wrong, and a log with both is
    # a log you have to interpret.
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
        # Write beside, then replace: a crash mid-write leaves the old sidecar
        # intact rather than a half-written one.
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        logger.warning("Could not write %s: %s", path, exc)
        return False
    return True

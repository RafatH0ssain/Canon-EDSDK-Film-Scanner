"""Structured names for captures: which roll, which frame, which folder.

A camera names everything ``IMG_0001`` and rolls the counter at 9999, which says
nothing about which roll a scan belongs to and eventually collides. A template
plus the roll and frame becomes a path relative to the output directory::

    {roll}/{roll}_Frame{frame:02d}   ->   Roll014/Roll014_Frame07.CR3

Fields: ``{roll}`` ``{frame}`` ``{original}`` ``{date}`` ``{time}`` ``{stock}``.

The roll name is repeated in the filename on purpose -- scans get dragged
around, and ``Roll014_Frame07.CR3`` survives being separated from its folder
where ``Frame07.CR3`` does not.

A frame is one *shutter release*, not one file: RAW+JPEG gives two files sharing
a frame number, differing by extension. Only :func:`unique` touches the disk.
"""

from __future__ import annotations

import re
import string
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

#: Anything else is an error, not an empty string: a dropped field would name
#: every frame of a roll the same thing.
FIELDS = ("roll", "frame", "original", "date", "time", "stock")

#: At least one must appear, or every frame renders to the same name --
#: ``unique`` would keep them as -1, -2 with no way to tell them apart.
VARYING_FIELDS = ("frame", "original", "time")

DEFAULT_TEMPLATE = "{roll}/{roll}_Frame{frame:02d}"

#: Illegal in a Windows filename. No forward slash: templates may contain
#: folder separators, though values substituted into them may not.
_ILLEGAL = r'<>:"\\|?*\x00-\x1f'

#: Windows refuses these whatever the extension, and obscurely.
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{n}" for n in range(1, 10)),
    *(f"LPT{n}" for n in range(1, 10)),
}

_FORMATTER = string.Formatter()


class NamingError(ValueError):
    """The template, or a value going into it, will not make a usable path."""


def sanitise(value: str, *, fallback: str = "") -> str:
    """Make free text safe as a path component -- ``../..`` and ``C:`` stop here."""
    cleaned = re.sub(f"[{_ILLEGAL}/]+", "-", str(value))
    # Trailing dots and spaces are creatable on Windows and then unopenable.
    cleaned = cleaned.strip().strip(".-_ ").strip()
    if cleaned.upper().split(".")[0] in _RESERVED:
        cleaned = f"{cleaned}-"
    return cleaned or fallback


def validate_template(template: str) -> None:
    """Check a template when it is saved, not when the shutter fires."""
    if not template or not template.strip():
        raise NamingError("The naming template cannot be empty.")
    # Not "':' in template" -- a colon also separates the format spec, so
    # {frame:02d}, the default, would fail. Only a drive letter matters.
    if PurePosixPath(template.replace("\\", "/")).is_absolute() or PureWindowsPath(template).drive:
        raise NamingError(
            "The naming template must be relative -- it is resolved inside the "
            "save location, so an absolute path would write outside it."
        )

    used = []
    try:
        for _, field, spec, _ in _FORMATTER.parse(template):
            if field is None:
                continue
            if "." in field or "[" in field:
                raise NamingError(
                    f"'{{{field}}}' is not a plain field name. Use one of: "
                    f"{', '.join(FIELDS)}."
                )
            if field not in FIELDS:
                raise NamingError(
                    f"Unknown field '{{{field}}}' in the naming template. "
                    f"Valid fields: {', '.join(FIELDS)}."
                )
            used.append(field)
            if spec:
                # A typo in "{frame:02d}" should not wait for a capture.
                try:
                    format(1 if field == "frame" else "x", spec)
                except ValueError as exc:
                    raise NamingError(f"Bad format '{{{field}:{spec}}}': {exc}") from exc
    except ValueError as exc:
        if isinstance(exc, NamingError):
            raise
        raise NamingError(f"Could not read the naming template: {exc}") from exc

    if ".." in PurePosixPath(template.replace("\\", "/")).parts:
        raise NamingError("The naming template must not contain '..'.")
    if not set(used) & set(VARYING_FIELDS):
        raise NamingError(
            "The naming template must include at least one of "
            f"{', '.join('{' + f + '}' for f in VARYING_FIELDS)}, or every frame "
            "of a roll renders to the same name."
        )


def render(
    template: str,
    *,
    roll: str,
    frame: int,
    extension: str,
    original: str = "",
    stock: str = "",
    when: datetime | None = None,
) -> PurePosixPath:
    """Render one capture's path, relative to the save location.

    ``extension`` includes the dot and comes from what the camera wrote.
    """
    validate_template(template)
    when = when or datetime.now()
    values = {
        "roll": sanitise(roll, fallback="Roll"),
        "frame": int(frame),
        "original": sanitise(original, fallback="capture"),
        "date": when.strftime("%Y-%m-%d"),
        "time": when.strftime("%H%M%S"),
        "stock": sanitise(stock),
    }
    try:
        rendered = template.format_map(values)
    except (KeyError, IndexError, ValueError) as exc:  # pragma: no cover - validated above
        raise NamingError(f"Could not render the naming template: {exc}") from exc

    parts = [p for p in PurePosixPath(rendered.replace("\\", "/")).parts if p not in ("", ".")]
    if not parts:
        raise NamingError(f"The naming template rendered to nothing: {template!r}")
    # Sanitised again per component: {stock} is empty until recorded, which can
    # leave a component that is only separators.
    parts = [sanitise(p) for p in parts]
    if not all(parts):
        raise NamingError(
            f"The naming template rendered an empty folder or filename: {rendered!r}. "
            f"A field it uses is probably blank."
        )
    # Concatenated, not with_suffix(): a roll labelled "R2.5" renders
    # "R2.5_Frame07", and with_suffix would read "_Frame07" as the extension and
    # replace it, turning every frame into "R2.CR3". Case is the camera's.
    parts[-1] = parts[-1] + extension
    return PurePosixPath(*parts)


def unique(path: Path) -> Path:
    """A path that does not exist yet, suffixing -1, -2. A capture cannot be
    regenerated, so nothing here overwrites."""
    if not path.exists():
        return path
    for n in range(1, 10000):
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise NamingError(f"Could not find a free filename beside {path}")


def example(template: str, *, roll: str = "Roll014", frame: int = 7, stock: str = "") -> str:
    """What ``template`` names frame 7 of Roll014 -- shown in the UI, so a typo
    is noticed before the roll rather than after it."""
    return str(
        render(
            template,
            roll=roll,
            frame=frame,
            extension=".CR3",
            original="IMG_0001",
            stock=stock,
        )
    )

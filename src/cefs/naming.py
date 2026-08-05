"""Structured names for captures: which roll, which frame, which folder.

A camera names files ``IMG_0001.CR3`` and rolls the counter over at 9999, which
tells you nothing about which roll a scan belongs to and eventually collides.
This module turns a template plus the current roll and frame into a path
relative to the output directory::

    {roll}/{roll}_Frame{frame:02d}   ->   Roll014/Roll014_Frame07.CR3

The roll name appears in the filename as well as the folder on purpose. Scans
get imported, exported and dragged around, and a file called
``Roll014_Frame07.CR3`` survives being separated from its folder where
``Frame07.CR3`` does not.

Everything here is a pure function over strings. Nothing touches the camera, and
only :func:`unique` looks at the filesystem.

FIELDS
------
=============  =============================================================
``{roll}``     The roll label, e.g. ``Roll014``.
``{frame}``    Frame number, an int -- ``{frame:02d}`` pads it.
``{original}`` The camera's own stem, e.g. ``IMG_0001``.
``{date}``     Capture date, ``YYYY-MM-DD``.
``{time}``     Capture time, ``HHMMSS``.
``{stock}``    Film stock, if you have recorded one. Empty otherwise.
=============  =============================================================

A frame is one *shutter release*, not one file. A body set to RAW+JPEG sends
two files for one release and both take the same frame number, distinguished by
their extension -- which is what you want: they are two renderings of one
photograph, not two photographs.
"""

from __future__ import annotations

import re
import string
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

#: Template fields, and how each is filled. Anything else is an error rather
#: than an empty string: a silently dropped field would quietly name every
#: frame of a roll the same thing.
FIELDS = ("roll", "frame", "original", "date", "time", "stock")

#: At least one of these must appear, or every capture in a roll renders to the
#: same name. :func:`unique` would still refuse to overwrite, but you would get
#: ``Roll014-1``, ``Roll014-2`` and no idea which frame was which.
VARYING_FIELDS = ("frame", "original", "time")

DEFAULT_TEMPLATE = "{roll}/{roll}_Frame{frame:02d}"

#: Illegal in a Windows filename, and worth avoiding everywhere else. The
#: forward slash is absent deliberately: it is the folder separator, and a
#: template is allowed to contain those. Values substituted *into* the template
#: have it stripped -- see :func:`sanitise`.
_ILLEGAL = r'<>:"\\|?*\x00-\x1f'

#: Windows refuses these as filenames whatever the extension, and the failure
#: when it does is obscure.
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{n}" for n in range(1, 10)),
    *(f"LPT{n}" for n in range(1, 10)),
}

_FORMATTER = string.Formatter()


class NamingError(ValueError):
    """The template, or a value going into it, will not make a usable path."""


def sanitise(value: str, *, fallback: str = "") -> str:
    """Make a free-text value safe to put in a filename.

    Roll labels and film stocks are typed by a person into a web form and then
    become path components, so ``../..`` and ``C:`` have to stop here.
    """
    cleaned = re.sub(f"[{_ILLEGAL}/]+", "-", str(value))
    # Trailing dots and spaces are legal to create on Windows and then
    # impossible to open or delete.
    cleaned = cleaned.strip().strip(".-_ ").strip()
    if cleaned.upper().split(".")[0] in _RESERVED:
        cleaned = f"{cleaned}-"
    return cleaned or fallback


def validate_template(template: str) -> None:
    """Check a template before it is saved, not when the shutter fires.

    Raises:
        NamingError: With a message naming the problem and the valid fields.
    """
    if not template or not template.strip():
        raise NamingError("The naming template cannot be empty.")
    # Not "':' in template": a colon is also the format-spec separator, and
    # {frame:02d} -- the default -- would fail. Only a drive letter matters,
    # and PureWindowsPath knows what one of those looks like.
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
                # "{frame:02d}" is the common case and a typo in it should not
                # wait until a capture to surface.
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

    Args:
        template: See the module docstring.
        roll: Roll label. Sanitised before use.
        frame: Frame number -- one per shutter release.
        extension: Including the dot, taken from what the camera wrote.
        original: The camera's own stem, for ``{original}``.
        stock: Film stock, for ``{stock}``.
        when: Capture time, for ``{date}`` and ``{time}``. Defaults to now.

    Raises:
        NamingError: If the template is invalid, or renders to nothing.
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
    # Each component is sanitised again: a field could have been empty, leaving
    # a component that is only separators, and {stock} is empty until recorded.
    parts = [sanitise(p) for p in parts]
    if not all(parts):
        raise NamingError(
            f"The naming template rendered an empty folder or filename: {rendered!r}. "
            f"A field it uses is probably blank."
        )
    # Concatenated, not with_suffix(). A rendered name may legitimately contain
    # a dot -- a roll labelled "R2.5" gives "R2.5_Frame07" -- and with_suffix
    # would treat "_Frame07" as the extension and replace it, quietly turning
    # every frame of that roll into "R2.CR3". The extension keeps the camera's
    # own case: the bytes are exactly what the camera wrote, so the name should
    # say so too.
    parts[-1] = parts[-1] + extension
    return PurePosixPath(*parts)


def unique(path: Path) -> Path:
    """A path that does not exist yet.

    A capture is the one artefact that cannot be regenerated, so nothing here
    ever overwrites. Suffixes ``-1``, ``-2`` and so on.
    """
    if not path.exists():
        return path
    for n in range(1, 10000):
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise NamingError(f"Could not find a free filename beside {path}")


def example(template: str, *, roll: str = "Roll014", frame: int = 7, stock: str = "") -> str:
    """What ``template`` would name frame 7 of Roll014, for the UI to show.

    Seeing the answer beats reading the field list, and it turns a template
    typo into something you notice before the roll rather than after it.
    """
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

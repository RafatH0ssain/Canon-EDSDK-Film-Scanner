"""The local web server.

Serves a processed MJPEG live-view stream plus a small HTML/JS front end. Bound
to loopback by default: this server can fire your shutter, so it has no business
being reachable from a network you do not control.

Run it with::

    python -m cefs.app.server

No SDK and no camera are needed while ``camera.use_mock`` is true, which is the
default in a fresh clone.
"""

from __future__ import annotations

import argparse
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cefs.app.session import Session
from cefs.backend import CameraError
from cefs.config import Config, load_config

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
_BOUNDARY = "cefsframe"


class ViewUpdate(BaseModel):
    invert: bool | None = None
    loupe: bool | None = None
    zoom: float | None = None
    center_x: float | None = None
    center_y: float | None = None
    peaking: bool | None = None
    peaking_sensitivity: float | None = None
    inversion: str | None = None


class FocusRequest(BaseModel):
    direction: str
    coarseness: str = "medium"


class FilmUpdate(BaseModel):
    mode: str | None = None
    exposure: float | None = None
    contrast: float | None = None
    black_point: float | None = None
    white_point: float | None = None
    auto_balance: bool | None = None
    channel_gain: list[float] | None = None


class CaptureSettings(BaseModel):
    """Capture settings the UI can change without editing ``config.yaml``.

    Every field optional: the UI sends only the control that moved.
    """

    output_dir: str | None = None
    settle_delay_s: float | None = None
    develop_positives: bool | None = None
    positive_format: str | None = None
    tiff_compression: str | None = None
    jpeg_quality: int | None = None


class RollUpdate(BaseModel):
    """The roll being scanned, and what is recorded about it."""

    roll: str | None = None
    frame: int | None = None
    template: str | None = None
    stock: str | None = None
    developer: str | None = None
    notes: str | None = None
    date: str | None = None
    sidecar: bool | None = None


class NextRoll(BaseModel):
    """Start a new roll. Omit the label to increment the current one."""

    roll: str | None = None


class BaseSample(BaseModel):
    """Region to sample the film base from, normalised 0-1.

    Omit it to fall back to the automatic estimate.
    """

    region: list[float] | None = None


def create_app(config: Config | None = None) -> FastAPI:
    """Build the application. Taking config as an argument keeps it testable."""
    config = config or load_config()
    session = Session(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        # Always hand live view back to the camera and close the session, or
        # the body is left with a blank screen and a session another program
        # cannot open.
        session.disconnect()

    app = FastAPI(title="Canon EDSDK Film Scanner", version="0.1.0", lifespan=lifespan)
    app.state.session = session
    app.state.config = config

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/status")
    def status() -> dict:
        return session.status()

    @app.post("/api/connect")
    def connect() -> dict:
        try:
            return session.connect()
        except (CameraError, RuntimeError) as exc:
            # 503, not 500: the app is fine, the camera is not there yet.
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/disconnect")
    def disconnect() -> dict:
        return session.disconnect()

    @app.post("/api/view")
    def view(update: ViewUpdate) -> dict:
        try:
            return session.update_view(**update.model_dump(exclude_none=True))
        except ValueError as exc:
            # A bad value is the caller's fault, not the server's; a 500 here
            # would send someone hunting through server logs for their typo.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/frame")
    def frame():
        """A single processed frame. Useful for tests and for debugging."""
        backend_frame = session._backend.latest_frame() if session.connected else None
        if backend_frame is None:
            raise HTTPException(status_code=503, detail="No frame available yet.")
        return StreamingResponse(iter([session.process(backend_frame)]), media_type="image/jpeg")

    @app.get("/api/stream")
    def stream():
        if not session.connected:
            raise HTTPException(status_code=503, detail="Not connected.")
        return StreamingResponse(
            session.mjpeg(_BOUNDARY),
            media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY}",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/focus")
    def focus(request: FocusRequest) -> dict:
        if request.direction not in ("near", "far"):
            raise HTTPException(
                status_code=422, detail="direction must be 'near' or 'far'."
            )
        try:
            return session.drive_focus(request.direction, request.coarseness)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except CameraError as exc:
            # 409: the camera is fine, this lens just cannot be driven.
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/film")
    def film(update: FilmUpdate) -> dict:
        try:
            return session.update_film(**update.model_dump(exclude_none=True))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/film/base")
    def film_base(sample: BaseSample) -> dict:
        region = sample.region
        if region is not None and len(region) != 4:
            raise HTTPException(
                status_code=422, detail="region must be [x, y, w, h] in 0-1 coordinates."
            )
        try:
            return session.remeasure_film_base(region)
        except CameraError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/sharpness")
    def sharpness() -> dict:
        try:
            return session.measure_sharpness()
        except CameraError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/sharpness/reset")
    def sharpness_reset() -> dict:
        return session.reset_sharpness_best()

    @app.get("/api/roll")
    def roll() -> dict:
        return session.roll_status()

    @app.post("/api/roll")
    def set_roll(update: RollUpdate) -> dict:
        try:
            return session.update_roll(**update.model_dump(exclude_none=True))
        except ValueError as exc:
            # NamingError is a ValueError, so a bad template lands here too.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/roll/next")
    def roll_next(request: NextRoll) -> dict:
        try:
            return session.next_roll(request.roll)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/capture/settings")
    def capture_settings() -> dict:
        return session.capture_status()

    @app.post("/api/capture/settings")
    def set_capture_settings(settings: CaptureSettings) -> dict:
        try:
            return session.update_capture(**settings.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/capture")
    def capture() -> JSONResponse:
        try:
            return JSONResponse(session.capture())
        except CameraError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument(
        "--real",
        action="store_true",
        help="Use the real camera over EDSDK instead of the mock.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    config = load_config()
    if args.real:
        config.camera.use_mock = False

    host = args.host or config.server.host
    port = args.port or config.server.port

    import uvicorn

    backend = "mock camera" if config.camera.use_mock else "real camera over EDSDK"
    print(f"Canon EDSDK Film Scanner -- {backend}")
    print(f"Open http://{host}:{port}/ in a browser.\n")
    uvicorn.run(create_app(config), host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

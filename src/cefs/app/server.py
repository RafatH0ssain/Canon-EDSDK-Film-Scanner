"""The local web server: a processed MJPEG stream plus a small HTML/JS UI.

Loopback by default -- it can fire your shutter. Run with
``python -m cefs.app.server``; no SDK or camera is needed while
``camera.use_mock`` is true, the default in a fresh clone.
"""

from __future__ import annotations

import argparse
import logging
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cefs import __version__
from cefs.app.session import Session
from cefs.backend import CameraError
from cefs.config import Config, load_config
from cefs.paths import example_config_path, is_frozen, static_dir, user_data_dir

logger = logging.getLogger(__name__)

STATIC_DIR = static_dir()
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
    """Capture settings the UI can change. The UI sends only what moved."""

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
    """Region to sample the base from, normalised 0-1. Omit for automatic."""

    region: list[float] | None = None


def create_app(config: Config | None = None) -> FastAPI:
    """Build the app. Config as an argument keeps it testable."""
    config = config or load_config()
    session = Session(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        # Hand live view back, or the body is left with a blank screen and a
        # session another program cannot open.
        session.disconnect()

    app = FastAPI(title="Canon EDSDK Film Scanner", version=__version__, lifespan=lifespan)
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
            # The caller's typo, not a server fault: 500 would send them
            # hunting through logs for it.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/frame")
    def frame():
        """A single processed frame, for tests and debugging."""
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


def prepare_user_folder() -> Path | None:
    """Create the user's folder and seed a config, on first launch of a bundle.

    A packaged app has no repository to read a config out of and no console to
    explain itself in. Both are solved by giving the person a real folder with
    a real ``config.yaml`` in it, which is also where they will have to go to
    point the app at their own copy of EDSDK.

    Returns the config path, or None when running from a checkout, where the
    repository already is that folder.
    """
    if not is_frozen():
        return None

    folder = user_data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "captures").mkdir(exist_ok=True)

    destination = folder / "config.yaml"
    if not destination.is_file():
        example = example_config_path()
        if example.is_file():
            # Copied, not written from defaults: the example carries the
            # comments explaining every setting, which is the only
            # documentation a double-click user is going to meet.
            destination.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            logger.info("Created %s", destination)
    return destination


def open_browser_when_ready(url: str, server: Any) -> None:
    """Open the UI once the server is actually accepting connections.

    Opening immediately races the bind and shows a connection error, which for
    a double-clicked app looks exactly like the app being broken.
    """

    def wait_then_open() -> None:
        deadline = time.perf_counter() + 20.0
        while time.perf_counter() < deadline:
            if getattr(server, "started", False):
                webbrowser.open(url)
                return
            time.sleep(0.1)
        logger.warning("Server did not start within 20 s; not opening a browser.")

    threading.Thread(target=wait_then_open, name="cefs-browser", daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument(
        "--real",
        action="store_true",
        help="Use the real camera over EDSDK instead of the mock.",
    )
    parser.add_argument(
        "--browser",
        dest="browser",
        action="store_true",
        default=None,
        help="Open the UI in a browser once the server is up (default when packaged).",
    )
    parser.add_argument(
        "--no-browser", dest="browser", action="store_false", help="Never open a browser."
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    prepare_user_folder()
    config = load_config()
    if args.real:
        config.camera.use_mock = False

    host = args.host or config.server.host
    port = args.port or config.server.port

    import uvicorn

    backend = "mock camera" if config.camera.use_mock else "real camera over EDSDK"
    url = f"http://{host}:{port}/"
    print(f"Canon EDSDK Film Scanner -- {backend}")
    print(f"Open {url} in a browser.")
    if is_frozen():
        print(f"Settings and captures: {user_data_dir()}")
    print()

    server = uvicorn.Server(
        uvicorn.Config(create_app(config), host=host, port=port, log_level="warning")
    )

    # Packaged, the browser is the only UI there is, so open it unless told
    # not to. From a checkout it stays off: restarting the server is something
    # you do constantly, and a new tab each time is a nuisance.
    if args.browser or (args.browser is None and is_frozen()):
        open_browser_when_ready(url, server)

    # On macOS the SDK only finds cameras from the main thread, so the web
    # server has to give it up. Everywhere else, and with the mock, the main
    # thread has no special job and uvicorn keeps it.
    from cefs.edsdk.mainthread import EXECUTOR, REQUIRES_MAIN_THREAD

    if not (REQUIRES_MAIN_THREAD and not config.camera.use_mock):
        server.run()
        return 0

    web = threading.Thread(target=server.run, name="cefs-web", daemon=True)
    web.start()
    try:
        EXECUTOR.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        EXECUTOR.stop()
        server.should_exit = True
        web.join(timeout=10.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

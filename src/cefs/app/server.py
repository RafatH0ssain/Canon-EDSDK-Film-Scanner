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
        return session.update_view(**update.model_dump(exclude_none=True))

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

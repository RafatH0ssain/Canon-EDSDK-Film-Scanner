"""Entry point for the packaged app.

Thin on purpose. It exists because a bundle needs a single script to start
from, and because **it must be `cefs.app.server.main`** that runs: on macOS
that function is what reserves the main thread for EDSDK, and a bundler
pointed at anything else would reintroduce a camera that is never found.
"""

from __future__ import annotations

import multiprocessing
import sys


def run() -> int:
    # Without this a frozen app on Windows re-launches itself for every
    # subprocess, which presents as the app opening over and over.
    multiprocessing.freeze_support()

    from cefs.app.server import main

    return main()


if __name__ == "__main__":
    sys.exit(run())

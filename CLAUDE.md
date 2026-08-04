# CLAUDE.md — Instructions for Claude Code

This file tells Claude Code how to build **Canon EDSDK Film Scanner** from an empty folder. Read it fully before writing any code. It assumes a capable programmer whose C is rusty, and that the project is public on GitHub for others in the same situation.

Read `ROADMAP.md` for the full feature plan and milestone order. Build in milestone order; do not jump ahead.

---

## 1. What we're building (context)

A desktop tool that remote-controls a Canon camera over **EDSDK** (Canon's EOS Digital SDK — a native C SDK speaking PTP over USB) to digitize film negatives on a copy stand. Core value: focus and shoot from a big monitor with the camera untouched, with a **live positive preview** and **low-latency live view**.

There is a working sibling project, **Canon Smart Film Scan**, doing the same job over CCAPI/Wi-Fi. It lives at `D:\projects\Canon Smart Film Scan` on this machine. This project exists solely because wireless live view is too laggy — measured at 3.98 fps / 251 ms at 960×640. Roughly half of the sibling's code is transport-agnostic and should be **ported, not reinvented** (see `ROADMAP.md` §7).

**This project never modifies camera firmware.** It only calls documented SDK functions. Do not attempt or suggest firmware-level changes.

## 2. Critical constraints — read before anything else

1. **Never commit Canon's EDSDK.** Not the headers, not the DLLs/dylibs/shared objects, not the reference manual, not the sample code. Canon licenses it per developer and forbids redistribution. The human keeps their own copy locally in a git-ignored path. When you need a function signature or constant, **ask the human to point you at their local SDK headers, then build against them.** Do not guess signatures from memory — a wrong `ctypes` signature corrupts the stack and crashes in ways that look nothing like the actual mistake.
2. **Never quote Canon's reference text** into source comments, docs, issues, or PRs. Function names and constant names that appear in your code are fine; Canon's prose is not.
3. **Never commit secrets or personal data:** camera serial numbers, the user's scans, or paths that leak SDK contents.
4. **Respect the human's hardware.** Nothing here can brick a camera, but use the **electronic shutter** and sensible settle delays, and never fire the shutter without saying so first. Before running anything against the real camera — as opposed to the mock — state plainly what it will do.
5. **Camera-agnostic, not R7-only.** The R7 is the reference body; target the SDK and detect capabilities at runtime.
6. **Both film types are first-class.** Never implement inversion in a way that assumes black-and-white.

## 3. Hard-won lessons from the sibling project

These cost real time on the CCAPI project. They transfer directly.

- **A mock encodes its author's assumptions.** Every significant bug in the sibling project passed a green test suite first, because the mock was built from the same misunderstanding as the code. A mock proves internal consistency, never correctness. **Get onto real hardware early and often**, especially for anything performance- or protocol-shaped.
- **Never invent an API's shape.** The sibling project invented a `{"direction": "near", "steps": 3}` payload for focus, encoded that invention in its mock, and passed all its tests. The real API took `{"value": "near1"}`. Read the headers.
- **Measure before committing to an approach.** The sibling project used variance of the Laplacian for its sharpness metric — the textbook choice — and it scored well on synthetic fixtures. On real frames it separated tack-sharp from complete mush by only 1.22×. A different metric gave 5.66×. Validate metrics and performance claims on real data.
- **Quarantine assumptions in one file.** In the sibling project every API path assumption lives in a single module, so correcting a wrong one touches one file. Do the same here for `ctypes` signatures and SDK constants: they go in `edsdk/bindings.py` and nowhere else.
- **Restart the server after code changes and hard-refresh the browser.** Two "bugs" in the sibling project were a stale server process plus a cached `app.js`. Static files reload from disk; Python modules do not.
- **Prefer an honest failure to a silent wrong answer.** A capability reported as missing is diagnosable; one silently bound to the wrong thing is not.

## 4. Recommended tech stack

Confirm with the human before diverging, but default to:

- **Language:** Python 3.10+. Use **3.13 or lower** until `opencv-python` ships wheels for newer versions — on 3.14 pip tries to build OpenCV from source, which fails messily on Windows.
- **SDK access:** `ctypes` against the EDSDK shared library. No third-party wrapper: the available Python wrappers are thin, largely unmaintained, and the most prominent one does not document live-view support at all. We need live view above everything, so bind it ourselves.
- **Image processing:** `opencv-python` (`cv2`) + `numpy`.
- **UI:** a **local web app** — `FastAPI` serving a processed MJPEG live-view stream plus a minimal HTML/JS/CSS frontend opened fullscreen in a browser. Port this from the sibling project.
- **Config:** a git-ignored `config.yaml` from a committed `config.example.yaml`, with env-var overrides. It must hold the path to the user's EDSDK.
- **Testing:** `pytest`.
- **Mock backend:** a fake camera implementing the same interface with no SDK and no hardware, so contributors can develop without either. Build it early — but re-read §3 about what a mock can and cannot prove.

Keep dependencies minimal so newcomers can install easily.

## 5. Target project structure

Create this incrementally as milestones require it. Do not scaffold empty files for features not yet being built.

```
Canon EDSDK Film Scanner/
├── README.md                 # user-facing intro + setup (keep updated)
├── ROADMAP.md                # feature plan and milestone order
├── CLAUDE.md                 # this file
├── LICENSE                   # MIT or Apache-2.0 (ask the human to confirm)
├── .gitignore                # see section 7 — write this BEFORE the first commit
├── requirements.txt
├── config.example.yaml       # committed template, no real paths
├── src/
│   └── cefs/                 # "Canon EDSDK Film Scanner"
│       ├── __init__.py
│       ├── config.py
│       ├── edsdk/            # native binding + camera thread. No UI, no images.
│       │   ├── bindings.py       # THE ONLY place ctypes signatures/constants live
│       │   ├── camera.py         # camera thread: STA, message pump, command queue
│       │   ├── liveview.py       # frame pump
│       │   ├── properties.py     # typed get/set over EdsGetPropertyData etc.
│       │   └── errors.py         # EDS error codes -> actionable messages
│       ├── processing/       # pure functions over numpy arrays
│       │   ├── invert.py
│       │   ├── peaking.py
│       │   ├── sharpness.py
│       │   ├── loupe.py
│       │   └── codec.py
│       ├── app/
│       │   ├── server.py
│       │   ├── session.py
│       │   └── static/
│       ├── mock/             # fake backend, no SDK required
│       └── tools/            # diagnostics: latency probe, capability dump
└── tests/
```

## 6. From-zero setup sequence

**Step A — Canon-side prerequisites (human does these; you guide).**
1. Register with Canon's Developer Community for their region and request **EDSDK** access.
2. **Confirm the camera model is on EDSDK's supported list** before going further. Canon does not publish this openly.
3. Download and unpack the SDK, keeping it outside the repo or in the git-ignored `edsdk_sdk/`.
4. Note the architecture: use the **64-bit** libraries with 64-bit Python. Mixing bitness fails at load time with an unhelpful error.

**Step B — Dev environment (you can do most of this).**
1. Confirm Python 3.10–3.13 is available; create a venv.
2. `requirements.txt` with the stack above; install.
3. `git init`, then **`.gitignore` and `config.example.yaml` before any first commit**, so SDK binaries can never enter history.
4. Choose and add the LICENSE (confirm MIT vs Apache-2.0 with the human).

**Step C — v0.0, the spike. This decides whether the project continues.**

Read `ROADMAP.md` §8 v0.0 for the kill criterion. In short: load the SDK, initialise it on an STA thread with a message pump, open a session, pull live-view frames, and **measure fps, latency and resolution**. If it does not clearly beat the CCAPI baseline of 3.98 fps / 251 ms, say so plainly and recommend stopping. Write the numbers down either way.

Treat this as throwaway code. No architecture, no UI, no test suite — just the measurement. Building the app first and measuring afterwards would be exactly the mistake §3 warns about.

**Step D — Build the mock backend, then v0.1 onward per the roadmap.**

## 7. .gitignore essentials

Write this before the first commit. At minimum:

```
# Canon EDSDK -- NEVER redistribute
edsdk_sdk/
EDSDK/
*.dll
*.dylib
*.so
EDSDK*.h
EdsStrings*
*EDSDK*Reference*

# secrets & local config
config.yaml
*.local.*
.env

# user captures / scans
captures/
scans/
*.CR2
*.CR3
*.cr2
*.cr3
*.tif
*.tiff
*.jpg
*.jpeg
*.png
!docs/**/*.png
!tests/fixtures/**/*.png

# python
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
dist/
build/
*.egg-info/

# editors / OS
.vscode/
.idea/
.DS_Store
Thumbs.db
```

The SDK, secrets and captures rules are non-negotiable. Note `*.dll` is deliberately broad: it is better to force an explicit allow-list later than to let a Canon binary slip in.

## 8. Coding conventions

- **Layer discipline.** `edsdk/` knows nothing about images or UI. `processing/` is pure functions over numpy arrays with no SDK and no network. `app/` wires them. This is what lets colour inversion be developed with no hardware.
- **One thread owns the SDK.** All EDSDK calls happen on the camera thread. Everything else submits commands to a queue and waits on a result. Do not call into the SDK from a request handler.
- **All `ctypes` signatures in `bindings.py`.** Declare `argtypes` and `restype` for every function you bind — omitting them is the single most common cause of crashes that appear to come from somewhere else entirely.
- **Every SDK call's return code is checked.** EDSDK reports failure by return value, not exceptions. An unchecked code becomes a mystery later.
- **Release what you retain.** EDSDK is reference-counted. Every `EdsCreate*`/`EdsGetChild*` needs its `EdsRelease`. Prefer context managers so leaks are hard to write.
- **Capability detection over model checks.** Ask the camera what it supports; degrade gracefully.
- **Non-destructive.** Always keep the original capture; write inverted results as new files.
- **Same code path for preview and output**, so the preview matches the saved result.
- **Type hints and docstrings** on public functions. Keep functions small.
- **Tests for every `processing/` function** using synthetic frames, runnable with no camera and no SDK.
- **Comments explain why, not what.** Keep them sparse. Do not narrate the history of a bug in a docstring; if a past mistake explains a constraint that still binds, one sentence is enough.
- **Clear errors.** Translate EDS error codes into messages naming the real-world cause: camera not connected, session already open by another app, USB cable, camera asleep, wrong SDK bitness.
- **Cross-platform where free.** Use `pathlib`; keep platform-specific code (the message pump) isolated so macOS/Linux support stays possible.

## 9. Milestone guidance

Follow `ROADMAP.md` for scope. **v0.0 gates everything** — do not build v0.1 before its numbers are in and the human has seen them. For each later milestone: implement against the mock, add tests, validate against the real camera with the human, then update the README.

Special care for **v0.3 colour inversion** — the hardest part. Implement the staged pipeline in the roadmap (sample film base → normalise orange mask → per-channel invert → per-channel curves), expose the key parameters, give good defaults, and test with synthetic negative fixtures as well as real frames. The sibling project's v0.1 linear inversion is portable and honest about being only a preview toggle; do not let it masquerade as the real thing.

Also in v0.3: **check whether EDSDK can decode RAW to RGB itself.** If it can, it removes a dependency and directly solves inverting CRAW captures. Verify before designing around it.

## 10. Working style with the human

- The human's C is rusty and this project involves a native SDK — the one part of the sibling project that had no equivalent. Explain the `ctypes` and threading parts as you go; do not assume familiarity.
- Explain *why* before large changes; teach as you build.
- When a step must happen on Canon's site or the physical camera, say so clearly and hand off. Do not pretend to automate it.
- **Before running any command against the real camera, state what it will do** — especially anything that fires the shutter or changes camera settings.
- Report performance honestly, including when the numbers are disappointing. The v0.0 kill criterion is only useful if you are willing to invoke it.
- Keep the public repo newcomer-friendly: good README, honest limitations, clear setup.

## 11. Quick reference — the guardrails, condensed

- ✅ External SDK control only; no firmware modification.
- ✅ One thread owns EDSDK; everything else uses a queue.
- ✅ All `ctypes` signatures in `bindings.py`, with `argtypes` and `restype`.
- ✅ Camera-agnostic; detect capabilities.
- ✅ B&W **and** colour.
- ✅ Electronic shutter + settle delay.
- ✅ Mock backend so development needs no SDK.
- ✅ Measure on real hardware before believing anything about performance.
- ❌ Never commit the EDSDK, its headers, its binaries, or its reference text.
- ❌ Never commit secrets, serial numbers, or captures.
- ❌ Never guess a native function signature from memory.
- ❌ Never assume B&W-only.
- ❌ Never build past v0.0 without the latency numbers.

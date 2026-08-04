# CONTEXT — handoff for the next session

Read `CLAUDE.md` and `ROADMAP.md` first; they are the standing instructions and
do not change. This file is the current state and what to do next.

**Status: v0.0–v0.3 built and verified on real hardware.** 115 tests pass.
Everything is committed. Nothing is half-applied.

---

## 1. Start here — the four tasks queued, in order

### 1. HEIF support (blocks the user's current camera setting)
The R7 is set to **RAW+JPEG**, but was on HEIF, and `.HIF` files still land in
`captures/`. `cv2.imread` cannot read them in this OpenCV build, so developing a
HEIF fails with a clear error while the original is kept.

Add **`pillow-heif`** (ships wheels, bundles libheif) and route `.hif/.heif/.heic`
through it in `src/cefs/processing/develop.py`. The decoder choice already
branches on file type there; add a third branch. Keep the honest error for when
the decoder is missing.

The user asked for **all formats supported: HEIF, JPEG, RAW, CRAW**. Note CRAW is
Canon's compressed RAW *inside* a `.CR3` container, so LibRaw already handles it —
no separate work, but say so rather than letting them think it was missed.

### 2. Capture settings UI (user asked for this explicitly)
A section under **Capture** in `src/cefs/app/static/index.html` for:
- **Save location** (currently only editable in `config.yaml`)
- **Settle delay**
- **Develop positives** on/off (`capture.develop_positives`)
- Positive output format / compression

Needs matching endpoints in `server.py` and setters in `session.py`. Config
fields already exist in `src/cefs/config.py`.

### 3. LZW-compress the positive TIFF
A 16-bit 32 MP positive is **160 MB** uncompressed. `cv2.imwrite` with
`IMWRITE_TIFF_COMPRESSION` (5 = LZW) roughly halves it losslessly.

### 4. Filename templates
The user wants configurable names. **Coordinate with ROADMAP v0.5**, which
specifies structured naming, foldering and per-roll sidecar metadata — build it
once, properly, rather than a throwaway now.

---

## 2. Environment facts you will otherwise rediscover slowly

- **Python**: `.venv` on **3.13** 64-bit. Not 3.14 — no `opencv-python` wheels.
- **SDK**: `EDSDK_v13.20.21_Windows/` in the repo root, git-ignored. 64-bit
  library at `EDSDK_v13.20.21_Windows/EDSDK_64/Dll`. Headers at
  `EDSDK_v13.20.21_Windows/EDSDK/Header/` — **read these for any signature.**
- **Camera**: EOS R7 + RF85mm F2 MACRO IS STM, USB-C.
- **Captures**: `D:\projects\Canon EDSDK Film Scanner\captures\` (git-ignored).
- **Run**: `.venv\Scripts\python -m cefs.app.server` (mock) or `--real`.
- **Check hardware**: `.venv\Scripts\python -m cefs.tools.check_camera --focus`
  (add `--capture` to fire one shot).

---

## 3. Traps this project has already fallen into

Every one of these cost real time. They are not hypothetical.

**A silent no-op looks exactly like success.** This is the recurring theme.
- `EdsSendCommand(DriveLensEvf)` returned `EDS_ERR_OK` while moving nothing
  detectable. A return code proves the command was *accepted*, never that it
  had an effect. Confirm effects by measuring the image.
- A `str.replace` in a patch script matched nothing and reported no error, so a
  commit described behaviour the code did not have. Capture shipped broken.
  **If an edit cannot fail loudly, assert afterwards that it changed something.**
- `.gitignore` had `EDSDK/` unanchored. Git on Windows matches ignore rules
  **case-insensitively**, so it swallowed our own `src/cefs/edsdk/` — the entire
  native layer went uncommitted for days. `git status` stayed clean because
  ignored files do not appear. The rules are now anchored (`/EDSDK/`, `/Dll/`,
  `/Header/`) and **must stay that way**. Check both directions: that Canon's
  files are ignored *and* that ours are not.

**Measure, and know what your instrument cannot see.** The whole-frame
mean-absolute-difference metric used to calibrate focus steps is blind below
~2× the noise floor and saturates on large moves. It correctly caught a step
that was invisible, then reported 1×1 and 2×8 as identical. The user judging
through an 8× loupe resolves far more. Pick the instrument to match the question.

**Two bugs in the inversion were caught only by measurable assertions**, because
both produced plausible-looking images: the contrast exponent was inverted (so
"more contrast" flattened the image), and exposure was set from the highlight
rather than the median (so one specular reflection left everything near black).

**Restart the server and hard-refresh.** Python does not reload modules; browsers
cache `app.js` hard. Two "bugs" in the sibling project were exactly this.

**Never commit Canon's SDK** — headers, DLLs, sample tree, reference PDF. Never
quote Canon's prose. Function and constant *names* in code are fine.

---

## 4. What was measured (do not re-derive)

| | |
|---|---|
| Live view | 960×640, **~60 fps ceiling**, 17 ms period. 15× the Wi-Fi baseline |
| Bottleneck | the camera's output rate, **not** USB |
| Processing pipeline | 13.2 ms colour / 19.0 ms B&W at 960×640 — inside budget |
| Capture download | ~10–17 MB/s |
| Colour cast vs linear flip | 96.8 → 2.0 (**~48× better**) |
| B&W neutrality | 0.000 channel spread |
| Sharpness metric | gated Tenengrad, **11.1×** separation on real frames |
| Focus: fine | SDK minimum (size 1 × 1), 22 ms/press |
| Focus: medium / coarse | 2×6 (141 ms) / 3×20 (377 ms) |
| `Evf_Zoom` | **NOT_SUPPORTED** on R7 — no camera-side magnification |
| Shutter mode | EDSDK exposes no property; must be set in the camera menu |
| **EDSDK RAW decode** | **Cannot decode CR3.** `EdsGetImage` → `NOT_SUPPORTED` for every target. Works on JPEG. `EdsGetImageInfo` *lies* — reports 1620×1080/16-bit for a CR3, which is an embedded preview. rawpy is used instead. |

---

## 5. Architecture, briefly

```
src/cefs/
├── backend.py      the contract both backends implement
├── config.py       defaults → config.yaml → CEFS_* env vars
├── edsdk/          bindings.py (ALL ctypes, nothing anywhere else)
│                   camera.py (STA thread + message pump + command queue)
│                   errors.py, decode.py
├── processing/     pure numpy. film.py is the v0.3 inversion; develop.py
│                   turns a capture into a positive; raw.py wraps LibRaw
├── mock/           camera-free backend, runs at a realistic frame rate
├── app/            session.py (view state + pipeline), server.py, static/
└── tools/          check_camera.py — diagnostics against real hardware
```

**One thread owns the SDK.** Everything else enqueues a callable and waits. The
thread must pump messages or events never arrive — including capture-complete.

**The inversion is one code path for preview and file.** Every per-pixel step is
a scalar function of one input, so it collapses into a lookup table per channel,
built once per frame from a subsample. That is what makes it fast *and* what
guarantees the preview matches the saved result.

---

## 6. User feedback so far

Confirmed working on real film: colour inversion ("great"), film base sampling,
peaking ("works great"), focus drive, live view ("20× better than the Wi-Fi
version"). Fine focus steps were too coarse and have since been reduced to the
SDK minimum — **not yet retested by the user.**

They read output carefully and report precisely. Give them numbers, name what is
unverified, and do not claim a feature works until it has been measured on
hardware.

---

## 7. Honest gaps

- **HEIF cannot be developed** (task 1).
- **No capture settings UI** (task 2).
- Positive TIFFs are uncompressed and large (task 3).
- Filename templates not built (task 4, v0.5).
- **Windows only.** The message pump is isolated, so macOS/Linux stay possible.
- Glass-to-glass latency has never been measured by either project. The quoted
  "251 ms" baseline is a frame period (1/fps), not lag.
- v0.4 (corner alignment) not started; `corner_sharpness()` already exists and
  is tested.

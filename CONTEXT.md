# CONTEXT — handoff for the next session

**`CLAUDE.md` and `ROADMAP.md` no longer exist** — both were deleted and added
to `.gitignore`. There are currently no standing instructions beyond this file,
and no written roadmap. That matters for task 1 below.

**Status: tasks 1–3 of the previous handoff are done, plus a base-scale bug
fix.** 153 tests pass (was 115). Verified against the real `.HIF` and `.CR3`
files in `captures/`, but **not yet against a live camera** — see §2.

---

## 1. The one task still queued

### Filename templates — deferred, deliberately
The user wants configurable names. The previous handoff said to coordinate this
with the ROADMAP v0.5 spec (structured naming, foldering, per-roll sidecar
metadata) — and that file is gone. Asked, and the user chose to **defer until
they restate the spec**. Do not build a throwaway version in the meantime.

---

## 1b. What was built this session

**HEIF, done properly.** `pillow-heif` added; `.hif/.heif/.heic` route through
it in `develop.py`. The important discovery: a Canon body writes HEIF only in
HDR PQ mode — measured on the R7 files, the NCLX profile says transfer
characteristic **16 (SMPTE ST 2084)**, BT.2020 primaries, **10 bits**, arriving
left-shifted into 16 (adjacent codes differ by exactly 64). Decoding it as sRGB
would be a plausible-looking mistake, so the transfer is read from the file, not
assumed. `pq_to_linear` and `invert_pq` live in `film.py`; the PQ EOTF composes
into the same per-channel LUT the rest of the pipeline uses, so there is no
second code path. CRAW needed nothing — it is a compression mode inside `.CR3`.

**Capture settings UI.** Save location, settle delay, develop-positives,
positive format, TIFF compression, JPEG quality — `POST /api/capture/settings`,
validated in `Session.update_capture`, all under **Capture** in the panel.
Session-scoped; `config.yaml` is never rewritten. Settle delay reaches a live
backend through a new settable `settle_delay_s` property on both backends.

**TIFF compression.** `none`/`lzw`/`deflate`, all verified lossless by reading
back and comparing every pixel. LZW is the default.

**The base-scale bug** (found while verifying, pre-existing, fixed — §3).

---

## 1c. Not yet verified on hardware

Everything below works against files and the mock, and none of it has met a
camera. `TESTING.md` §7b walks the user through it.

- HEIF developed only from files already on disk, never off a shutter release.
- Every capture setting: save location, settle delay, format, compression.
- Fine focus steps reduced to the SDK minimum — still unretested, from before.

---

## 2. The base-scale bug, fixed

Found while verifying HEIF, and it predates this session. Pressing **Sample
film base from loupe area** stored the sampled *value* and carried it into
`develop()`. But "linear" is not one scale: live view is sRGB-linear (1.0 =
display white), a CR3 is sensor-linear (1.0 = saturation), a PQ HEIF is
absolute (1.0 = 10000 cd/m², and a negative sits near 0.02). Measured on the
real `.HIF`: a preview-sampled base collapsed the positive's tonal spread from
**std 0.300 to 0.111**, with nothing outside 0.32–0.74. Flat, and plausible
enough to keep. On CR3 it was mild (5.3%) because the scales happen to overlap.

Fixed by carrying the **region** instead of the value: `Session._base_region`
holds normalised coordinates, and `develop(..., base_region=)` re-measures the
same rebate in the file's own scale at full resolution. `develop` now ignores
`params.base` entirely.

Two things this uncovered, both worth remembering:

- **`FilmParams.replace` drops `None` by design**, so `replace(base=None)` kept
  the old base — the UI's Reset had been reporting success and clearing
  nothing. There is now an explicit `without_base()`.
- **`np.asarray(heif_file)` is a view into libheif's buffer.** It is freed with
  the `HeifFile`, and touching the array afterwards is an access violation that
  kills the process with no traceback. It crashed the suite twice — once in
  library code, once in a test. Always copy.

---

## 3. Environment facts you will otherwise rediscover slowly

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

## 4. Traps this project has already fallen into

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

## 5. What was measured (do not re-derive)

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
| **R7 HEIF** | 6960×4640, **10-bit**, PQ (ST 2084) over BT.2020, full range. Codes arrive left-shifted into 16 bits (gap 64). Linear range of a real negative: **0.016–0.066** (160–660 cd/m²) |
| Develop, end to end | HEIF **3.8–4.0 s**, CR3 **4.9 s** (32 MP, LZW) |
| TIFF write, 32 MP 16-bit | none **194 MB / 0.13 s** · LZW **103 MB / 1.89 s** · deflate **75.5 MB / 4.86 s**. All lossless, verified pixel-for-pixel |
| PQ decoded as sRGB | 3.1% mean error vs 0.9% for the correct path, on a real negative's range; **31×** on a 30× range. The 0.9% is the 10-bit quantisation floor (0.98% with no file at all) |
| HEIF vs CR3, same frame | positives agree on level (mean 0.455 vs 0.445); HEIF carries **~23% more tonal spread** (std 0.292 vs 0.237), consistent with the camera's own rendering being baked in |

---

## 6. Architecture, briefly

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

## 7. User feedback so far

Confirmed working on real film: colour inversion ("great"), film base sampling,
peaking ("works great"), focus drive, live view ("20× better than the Wi-Fi
version"). Fine focus steps were too coarse and have since been reduced to the
SDK minimum — **not yet retested by the user.**

They asked for **all formats supported: HEIF, JPEG, RAW, CRAW** — all four now
are. They also asked for configurable filenames, which is the one thing still
outstanding, and chose to defer it rather than get a throwaway.

They read output carefully and report precisely. Give them numbers, name what is
unverified, and do not claim a feature works until it has been measured on
hardware.

---

## 8. Honest gaps

- **Filename templates not built** — deferred pending the user's spec (§1).
- **Nothing from this session has met a camera** (§1c).
- **HEIF primaries are not converted.** Canon writes BT.2020; it is developed in
  that space, matching the RAW path, which also stays in the camera's own
  primaries (`output_color=raw`). Converting on one path and not the other
  would be worse. Neither is colour-managed end to end.
- **Windows only.** The message pump is isolated, so macOS/Linux stay possible.
- Glass-to-glass latency has never been measured by either project. The quoted
  "251 ms" baseline is a frame period (1/fps), not lag.
- v0.4 (corner alignment) not started; `corner_sharpness()` already exists and
  is tested.

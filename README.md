# Canon EDSDK Film Scanner

Digitize film negatives with a Canon camera on a copy stand — focusing, framing, and firing the shutter from your computer, with a **live positive preview**. Connected over **USB**, for live view fast enough to focus by.

Built on [EDSDK](https://developercommunity.usa.canon.com/), Canon's EOS Digital SDK. Works with black-and-white **and** colour negatives.

> **Status: works on real hardware, on Windows and macOS.** Live view, a focus loupe, remote focus stepping, focus peaking, a sharpness readout, and remote capture with a settle delay — all verified on an EOS R7 from both. The premise that justified the project is measured and confirmed: **59.79 fps / 17 ms at 960×640** over USB, against 3.98 fps / 251 ms for the same frame size over Wi-Fi. Details in [spike/README.md](spike/README.md).
>
> Colour and black-and-white negatives both invert properly, in the preview and in the file written after a capture.

---

## Why this exists

There is a sibling project, **Canon Smart Film Scan**, that does the same job over [CCAPI](https://developercommunity.usa.canon.com/) — Canon's HTTP API — with no native dependencies and a plain `pip install`. It works, and if wireless live view feels fine on your rig, **you should use that one instead.**

This project exists because on a real EOS R7 the wireless live view is slow, and the numbers say the network is why:

| Live-view mode | fps | frame period | throughput |
|---|---|---|---|
| 960×640 over Wi-Fi (CCAPI) | 3.98 | 251 ms | 1.23 MB/s |
| 640×424 over Wi-Fi (CCAPI) | 11.15 | 90 ms | 0.82 MB/s |
| **960×640 over USB (EDSDK)** | **59.79** | **17 ms** | **15.33 MB/s** |
| Host-side processing | — | 14.3 ms | — |

The processing could sustain ~70 fps. The Wi-Fi link caps at ~1.2 MB/s, because the R7 is Wi-Fi 4 on 2.4 GHz with no Ethernet port — and **CCAPI has no USB transport at all**, so there was no way to fix this without changing SDK.

EDSDK speaks PTP over USB, and the measurement confirms bandwidth stops being the constraint: at 960×640 the R7 delivers a steady 59.6–60.0 fps over a 20-second run, with the loop frequently arriving before the camera had a frame ready. **The limit is now the camera's live-view output rate, not the link.**

One consequence worth knowing before building on this: at 60 fps the per-frame budget is ~17 ms, and host-side processing was measured at 14.3 ms. Processing was never the bottleneck over Wi-Fi; over USB it very nearly is.

> A note on the word *latency*: the figures above are frame periods — literally `1 / fps` — which is the quantity the CCAPI project quoted, so the comparison is like-for-like. True glass-to-glass lag (photon to pixel on your monitor) is a different and larger number that neither project has measured.

## What you trade for that

Be clear-eyed about this before starting:

| | Canon Smart Film Scan (CCAPI) | This project (EDSDK) |
|---|---|---|
| Install | `pip install`, nothing else | You must obtain EDSDK from Canon yourself |
| Connection | Wi-Fi, untethered | USB cable only |
| Platforms | Windows, macOS, Linux | Windows and macOS, both verified; Linux refused |
| Live-view frame period | 90–251 ms measured | **17 ms measured** at the larger frame size |
| Complexity | HTTP and JSON | Native SDK via `ctypes`, and one thread must own it |

## What it deliberately does not do

EDSDK is an external control SDK. **It never modifies camera firmware.** So it cannot change what the camera's own screen displays, cannot bake looks into the files the camera writes, and cannot unlock features the firmware does not already have. All image processing happens on the computer — which is exactly what film scanning needs.

It is also **not wireless**. If you need to shoot untethered, use the CCAPI project.

## Getting EDSDK

⚠️ **Canon licenses EDSDK per developer, and it may not be redistributed.** This repository contains code that *calls* the SDK; it will never contain the SDK itself.

1. **Register** with [Canon's Developer Community](https://developercommunity.usa.canon.com/) for your region and request EDSDK access. Approval is not instant.
2. **Check your camera is supported** on the download page before going further — Canon does not publish the compatibility list openly.
3. **Download the SDK for your platform.** Windows and macOS are separate downloads; the Windows one will not work on a Mac. Keep it *outside* this repository, or in `edsdk_sdk/`, which is git-ignored.
4. **Point the app at it** by setting `edsdk.library_dir` in `config.yaml`, which is git-ignored — the folder holding `EDSDK.dll` on Windows, or the one holding `EDSDK.framework` on macOS. Use the **64-bit** libraries with 64-bit Python; mixing bitness fails at load time with an error that never mentions bitness.
5. **On macOS, re-sign the framework**, or it will not load at all. Canon ships it with a broken signature — see step 4 of [Running it](#running-it).

Note for anyone hoping the RAW-develop bundle adds something: on macOS it cannot. That bundle's `DPP.framework` is **i386**, which macOS has not run since Catalina, so it will not load on any current Mac. `EDSDK.framework` beside it is universal and fine. RAW goes through LibRaw either way — and that is the better path here anyway, since the inversion wants linear sensor data rather than Canon's rendering.

Never commit the headers, the DLLs or frameworks, or the reference manual. Never paste Canon's reference text into an issue or a PR — the function names and constants you use in code are fine, Canon's prose is not.

## Hardware

- A Canon body supported by EDSDK. The **EOS R7** is the reference camera.
- A real data cable, plugged **straight into the machine**. Many USB-C cables are wired for USB 2.0 only, and plenty are charge-only. USB 2.0 is enough for live view — measured, it used 8.3 MB/s of a link that sustained 31 MB/s on downloads — so a slow preview is not the cable's fault. It does cap how fast captures land.
- A macro lens reaching ~1:1 for 35mm, a copy stand, a negative holder, and a backlight (high-CRI for colour).

**Autofocus lens:** focus can be driven from the PC.
**Manual lens:** you focus by hand, but keep the magnified view, the focus aids, and the remote shutter. The app detects which case applies.

## Running it

**With no SDK and no camera** — the default in a fresh clone:

```bash
python -m venv .venv                      # Python 3.10-3.13, 64-bit

# macOS/Linux
.venv/bin/pip install -r requirements.txt -e .
.venv/bin/python -m cefs.app.server

# Windows
.venv\Scripts\pip install -r requirements.txt -e .
.venv\Scripts\python -m cefs.app.server
```

Open <http://127.0.0.1:8000/> and press Connect. You get a synthetic film
negative with grain, an orange mask and a rebate — enough to develop against.

Keyboard: <kbd>Space</kbd> capture · <kbd>←</kbd><kbd>→</kbd> focus
(<kbd>Shift</kbd> medium, <kbd>Alt</kbd> coarse) · <kbd>I</kbd> invert ·
<kbd>L</kbd> loupe · <kbd>P</kbd> peaking · <kbd>S</kbd> sharpness.

**With a real camera** — verified on an EOS R7 from both Windows and macOS:

0. **Turn off the camera's Wi-Fi and Bluetooth.** A Canon body disables its USB
   data connection while wireless is on, and the symptom is simply
   `cameras detected: 0` — the camera still appears on the USB bus, so nothing
   points at the real cause. On macOS also quit **Image Capture** and
   **Photos**, which claim the camera the moment it is plugged in.
1. Copy `config.example.yaml` to `config.yaml`.
2. Set `edsdk.library_dir` to the folder holding the 64-bit library — the one
   containing `EDSDK.dll` on Windows, or `EDSDK.framework` on macOS.
3. Set `camera.use_mock: false`.
4. **On macOS, re-sign the framework before anything will load it.** Canon
   signs it as `Developer ID Application: Canon Inc.`, but ships it with the
   seal already broken — several image-processing bundles were added to the
   framework *after* it was signed. macOS then refuses it with `library load
   disallowed by system policy`, which never mentions signing. Copy it off the
   disk image, strip the Finder metadata `codesign` will not sign around, and
   re-seal it ad-hoc:
   ```bash
   ditto "/Volumes/Macintosh 1/EDSDK/Framework/EDSDK.framework" \
         edsdk_sdk/Framework/EDSDK.framework
   find edsdk_sdk/Framework/EDSDK.framework -name .DS_Store -delete
   xattr -cr edsdk_sdk/Framework/EDSDK.framework
   codesign --force --deep --sign - edsdk_sdk/Framework/EDSDK.framework
   codesign --verify --verbose edsdk_sdk/Framework/EDSDK.framework
   ```
   The last command must say **`valid on disk`**. Then confirm the
   architecture matches your Python:
   ```bash
   lipo -archs edsdk_sdk/Framework/EDSDK.framework/EDSDK   # arm64 on Apple Silicon
   ```
   The macOS 13.20.10 build is universal (`x86_64 arm64`), so Apple Silicon
   needs no Rosetta. An x86_64-only framework cannot be loaded by an arm64
   interpreter, and that error does not mention architecture either.
5. Check the camera, without firing anything:
   ```bash
   .venv\Scripts\python -m cefs.tools.check_camera --focus   # Windows
   .venv/bin/python -m cefs.tools.check_camera --focus       # macOS
   ```
   This reports the body, lens, capabilities and live-view rate, and confirms
   focus drive actually moves the image. Add `--capture` to fire one test shot.
6. `.venv\Scripts\python -m cefs.app.server` (`.venv/bin/…` on macOS)

The server binds to loopback. Do not expose it to a network you do not
control — it can fire your shutter.

### Measured on an EOS R7

| | |
|---|---|
| Live view | 960×640, ~60 fps available, 30 fps default |
| Capture download | **26 MB/s** (a 34.7 MB CR3 in 1.3 s) |
| Focus drive | works; 0.03 / 0.12 / 0.49 s per press (fine / medium / coarse) |
| Focus steps | fine is the SDK minimum, 1×1; medium 2×6, coarse 3×20 |
| Peaking | +7.4 ms/frame; whole pipeline 14.9 ms, still inside the 17 ms budget at 60 fps |
| Sharpness metric | gated Tenengrad, **11.1×** separation on real frames |
| Capture cycle | ~3 s RAW only; ~15 s for RAW+HEIF with a positive from each |
| Camera-side live-view zoom | **not available** over EDSDK; the software loupe is the only magnification |
| Electronic shutter | **not selectable** over EDSDK — set Shutter mode in the camera menu |

### Also measured on macOS (Apple Silicon, same R7)

Every feature above works. The numbers differ where the platform does:

| | macOS | Windows |
|---|---|---|
| Live view, app end to end | 27 / 48 / **71.7** fps at `target_fps` 30 / 60 / 120 | 59.79 fps |
| Live view, body's own rate | 96.5 fps of distinct frames | ~60 fps |
| Capture download | **31 MB/s** | 26 MB/s |
| Capture event (`EdsGetEvent`) | 0.51 s from release | via message pump |
| Focus per press, fine / medium / coarse | 41 / 171 / 512 ms | 22 / 141 / 377 ms |
| Develop CR3 / HEIF | 5.4 s / 3.9 s | 4.9 s / 3.8–4.0 s |

**Live view runs at the taking shutter speed.** With exposure simulation the
body emits frames at roughly the shutter, so a slow shutter throttles live
view long before USB or the host does — measured, 1/15 gave exactly 15 fps
(66.8 ms between frames) and a fast shutter gave 96.5. If focusing feels
choppy, check the shutter speed before suspecting anything else.

Focus feel was judged by eye at magnification rather than by the sharpness
metric, which cannot resolve a fine step: fine stepping is indistinguishable
from Windows, medium and coarse are proportionate, and peaking behaves.

## Capture formats

Every format an EOS body writes is developed into a positive, chosen by the
file rather than by a setting:

| Format | Decoder | Positive |
|---|---|---|
| RAW `.CR3`/`.CR2`, including CRAW | LibRaw — EDSDK cannot decode CR3, measured | 16-bit TIFF |
| HEIF `.HIF` | pillow-heif, 10-bit, PQ transfer read from the file's own profile | 16-bit TIFF |
| JPEG | OpenCV | JPEG |

CRAW needs no separate handling: it is a compression mode inside the `.CR3`
container. A HEIF is rendered in the camera where a RAW is not, so the two
develop close but not identically — measured on one frame shot both ways, the
positives agree on overall level within 1% and the HEIF carries about a quarter
more tonal spread.

Container, TIFF compression and JPEG quality are all settable from the UI or
`config.yaml`. TIFF defaults to **deflate**: measured on two real 32 MP
positives it holds 2.4–2.6× losslessly (194 MB → 74–81 MB), where LZW manages
only 1.1–1.2× on 16-bit continuous tone — and LZW is what OpenCV writes when
given no setting at all, so it was never a choice worth making.

If you keep the RAW and develop it elsewhere, turn positives off: set
`capture.develop_positives: false` and the camera to RAW only, and a shot
writes one ~35 MB `.CR3` and nothing else. The live preview still inverts, so
focusing is unaffected.

## Rolls and naming

A camera names everything `IMG_0001` and rolls the counter over at 9999, which
says nothing about which roll a scan belongs to and eventually collides. So
captures are filed by roll and frame:

```
captures/Roll014/Roll014_Frame07.CR3
                 Roll014_Frame07-positive.tif
                 roll.json
```

The template is configurable — fields are `{roll}` `{frame}` `{original}`
`{date}` `{time}` `{stock}`, `{frame:02d}` pads, and a `/` makes a folder. Leave
it empty to keep the camera's own names. The roll name is repeated in the
filename deliberately: scans get imported and moved, and `Roll014_Frame07.CR3`
survives being separated from its folder where `Frame07.CR3` does not.

**A frame is one shutter release, not one file.** A body set to RAW+JPEG sends
two files for one release and both take the same frame number, distinguished by
extension — they are two renderings of one photograph.

`roll.json` records the stock, developer, date and notes beside the frames, and
is rewritten after every capture, so filling a field in halfway through the roll
still records it. What a negative *is* cannot be read back off the file months
later unless it was written down at the time.

## Honest limitations

- **Corner-by-corner alignment checking is not built yet.** The per-region
  sharpness it needs is present and tested.
- **No batch re-develop.** Changing inversion settings does not re-export a
  roll you have already scanned; you would re-develop those frames yourself.
- **Linux is refused outright** rather than half-working. Windows and macOS are
  both verified on an EOS R7. On macOS the SDK must own the **main thread** —
  it reports zero cameras from anywhere else — so the web server runs on a
  worker there; see `cefs/edsdk/mainthread.py`. Canon's macOS framework also
  ships with a broken code signature and needs re-signing before it will load
  at all (step 4 above).

## Project layout

```
src/cefs/
├── backend.py       The contract both backends implement
├── config.py        Defaults, then config.yaml, then CEFS_* env vars
├── naming.py        Template -> Roll014/Roll014_Frame07.CR3
├── sidecar.py       The per-roll roll.json written beside the frames
├── edsdk/           ctypes bindings + the thread that owns the SDK. Native.
│   ├── bindings.py      The ONLY place SDK signatures and constants live
│   ├── camera.py        Command queue, event pump, live view, capture
│   ├── mainthread.py    The main-thread SDK loop macOS insists on
│   ├── decode.py        EDSDK's own decoder, and the record of what it cannot do
│   └── errors.py        SDK error codes -> messages that name the real cause
├── processing/      Pure functions over numpy arrays. No SDK, no UI.
├── app/             Local web server + browser UI
├── mock/            A fake backend, so development needs no SDK and no camera
└── tools/           Diagnostics: check_camera reports what a body supports
tests/               213 tests, all runnable with no camera and no SDK
```

The layering matters: it is what lets the colour-inversion work happen with no hardware attached, and it is why roughly half of the sibling project can be ported straight across.

## Design constraint worth knowing up front

**One thread owns the SDK.** Every call — session, live view, shutter, properties — happens on it, and the rest of the app submits commands through a queue and waits. That much is true everywhere. *Which* thread is not:

- **Windows.** EDSDK delivers events through COM, so the owner must be a single-threaded-apartment thread running a Windows message pump. Any dedicated thread will do.
- **macOS.** The owner must be the **main thread**. `EdsGetCameraList` returns `EDS_ERR_OK` and *zero cameras* from anywhere else, and a worker also hangs in `EdsTerminateSDK`. It is not a run-loop problem — giving the worker a running CFRunLoop changes nothing and registers no sources on it. So the web server moves to a worker thread and the main thread runs the SDK loop; see `cefs/edsdk/mainthread.py`.

Events follow the same split: a Windows message pump there, `EdsGetEvent` polled here — which is what Canon documents for a console application.

Getting any of this wrong produces hangs and silently missing events rather than clean errors. Live view is *polled*, so it keeps working when event dispatch is broken; the first thing that actually fails is the capture-complete that never arrives. Do not take a working live view as evidence the threading is right.

## Contributing

Forks and PRs welcome. Keep the layers decoupled and add tests for anything in `processing/`.

**Never commit:** EDSDK headers or binaries, Canon's reference text, camera serial numbers, or your own scans.

Got a camera model working? A short test report including the v0.0 latency numbers is genuinely useful — it tells the next person whether this approach is worth it on their body.

## Licence

Permissive open-source, covering **this project's code only**.

Canon's EDSDK and its documentation are Canon's property under separate terms. Nothing here grants you any rights to them; you obtain your own access from Canon.

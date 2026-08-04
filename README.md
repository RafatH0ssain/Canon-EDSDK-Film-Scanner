# Canon EDSDK Film Scanner

Digitize film negatives with a Canon camera on a copy stand — focusing, framing, and firing the shutter from your computer, with a **live positive preview**. Connected over **USB**, for live view fast enough to focus by.

Built on [EDSDK](https://developercommunity.usa.canon.com/), Canon's EOS Digital SDK. Works with black-and-white **and** colour negatives.

> **Status: v0.1 works on real hardware.** Live view, a focus loupe, remote capture with a settle delay, and a positive preview — all verified on an EOS R7. The premise that justified the project is measured and confirmed: **59.79 fps / 17 ms at 960×640** over USB, against 3.98 fps / 251 ms for the same frame size over Wi-Fi. Details in [spike/README.md](spike/README.md).
>
> Colour negatives still preview cyan (v0.3), and focus stepping, peaking and the sharpness readout are not in the UI yet (v0.2). See [ROADMAP.md](ROADMAP.md).

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
| Platforms | Windows, macOS, Linux | Windows first; macOS/Pi/Ubuntu possible |
| Live-view frame period | 90–251 ms measured | **17 ms measured** at the larger frame size |
| Complexity | HTTP and JSON | Native SDK via `ctypes`, single-apartment threading |

## What it deliberately does not do

EDSDK is an external control SDK. **It never modifies camera firmware.** So it cannot change what the camera's own screen displays, cannot bake looks into the files the camera writes, and cannot unlock features the firmware does not already have. All image processing happens on the computer — which is exactly what film scanning needs.

It is also **not wireless**. If you need to shoot untethered, use the CCAPI project.

## Getting EDSDK

⚠️ **Canon licenses EDSDK per developer, and it may not be redistributed.** This repository contains code that *calls* the SDK; it will never contain the SDK itself.

1. **Register** with [Canon's Developer Community](https://developercommunity.usa.canon.com/) for your region and request EDSDK access. Approval is not instant.
2. **Check your camera is supported** on the download page before going further — Canon does not publish the compatibility list openly.
3. **Download and unpack the SDK.** Keep it *outside* this repository, or in `edsdk_sdk/`, which is git-ignored.
4. **Point the app at it** by setting `edsdk.library_dir` in `config.yaml`, which is git-ignored. Use the **64-bit** libraries with 64-bit Python; mixing bitness fails at load time with an error that never mentions bitness.

Never commit the headers, the DLLs, or the reference manual. Never paste Canon's reference text into an issue or a PR — the function names and constants you use in code are fine, Canon's prose is not.

## Hardware

- A Canon body supported by EDSDK. The **EOS R7** is the reference camera.
- A good USB cable — live view is a sustained transfer, not a trickle.
- A macro lens reaching ~1:1 for 35mm, a copy stand, a negative holder, and a backlight (high-CRI for colour).

**Autofocus lens:** focus can be driven from the PC.
**Manual lens:** you focus by hand, but keep the magnified view, the focus aids, and the remote shutter. The app detects which case applies.

## Running it

**With no SDK and no camera** — the default in a fresh clone:

```bash
python -m venv .venv                      # Python 3.10-3.13, 64-bit
.venv/Scripts/pip install -r requirements.txt -e .
.venv/Scripts/python -m cefs.app.server
```

Open <http://127.0.0.1:8000/> and press Connect. You get a synthetic film
negative with grain, an orange mask and a rebate — enough to develop against.

**With a real camera:**

1. Copy `config.example.yaml` to `config.yaml`.
2. Set `edsdk.library_dir` to the folder holding your 64-bit `EDSDK.dll`.
3. Set `camera.use_mock: false`.
4. Check the camera first, without firing anything:
   ```bash
   .venv/Scripts/python -m cefs.tools.check_camera --focus
   ```
   This reports the body, lens, capabilities and live-view rate, and confirms
   focus drive actually moves the image. Add `--capture` to fire one test shot.
5. `.venv/Scripts/python -m cefs.app.server`

The server binds to loopback. Do not expose it to a network you do not
control — it can fire your shutter.

### Measured on an EOS R7

| | |
|---|---|
| Live view | 960×640, ~60 fps available, 30 fps default |
| Capture download | ~10 MB/s (a 14.6 MB HEIF in ~1.4 s) |
| Focus drive | works, but one step is below the frame noise — steps must accumulate |
| Camera-side live-view zoom | **not available** over EDSDK; the software loupe is the only magnification |
| Electronic shutter | **not selectable** over EDSDK — set Shutter mode in the camera menu |

## Honest limitations

- **Colour negatives preview cyan.** v0.1 inversion is a plain linear flip,
  which is fine for black & white but leaves colour film with the inverse of
  its orange mask. Doing it properly is v0.3 and needs a real pipeline.
- **The preview is inverted; the saved file is not.** Captures are always kept
  exactly as the camera wrote them. Inverting captured files is v0.3.
- **No focus stepping, peaking or sharpness readout in the UI yet.** The
  processing for the latter two is present and tested; wiring is v0.2.
- **Windows only so far.** The message pump is platform-specific and isolated,
  so macOS and Linux remain possible, but neither is done.

## Project layout

```
src/cefs/
├── backend.py       The contract both backends implement
├── config.py        Defaults, then config.yaml, then CEFS_* env vars
├── edsdk/           ctypes bindings + the camera thread. Native, Windows-first.
│   ├── bindings.py      The ONLY place SDK signatures and constants live
│   ├── camera.py        Camera thread: STA, message pump, command queue
│   └── errors.py        SDK error codes -> messages that name the real cause
├── processing/      Pure functions over numpy arrays. No SDK, no UI.
├── app/             Local web server + browser UI
├── mock/            A fake backend, so development needs no SDK and no camera
└── tools/           Diagnostics: check_camera reports what a body supports
tests/               64 tests, all runnable with no camera and no SDK
```

The layering matters: it is what lets the colour-inversion work happen with no hardware attached, and it is why roughly half of the sibling project can be ported straight across.

## Design constraint worth knowing up front

EDSDK delivers events through COM, which requires a **single-threaded-apartment thread running a Windows message pump**. So every SDK call — session, live view, shutter, properties — happens on **one dedicated thread**, and the rest of the app submits commands to it through a queue.

Getting this wrong produces hangs and silently missing events rather than clean errors, which is why the camera thread gets built properly before anything sits on top of it.

## Contributing

Forks and PRs welcome. Keep the layers decoupled and add tests for anything in `processing/`.

**Never commit:** EDSDK headers or binaries, Canon's reference text, camera serial numbers, or your own scans.

Got a camera model working? A short test report including the v0.0 latency numbers is genuinely useful — it tells the next person whether this approach is worth it on their body.

## Licence

Permissive open-source, covering **this project's code only**.

Canon's EDSDK and its documentation are Canon's property under separate terms. Nothing here grants you any rights to them; you obtain your own access from Canon.

# Canon EDSDK Film Scanner

Digitize film negatives with a Canon camera on a copy stand — focusing, framing, and firing the shutter from your computer, with a **live positive preview**. Connected over **USB**, for live view fast enough to focus by.

Built on [EDSDK](https://developercommunity.usa.canon.com/), Canon's EOS Digital SDK. Works with black-and-white **and** colour negatives.

> **Status: v0.0 passed.** The premise is measured and confirmed — **59.79 fps / 17 ms at 960×640** over USB on an EOS R7, against 3.98 fps / 251 ms for the same frame size over Wi-Fi. That is 15× the frame rate at identical resolution. Details in [spike/README.md](spike/README.md). The app itself is not built yet; v0.1 is next.

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
4. **Point the app at it** via a git-ignored config file (once that exists).

Never commit the headers, the DLLs, or the reference manual. Never paste Canon's reference text into an issue or a PR — the function names and constants you use in code are fine, Canon's prose is not.

## Hardware

- A Canon body supported by EDSDK. The **EOS R7** is the reference camera.
- A good USB cable — live view is a sustained transfer, not a trickle.
- A macro lens reaching ~1:1 for 35mm, a copy stand, a negative holder, and a backlight (high-CRI for colour).

**Autofocus lens:** focus can be driven from the PC.
**Manual lens:** you focus by hand, but keep the magnified view, the focus aids, and the remote shutter. The app detects which case applies.

## Planned project layout

```
src/cefs/
├── edsdk/           ctypes bindings + the camera thread. Native, Windows-first.
│   ├── bindings.py      The ONLY place SDK signatures and constants live
│   ├── camera.py        Camera thread: STA, message pump, command queue
│   ├── liveview.py      Frame pump
│   └── errors.py        SDK error codes -> messages that name the real cause
├── processing/      Pure functions over numpy arrays. No SDK, no UI.
├── app/             Local web server + browser UI
└── mock/            A fake backend, so development needs no SDK and no camera
tests/               Runs without a camera
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

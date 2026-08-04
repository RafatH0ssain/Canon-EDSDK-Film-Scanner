# Canon EDSDK Film Scanner — Roadmap

**A wired, low-latency film-scanning tool for Canon cameras, built on Canon's EOS Digital SDK (EDSDK) over USB.**

Digitize developed film negatives with a camera + macro lens on a copy stand — focusing, framing, and firing the shutter from the computer, with a live positive preview. Same goal as its sibling project, different transport: **USB instead of Wi-Fi**, chosen because live-view latency over Wi-Fi is the thing that makes the workflow unpleasant.

---

## 1. Why this project exists separately

There is already a working tool for this: **Canon Smart Film Scan**, built on CCAPI (Canon's HTTP camera-control API). It does live view, a focus loupe, focus peaking, a sharpness readout, remote shutter with settle delay, and auto-download. It works.

It is also **slow**, and the reason is measured, not assumed:

| Live-view mode | fps | frame size | throughput | latency |
|---|---|---|---|---|
| `medium` 960×640 | 3.98 | 316 KB | 1.23 MB/s | **251 ms** |
| `small` 640×424 | 11.15 | 76 KB | 0.82 MB/s | **90 ms** |
| Host-side processing | — | — | — | 14.3 ms |

The processing pipeline could sustain ~70 fps. The bottleneck is entirely the link: CCAPI ships MJPEG over Wi-Fi, and the reference camera (EOS R7) is **Wi-Fi 4, 2.4 GHz only, with no Ethernet port**. There is no faster access point to buy, and **CCAPI has no USB transport at all** — USB is used only for its one-time activation.

So getting a wired connection means leaving CCAPI. EDSDK is Canon's other SDK: it speaks PTP over USB, and the R7's USB-C port is 10 Gbps, making bandwidth a non-issue.

**Why a separate repository rather than a second backend in the existing project:**

- EDSDK is a **native C SDK** with per-developer licensing and redistributable binaries. CCAPI needs nothing but `pip install`. Mixing them makes the simple project harder to install for everyone.
- EDSDK requires a **single-threaded-apartment thread with a Windows message pump** (see §5). That is a different concurrency model, not a swappable transport.
- EDSDK is **USB-only**. CCAPI is wireless-only in practice. They serve genuinely different rigs.

The two projects should stay friendly: the image-processing and web-UI layers are transport-agnostic and should be ported, not reinvented. See §7.

## 2. Who it's for

- Anyone already camera-scanning film who finds wireless live view too laggy to focus comfortably.
- Users whose camera has no Ethernet and only 2.4 GHz Wi-Fi — which is most enthusiast bodies.
- Black-and-white **and** colour shooters. Colour negative support is a first-class goal.

**If your rig works acceptably over Wi-Fi, use the CCAPI project instead.** It installs with pip, runs on any OS, and needs no native SDK. This project trades those away for latency.

## 3. What this is *not*

- **Not a firmware modification.** EDSDK is an external, documented control SDK. It cannot change what the camera's own screen shows, cannot bake looks into files the camera writes, and cannot unlock features the firmware lacks.
- **Not wireless.** EDSDK is USB tethering. If you need to shoot untethered, use CCAPI.
- **Not redistributable as a single download.** Canon licenses EDSDK per developer. Users must obtain it themselves; this repo ships code that *calls* it, never the SDK. See §4.

## 4. The EDSDK licensing constraint

Same shape as CCAPI's, and just as binding:

- EDSDK is obtained by registering with **Canon's Developer Community** for your region and requesting access.
- The SDK archive contains **headers, DLLs/dylibs/shared objects, and a reference manual**. All are covered by Canon's licence terms.
- **None of it may be committed to this repository**, and the reference text may not be quoted into issues, PRs, or source comments.
- Redistribution of the binaries is restricted, which is why this project cannot ship a one-file installer that "just works". Setup docs must walk users through obtaining their own copy.

This is baked into `.gitignore` and the contributing guidelines. Function names and constants that appear in code are fine; Canon's documentation text is not.

## 5. Architecture overview

```
┌──────────────┐   PTP over USB    ┌────────────────────────┐
│  Canon body  │ <---------------> │  EDSDK (native DLL)    │
│  (R7 etc.)   │                   │  loaded via ctypes     │
└──────────────┘                   └───────────┬────────────┘
                                               │  all calls on ONE thread
                                   ┌───────────▼────────────┐
                                   │  Camera thread          │
                                   │  STA + message pump     │
                                   │  command queue in,      │
                                   │  frames + events out    │
                                   └───────────┬────────────┘
                                               │
                                   ┌───────────▼────────────┐
                                   │  Processing engine      │
                                   │  invert, peaking,       │
                                   │  sharpness, alignment   │
                                   │  (pure numpy)           │
                                   └───────────┬────────────┘
                                               │
                                   ┌───────────▼────────────┐
                                   │  Local web UI           │
                                   │  (browser, fullscreen)  │
                                   └────────────────────────┘
```

**The camera thread is the defining constraint.** EDSDK delivers events through COM, which requires the initialising thread to be in a single-threaded apartment with a running Windows message pump. Practical consequences:

- `EdsInitializeSDK`, session open/close, live view, shutter and property access all happen on **one dedicated thread**.
- Every other part of the app submits **commands to a queue** and waits on a result, rather than calling the SDK directly.
- That thread must pump messages regularly or events (including capture-complete notifications) never arrive.
- Getting this wrong produces hangs and missed events rather than clean errors, so it is worth building the thread properly before anything else sits on top of it.

This is stricter than the CCAPI project, where any thread could make an HTTP call behind a lock.

**Layering.** The EDSDK layer knows nothing about images or the UI. The processing engine is pure functions over numpy arrays. The web app wires them. A **mock backend** implements the same interface with no SDK and no camera, so most development needs neither.

## 6. Hardware & software assumptions

**Hardware**
- A Canon body supported by EDSDK. Canon does not publish the compatibility list openly — check it on the developer download page before starting. The **EOS R7** is the reference/test body.
- A USB cable appropriate for the body (USB-C for the R7). Use a decent cable; live view is a sustained transfer.
- A macro lens reaching ~1:1 for 35mm, a copy stand, a negative holder, and a backlight — high-CRI if you shoot colour.

**Lens focus**
- With an **autofocus** lens, focus can be driven from the PC.
- With a **fully manual** lens, focus is by hand, but the magnified view and focus aids still apply. The app must detect and adapt.

**Software**
- Python 3.10+. Use 3.13 or lower until `opencv-python` publishes wheels for newer versions.
- **Windows first.** EDSDK also supports macOS, Raspberry Pi OS and Ubuntu, so cross-platform is achievable, but the event model differs by platform and only one should be tackled at a time.
- EDSDK obtained separately by each user.

## 7. What to port from the CCAPI project

Roughly half of Canon Smart Film Scan is transport-agnostic and should be carried across rather than rewritten:

| Component | Portable? | Notes |
|---|---|---|
| `processing/invert.py` | Yes, as-is | Linear inversion; the colour pipeline is still to be built |
| `processing/peaking.py` | Yes, as-is | Absolute-threshold focus peaking, calibrated on real frames |
| `processing/sharpness.py` | Yes, as-is | Gated Tenengrad; validated against a real camera |
| `processing/loupe.py` | Yes, as-is | Crop-and-zoom with edge clamping |
| `processing/codec.py` | Mostly | EDSDK hands back JPEG bytes for live view, so decode still applies |
| `app/server.py`, `app/static/` | Yes, with edits | Same UI; swap the session layer underneath |
| `app/session.py` | Rewrite | Owns the transport; becomes the camera-thread client |
| `ccapi/*` | No | Replaced entirely by `edsdk/*` |
| `mock/frames.py` | Yes, as-is | Synthetic negatives with rebate, orange mask, grain and defocus |

**Recommendation:** copy the portable files in at v0.1 with their tests, keeping module paths parallel so diffs against the sibling project stay legible. Do **not** try to share a package between the two repos until both have settled; premature coupling would make the simple project depend on the complicated one.

## 8. Feature roadmap

Milestones are ordered so each is independently useful and testable.

### v0.0 — Spike: prove the premise, with a kill criterion

**This milestone exists to decide whether the project should continue.** The entire justification is latency. Measure it before building anything.

- Load EDSDK from Python via `ctypes`.
- Initialise the SDK on an STA thread with a working message pump.
- Enumerate cameras and open a session with the body over USB.
- Start live view, pull frames, and **measure: frames per second, end-to-end latency, frame resolution, and stability over a few minutes.**
- Drive focus one step and confirm the frame changes.

**Decision gate.** The CCAPI baseline is **3.98 fps / 251 ms at 960×640**, or **11.15 fps / 90 ms at 640×424**. If EDSDK does not clearly beat the larger-frame figure — say, 20+ fps at comparable or better resolution — then **stop and go back to the CCAPI project**, using its `small` live-view size and treating this repo as a documented dead end. Write the numbers down either way.

*Outcome: a throwaway script and a table of numbers. No architecture, no UI, no tests beyond what proves the numbers.*

### v0.1 — MVP: sharp, shake-free remote capture

Only after v0.0 passes its gate.

- Proper **camera thread**: STA, message pump, command queue, lifecycle, clean shutdown.
- **Mock backend** implementing the same interface, so development needs no SDK or camera. Build it early.
- Live view streamed to a browser at a usable size, with the measured latency preserved.
- **Magnified focus loupe.**
- **Remote shutter**, with the **electronic shutter** selected and a configurable **settle delay**.
- Simple **invert toggle** for a positive preview.
- Captured file **auto-downloads** to a chosen folder; the original is always kept.

### v0.2 — Focus control & focus aids

- **Remote focus stepping** with keyboard shortcuts, coarse and fine.
- Detection and graceful fallback for manual-focus lenses.
- **Focus peaking** overlay, ported from the sibling project.
- **Sharpness readout** for objective focus confirmation.
- **Camera-side live-view zoom if EDSDK exposes it.** Worth checking early: the R7 does *not* offer live-view magnification over CCAPI, so if EDSDK does, that is a real capability gain over the sibling project and not merely a speed one.

### v0.3 — Inversion done properly (B&W and colour)

- **B&W:** tone-curve-aware inversion with adjustable black/white points, handling film base density.
- **Colour negative**, the hard part: sample the film base/rebate, normalise against the orange mask, per-channel invert, per-channel curves, sensible defaults with the key parameters exposed.
- Applied **live** and to **downloaded captures** through the same code path.
- **Investigate EDSDK's image-processing functions for RAW.** The SDK appears able to decode RAW to RGB, which if true removes the need for a separate RAW library and directly solves inverting CRAW captures. Verify before designing around it.

### v0.4 — Alignment / parallelism checker

- Corner-by-corner sharpness, reporting which corners are soft and by how much.
- Optional live tilt indicator during setup.

### v0.5 — Roll automation & organization

- One-key capture → download → invert → save.
- Structured naming and foldering, configurable templates.
- Per-roll session metadata as a sidecar file.
- Batch re-invert of a roll after tweaking parameters, without rescanning.

### v0.6 — Consistent exposure & capture settings

- Lock exposure and related settings at the start of a roll.
- Preset profiles per film stock / format.
- **Camera setup on connect**: drive mode to single, AF to one-shot, auto power-off disabled. The sibling project found a real body arriving on continuous drive, where one remote release fires a burst.

### v1.0 — Polish, packaging, docs

- The **distribution problem**: users must supply their own EDSDK. Document it honestly and make setup as close to scripted as the licence allows — a helper that validates an SDK directory and reports what is missing.
- Robust error handling, reconnection, and clear messages for USB disconnects.
- Verified support across multiple bodies via community test reports.
- Sample outputs and a short demo.

### Post-1.0 — stretch ideas

- macOS / Raspberry Pi support, which EDSDK allows but which needs its own event handling.
- Dust and scratch heuristics — software only; true IR-channel removal is impossible here, so set expectations honestly.
- Auto-crop and auto-straighten to the film frame.
- Multi-frame stitching for medium and large format.
- Tethered contact-sheet view of a whole roll.
- Native desktop UI as an alternative to the browser.

## 9. Design principles

- **Prove the premise before building on it.** v0.0 exists because the entire reason for this project is a performance claim that must be measured on real hardware.
- **One thread owns the SDK.** Everything else talks to it through a queue.
- **Capability detection over model checks.** Ask what the camera supports; degrade gracefully.
- **Both film types are first-class.** Never assume black-and-white.
- **Separation of concerns.** EDSDK layer / processing / UI stay independently testable.
- **Developable without hardware.** A mock backend keeps most work possible with no camera and no SDK — but see the warning in `CLAUDE.md`: a mock encodes its author's assumptions, and only real hardware can correct them.
- **Keep the original.** Inversion is non-destructive; the un-inverted capture is always retained.
- **Respect Canon's licence.** No SDK binaries, headers, or reference text in the repository.

## 10. Contributing

- Fork, branch, PR. Keep the three layers decoupled.
- Do **not** commit: EDSDK headers or binaries, Canon's reference text, camera serial numbers, or personal captures.
- New camera model working for you? A short test report with the v0.0 latency numbers is a genuinely useful contribution.
- See `CLAUDE.md` for the from-zero development setup and conventions.

## 11. License

Permissive open-source (MIT or Apache-2.0) covering **this project's code only**. Canon's EDSDK and its documentation carry their own separate terms; nothing here grants any rights to them.

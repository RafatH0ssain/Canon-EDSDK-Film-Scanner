# v0.0 — running the live-view spike

This is the measurement that decides whether the project continues. It answers one
question: **is EDSDK live view over USB fast enough to be worth the complexity?**

Everything here is throwaway. Nothing should be built on top of it.

---

## Recorded result — PASS

**Canon EOS R7, USB-C, 2026-08-03. 20-second run.**

| | measured |
|---|---|
| fps | **59.79** |
| frame period (1/fps) | **17 ms** |
| resolution | **960×640** |
| inter-frame gap | median 15 ms, p95 25 ms, max 52 ms |
| `EdsDownloadEvfImage` | median 13.7 ms, max 44.1 ms |
| frame size | median 263 KB |
| throughput | 15.33 MB/s |
| stability (5 s windows) | 60.00 / 59.60 / 59.60 / 60.00 fps |
| frames / not-ready polls | 1196 / 272 |

**15.0× the 960×640 Wi-Fi baseline at identical resolution.** The gate is passed
decisively; v0.1 proceeds.

Two conclusions from the shape of these numbers, not just their size:

- **The camera is the limit, not the cable.** Two windows sit at exactly 60.00 fps
  and 272 polls found no frame ready, so the loop was outrunning the body's live-view
  output. USB bandwidth is a non-issue, and there is nothing to gain from optimising
  the transfer.
- **Processing is now the near-bottleneck.** 60 fps leaves a ~17 ms per-frame budget,
  and the sibling project measured host-side processing at 14.3 ms. Over Wi-Fi that
  headroom was irrelevant; over USB it is the thing to watch in v0.1.

### Focus stepping — works, with a caveat that matters for v0.2

`DriveLensEvf` drives the lens correctly on an R7 with an `RF85mm F2 MACRO IS STM`.
Verified by image, not by return code:

| | sharpness | diff vs start |
|---|---|---|
| start | 157 | — (noise floor 2.51) |
| after 15 × `near3` | **3320** | 21.77 |
| after 15 × `far3` | 156 | back to start |

**The caveat: a single step is invisible.** Even `near3`, the coarsest of the three
sizes, moves this lens by less than the frame's sensor noise — roughly 0.75 mean
absolute difference against a ~2.4 floor. A one-step test reports "nothing happened"
no matter how carefully it measures, and the lens focuses internally so nothing moves
visibly either. Both signals say "broken" while the SDK is working correctly.

Two consequences:

- **v0.2 focus stepping must accumulate steps per keypress**, not send one. A UI that
  sends a single step per press will feel completely dead on a lens like this.
- **Never trust `EDS_ERR_OK` as proof an action happened.** It means the command was
  accepted. Confirming the effect needs a measurement, and the measurement needs a
  noise floor to compare against.

### Two findings for later milestones

**No camera-side live-view magnification.** `Evf_Zoom` reports `NOT_SUPPORTED` on the
R7, and `EdsGetPropertyDesc` returns `access=0, count=0`. This settles the open
question in ROADMAP §8 v0.2: EDSDK offers no magnification gain over CCAPI here, so
the software loupe stays the only option. Capability detection should hide the control.

**Tenengrad is a good sharpness metric on this hardware.** It separated sharp from soft
by **21×** (157 → 3320) on real frames. The sibling project measured variance of the
Laplacian at only 1.22× and its replacement at 5.66× on real data, so this is worth
carrying into the v0.2 sharpness readout.

---

## What the gate actually is

The CCAPI sibling project, over 2.4 GHz Wi-Fi, measured:

| mode | fps | frame period |
|---|---|---|
| 960×640 | 3.98 | 251 ms |
| 640×424 | 11.15 | 90 ms |

One thing worth knowing before you read your own numbers: the baseline's quoted
"latency" is exactly `1 / fps`. It is the **inter-frame period**, not glass-to-glass
lag. This script reports the same quantity so the two are directly comparable. True
end-to-end lag — photon to pixel on your monitor — is a different figure that neither
project has measured, and measuring it properly needs the camera pointed at a running
millisecond clock.

**Pass:** 20+ fps at comparable or better resolution.
**Fail:** anything less. The honest move is then to stop, go back to the CCAPI project
using its `small` live-view size, and leave this repo as a documented dead end.

Write the numbers down either way. A test report from a different body is genuinely
useful to the next person.

---

## What it does to your camera

1. Opens a session and reads the body name.
2. Sets `Evf_OutputDevice` to `PC`. This starts live view and routes it to the
   computer — **your camera's rear screen will go blank** while it runs. That is
   expected, not a fault.
3. Pulls frames as fast as the link allows, for 20 seconds by default.
4. Sets `Evf_OutputDevice` back to `TFT`, restoring the screen, and closes the session.

**It never fires the shutter. It never writes to the card. It changes no exposure
setting.** The only thing that moves your lens is the optional `--focus-test`.

---

## Before you plug in

**On the computer**

- Close **EOS Utility**, **Lightroom tethering**, **Canon Camera Connect**, or anything
  else that may claim the camera. Only one program can hold the body at a time; a
  second one gets `DEVICE_BUSY` or `COMM_PORT_IS_IN_USE`.
- Windows sometimes auto-launches EOS Utility on connect. If it appears, quit it.

**On the camera**

- **Charge or mains-power it.** A 20-second run is short, but live view to the PC is
  a sustained draw, and you will likely run this several times.
- **Disable auto power-off.** If the body sleeps mid-run the session drops and the
  numbers are garbage. On the R7 this is in the wrench/set-up menu.
- Set the mode dial to **M**, **Av**, or **P** — a stills mode. Movie mode and some
  scene modes change or forbid live view behaviour.
- **Take the camera off any menu screen.** Live view will not stream while a menu or
  playback view is up; you get zero frames and a confusing timeout.
- Card in or out does not matter — nothing is written.

**The cable**

- Use a **data-capable USB-C cable**, plugged into the camera's USB-C port. Many cables
  sold with phones and battery packs are charge-only and will simply never enumerate.
  If the script reports zero cameras, this is the first thing to suspect.
- Prefer a port directly on the machine over a hub for the first run.

**The lens** — you mentioned an 85 mm AF macro. That is the good case: focus can be
driven from the PC, so `--focus-test` is meaningful for you. Make sure the lens's
**AF/MF switch is set to AF**, or the focus commands will report failure even though
the lens is capable.

---

## Running it

From the repository root, using the project venv:

```powershell
.venv\Scripts\python.exe spike\v0_0_liveview.py
```

That is the safe default: 20 seconds, 10 warm-up frames, no focus movement.

**With the focus test** — drives your 85 mm one step near, then one step back, at the
very end, after all measurement is done:

```powershell
.venv\Scripts\python.exe spike\v0_0_liveview.py --focus-test
```

**Useful flags**

| flag | default | why you'd change it |
|---|---|---|
| `--seconds 20` | 20 | Raise to 120 to check for thermal or buffer drift over a realistic session |
| `--warmup 10` | 10 | Frames discarded before timing starts; the first few are never representative |
| `--focus-test` | off | Drives the lens. AF lens only |
| `--dll-dir ...` | `EDSDK_v13.20.21_Windows/EDSDK_64/Dll` | If you move your SDK copy |

Note the default DLL path is **relative to the repository root**, so run the command
from there. It points at `EDSDK_64`, the 64-bit build — that is deliberate and must
match the 64-bit Python in `.venv`. Mixing bitness fails at load with an error that
never mentions bitness.

---

## Reading the output

A healthy run prints, in order: the DLL it loaded, SDK initialised, cameras detected,
the body name, session open, warm-up, then the results block.

The numbers that matter:

- **fps** and **frame period** — these are what the gate is judged on.
- **inter-frame gap, p95 and max** — a good median with a terrible max means stutter.
  For focusing, consistency matters as much as the average.
- **download call** — how long `EdsDownloadEvfImage` itself blocks. If this is most of
  the frame period, the link is the limit. If it is small and fps is still low, the
  polling loop is the limit and there is headroom to recover.
- **frame resolution** — EDSDK picks this; we do not request a size the way CCAPI does.
  A big win in fps at a much smaller frame is not the win it appears to be, so check it.
- **stability per 5 s window** — a rate that only holds for the first second is not a
  rate worth building on.

The script exits `0` on a pass and `1` on a fail, and prints its own verdict against
the baseline.

---

## If it goes wrong

The script translates SDK error codes into causes. The ones you are most likely to see:

| what you see | what it means |
|---|---|
| `cameras detected: 0` | Charge-only cable, camera asleep or off, or a hub problem. Try a different cable first — this is by far the most common cause. |
| `DEVICE_BUSY` / `COMM_PORT_IS_IN_USE` | Another program holds the camera. Quit EOS Utility and retry. |
| `SESSION_ALREADY_OPEN` | An earlier run crashed without closing. Unplug the USB cable, wait a few seconds, plug back in. |
| `Live view produced no frames in 15 s` | Camera is on a menu or playback screen, or in a mode that forbids live view. Half-press the shutter to return to shooting, then retry. |
| `COMM_DISCONNECTED` mid-run | Cable or port dropped, or the camera slept. Check auto power-off is disabled. |
| `The camera thread hung` | The message pump stopped. This is a bug in the spike, not your setup — worth reporting with the output. |
| `NOT_SUPPORTED` from the focus test | Lens is in MF, or is a manual-focus lens. Check the AF/MF switch. |

If the run dies partway, live view may be left routed to the PC and the camera screen
left blank. **Power-cycling the camera always restores it.**

---

## After you have numbers

Paste the results block back and we will decide the gate together. If it passes, v0.1
starts with a proper camera thread built on the same STA-plus-message-pump pattern this
spike proves. If it fails, that is a real answer too, and the README should say so
plainly rather than quietly.

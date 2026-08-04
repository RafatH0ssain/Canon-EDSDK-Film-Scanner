# Testing on the real rig

A walkthrough of everything built so far (v0.1–v0.3), on the actual camera with
actual film. Roughly 30–40 minutes.

The point is not to confirm it works. It is to find where it doesn't — every
number in the commit history so far came from synthetic fixtures, and a mock
only ever proves the code agrees with itself.

---

## Read this first — two things that will bite you

**1. Do not set the camera to RAW+JPEG yet.**

You chose RAW+JPEG as the capture format, but **the download path does not handle
it yet**. The camera sends one transfer event per file, and the current code
takes only the first and discards the rest. You would silently get one file per
shot, and possibly not the one you expected.

For this test, set the camera to a **single format** — RAW *or* JPEG, not both.
JPEG is the easier choice since nothing inverts captured files yet anyway.
I'll fix the dual-file path before you shoot a real roll.

**2. Restart the server and hard-refresh the browser.**

Python does not reload modules, and browsers cache `app.js` hard. If you skip
either, you will be testing old code and blaming new code. Two "bugs" in the
sibling project were exactly this.

```powershell
# stop any running server first (Ctrl+C in its window)
.venv\Scripts\python -m cefs.app.server --real
```

Then **Ctrl+Shift+R** in the browser at <http://127.0.0.1:8000/>.

---

## Setup

**Camera**
- Mode dial on **M** (or Av/P) — a stills mode, not movie.
- **Shutter mode → Electronic** in the camera menu. EDSDK cannot set this, so
  the app reports "camera menu" rather than pretending. On a copy stand,
  mechanical shutter shock is a real cause of soft scans.
- **Auto power-off → disabled.**
- Image quality → **a single format** (see the warning above).
- Lens **AF/MF switch → AF**, or focus stepping will be unavailable (and the app
  will tell you why).

**Computer**
- Quit EOS Utility / Lightroom tethering. Only one program can hold the camera.
- Nothing else should be using the camera — if a previous run crashed, unplug
  the USB cable, wait a few seconds, plug it back in.

**Rig**
- A developed negative in the holder, backlit, camera on the copy stand.
- Include a bit of the **rebate** — the clear unexposed strip between frames — in
  the field of view if you can. It matters for step 5.

---

## 1. Connect and check what it detected

Press **Connect**. Within a couple of seconds you should see live view and, in
the right-hand panel:

| Field | Expected on your rig |
|---|---|
| Body | `Canon EOS R7` |
| Lens | `RF85mm F2 MACRO IS STM` |
| Saving to | your `captures/` path |
| Shutter | `camera menu` (hover for why) |

**Focus drive** controls should be visible. If instead you see a message saying
the lens can't be driven, the AF/MF switch is on MF — that message is the app
working correctly, not failing.

🔍 **Look for:** the camera's own rear screen goes blank while connected. That is
expected — live view is routed to the PC. It restores on Disconnect.

---

## 2. Live view

Just watch it for 30 seconds.

✅ **Good:** smooth, no stutter, no tearing. Should feel like the responsive
version you already tried.

❌ **Report if:** it freezes, the stream dies after a while, or frames arrive in
visible jerks. Also check the server window for `WARNING`/`ERROR` lines.

---

## 3. Focus loupe

- Tick **Magnified loupe** (or press <kbd>L</kbd>).
- **Click anywhere on the image** — the loupe centres there.
- Drag the **Zoom** slider from 1× to 12×.

✅ **Good:** clicking near an edge or corner still gives a full-size view (it
slides inward rather than shrinking). Magnified pixels look blocky, not smooth —
that is deliberate: smooth interpolation invents detail and would make an
out-of-focus frame look acceptably sharp.

❌ **Report if:** the loupe jumps to the wrong place when you click, especially
with the browser window at an unusual aspect ratio.

---

## 4. Focus stepping and the sharpness readout

This is the part I'd most like real-world feedback on, because the step sizes
were calibrated on one scene.

- Tick **Measure continuously** under Sharpness (or press <kbd>S</kbd>).
- Put the loupe on a detailed part of the negative — grain, an edge, text.
- Drive focus with <kbd>←</kbd> and <kbd>→</kbd>:
  - plain = **fine**
  - <kbd>Shift</kbd> = **medium**
  - <kbd>Alt</kbd> = **coarse**

✅ **Good:**
- Every press produces a **visible** change. Fine should be small but not
  invisible — measured at 2.6× the frame noise, which is deliberately near the
  threshold of perceptibility.
- The **Now** number peaks as you pass best focus and falls either side. Walk
  focus back and forth and find the peak by the number alone.
- **Best** sticks at the highest value seen. **Reset best** clears it.
- Response feels prompt: ~0.15 s for fine/medium, ~0.5 s for coarse.

❌ **Report if:**
- **Fine steps feel invisible.** This is the single most likely thing to be
  wrong on a different scene — it is exactly the failure I hit and fixed once
  already, and the fix was calibrated against one subject.
- Coarse feels uselessly large, or too slow.
- The sharpness number wanders randomly instead of tracking focus. Note the
  rough range it sits in — on my test frames it read single digits to ~20.

> Sharpness is **relative only**. It depends on subject, exposure and region, so
> compare within one focusing session and never between sessions.

---

## 5. Focus peaking

- Tick **Highlight sharp edges** (or <kbd>P</kbd>).
- Sweep the **Sensitivity** slider across its range.
- Drive focus in and out while it's on.

✅ **Good:**
- Sharp edges are marked **red**. In-focus areas gain marks; defocusing the image
  makes marks *disappear* rather than simply move.
- Peaking works while **Invert** is on, and the marks stay **red** — not cyan.
  (Getting that ordering wrong is an easy bug: mark red, then invert, and every
  mark turns cyan. It's tested, but confirm it by eye.)
- Low sensitivity marks only the sharpest edges; high marks much more.

❌ **Report if:** marks appear cyan, or coverage barely changes across the
sensitivity range, or peaking makes live view noticeably stutter.

---

## 6. The inversion — the main event for v0.3

This is new and the most likely to need tuning on real film.

### 6a. Compare methods

Under **Preview**, the **Method** control has three settings. Switch between
them on the same frame:

| Method | What to expect |
|---|---|
| **Off** | The negative as the camera sees it — orange, inverted tones |
| **Linear** | The old v0.1 flip. On **colour** film this should look clearly **cyan** |
| **Film** | The real pipeline: neutral, properly exposed positive |

✅ **The headline check:** on a **colour negative**, Linear should look obviously
cyan and Film should not. On synthetic fixtures the colour cast dropped ~48×.

Set **Film type** to match what's in the holder — **B&W** or **Colour**. B&W mode
forces a truly neutral result rather than colour with the saturation reduced.

### 6b. Sample the film base

The most important input to the pipeline is the film base — the orange mask.
Automatic estimation assumes the densest part of the picture is near base
density, which is a guess. Measuring the actual rebate is far better.

1. Turn the **loupe** on and click on the **rebate** (the clear strip between
   frames). Use a fairly high zoom so the sample is tight.
2. Press **Sample film base from loupe area**.
3. A message shows the measured RGB.

✅ **Good:** for colour negative, **R > G > B** — that's the orange mask. The
image should visibly improve, or at least change, when you sample a real rebate
versus the automatic guess.

❌ **Report if:** sampling the rebate makes it *worse*, or the values come back
roughly equal on colour film (that would mean it didn't sample the base).

### 6c. Exposure and contrast

- **Exposure** should brighten/darken smoothly.
- **Contrast** should visibly increase midtone separation as you raise it.
  Default 1.65 corresponds to undoing film's ~0.6 gamma.
- **Reset inversion settings** returns to defaults and clears a sampled base.

❌ **Report if:** raising contrast makes the image *flatter* — I had that bug
(the exponent was inverted) and fixed it, so I'd like confirmation on real film.

> **What is honestly missing:** this affects the **preview only**. Captured files
> are saved exactly as the camera wrote them. Inverting downloaded files is the
> next thing I'll build.

---

## 7. Capture

Check once more that the camera is on a **single** image format.

- Note the **Settle delay** (1.5 s default).
- Press <kbd>Space</kbd> or **Capture**.

✅ **Good:**
- A visible pause (the settle delay) before the shutter fires.
- The file appears under **Downloaded** with size and elapsed time.
- The file really exists in `captures/`.
- Fire 3–4 more. Filenames never collide — the original is never overwritten.

❌ **Report if:** it hangs, the file never arrives, or two shots produce one
file. Timing reference: a 14.6 MB HEIF took ~2.9 s including the 1.5 s settle.

> If you shot RAW, expect ~2–3× longer for a 30 MB CR3.

---

## 8. Shut down cleanly

Press **Disconnect**. The camera's rear screen should come back.

Then Ctrl+C the server. If the screen stays blank, power-cycle the camera —
harmless, but tell me, because it means teardown didn't run.

---

## What to send back

Most useful, in order:

1. **Anything in the ❌ lists above.**
2. **A photo or screenshot of the Film-inverted preview** next to the Linear one,
   on colour negative. That single comparison tells me more than any number.
3. **How the focus steps felt** — especially whether fine is perceptible on your
   film. The calibration is scene-dependent and yours is the one that matters.
4. **Any `WARNING` or `ERROR` lines** from the server window.
5. Rough sharpness range you saw while focusing.

Don't send actual scans — they're git-ignored for a reason, and I don't need them.

---

## If something goes wrong

| Symptom | Cause |
|---|---|
| `COMM_PORT_IS_IN_USE` | Another program has the camera, or an old server is still running |
| `SESSION_ALREADY_OPEN` | A previous run crashed. Unplug USB, wait, replug |
| `cameras detected: 0` | Charge-only cable, camera asleep, or a hub |
| No frames, but connected | Camera is on a menu or playback screen |
| Controls do nothing | Stale `app.js` — hard-refresh (Ctrl+Shift+R) |
| Changes not taking effect | Server wasn't restarted |
| Camera screen stays blank after exit | Power-cycle the body; report it |

A camera-free sanity check, any time:

```powershell
.venv\Scripts\python -m cefs.tools.check_camera --focus
```

Reports body, lens, capabilities and live-view rate, and confirms focus drive
actually moves the image. Add `--capture` to fire one test shot.

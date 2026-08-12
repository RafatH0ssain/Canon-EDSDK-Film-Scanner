# Canon EDSDK Film Scanner

Scan film negatives with a Canon camera on a copy stand — live positive preview, remote focus and shutter, over USB.

Colour and black-and-white. Verified on an EOS R7 from Windows and macOS.

## Why not the wireless one

The sibling project, **Canon Smart Film Scan**, does the same job over [CCAPI](https://developercommunity.usa.canon.com/) with no native dependencies and a plain `pip install`. If wireless live view is fast enough on your rig, use that instead.

It wasn't here. At 960×640:

| | fps | period | throughput |
|---|---|---|---|
| Wi-Fi (CCAPI) | 3.98 | 251 ms | 1.23 MB/s |
| **USB (EDSDK)** | **59.79** | **17 ms** | **15.33 MB/s** |

The R7 is Wi-Fi 4 on 2.4 GHz with no Ethernet, and CCAPI has no USB transport — so the only fix was changing SDK. Details in [spike/README.md](spike/README.md).

What it costs: USB cable only, and you obtain EDSDK from Canon yourself.

EDSDK never modifies firmware, so it can't change what the camera's screen shows or bake looks into the files it writes. All processing happens on the computer, which is what film scanning wants anyway.

## Getting EDSDK

⚠️ **Canon licenses EDSDK per developer and it may not be redistributed.** This repo calls the SDK; it will never contain it.

Register with [Canon's Developer Community](https://developercommunity.usa.canon.com/), check your body is on the supported list, and download the SDK **for your platform** — Windows and macOS are separate downloads. Keep it outside the repo, or in `edsdk_sdk/`, which is git-ignored.

Never commit headers, binaries, or Canon's reference text.

> The RAW-develop bundle adds nothing on macOS: its `DPP.framework` is **i386**, which macOS hasn't run since Catalina. RAW goes through LibRaw either way — better here regardless, since the inversion wants linear sensor data rather than Canon's rendering.

## Hardware

- A Canon body EDSDK supports. The **EOS R7** is the reference.
- A real data cable, straight into the machine. USB 2.0 is enough for live view — measured, 8.3 MB/s used of 31 available — it only caps download speed.
- A macro lens near 1:1, copy stand, negative holder, backlight (high-CRI for colour).

An autofocus lens can be driven from the PC; with a manual lens you focus by hand and keep everything else. Detected automatically.

## Running

No SDK, no camera — the default in a fresh clone:

```bash
python -m venv .venv                              # 3.10-3.13, 64-bit
.venv/bin/pip install -r requirements.txt -e .    # Windows: .venv\Scripts\
.venv/bin/python -m cefs.app.server
```

<http://127.0.0.1:8000/>, press Connect, and you get a synthetic negative with grain, mask and rebate. Loopback only — don't expose it, it can fire your shutter.

<kbd>Space</kbd> capture · <kbd>←</kbd><kbd>→</kbd> focus (<kbd>Shift</kbd> medium, <kbd>Alt</kbd> coarse) · <kbd>I</kbd> invert · <kbd>L</kbd> loupe · <kbd>P</kbd> peaking · <kbd>S</kbd> sharpness

### With a real camera

**Turn the camera's Wi-Fi and Bluetooth off first.** A Canon body disables USB data while wireless is on, and the only symptom is `cameras detected: 0` — it still appears on the USB bus, so nothing points at the cause. On macOS also quit Image Capture and Photos, which claim the camera on plug-in.

Copy `config.example.yaml` to `config.yaml`, set `camera.use_mock: false`, and point `edsdk.library_dir` at the folder holding `EDSDK.dll` or `EDSDK.framework`.

macOS needs the framework re-signed before it will load — Canon ships it with the seal already broken, and the refusal (`library load disallowed by system policy`) never mentions signing:

```bash
ditto "/Volumes/Macintosh 1/EDSDK/Framework/EDSDK.framework" edsdk_sdk/Framework/EDSDK.framework
find edsdk_sdk/Framework/EDSDK.framework -name .DS_Store -delete
xattr -cr edsdk_sdk/Framework/EDSDK.framework
codesign --force --deep --sign - edsdk_sdk/Framework/EDSDK.framework
codesign --verify --verbose edsdk_sdk/Framework/EDSDK.framework   # must say "valid on disk"
```

The macOS 13.20.10 build is universal, so Apple Silicon needs no Rosetta. Check with `lipo -archs` if a load fails — that error doesn't mention architecture either.

Then:

```bash
.venv/bin/python -m cefs.tools.check_camera --focus   # --capture fires one shot
.venv/bin/python -m cefs.app.server
```

## Measured on an EOS R7

| | Windows | macOS |
|---|---|---|
| Live view, end to end | 59.79 fps | 27 / 48 / **71.7** at `target_fps` 30 / 60 / 120 |
| Live view, body's own rate | ~60 fps | 96.5 fps |
| Capture download | 26 MB/s | **31 MB/s** |
| Focus per press, fine / medium / coarse | 22 / 141 / 377 ms | 41 / 171 / 512 ms |
| Develop CR3 / HEIF | 4.9 s / 3.8–4.0 s | 5.4 s / 3.9 s |

Capture cycle ~3 s RAW only, ~15 s for RAW+HEIF with a positive from each. Focus steps are 1×1 fine, 2×6 medium, 3×20 coarse. Peaking costs +7.4 ms/frame. Sharpness is a gated Tenengrad, 11.1× separation on real frames.

Not available over EDSDK: camera-side live-view zoom (the software loupe is the only magnification) and electronic shutter (set Shutter mode in the camera menu).

**Live view runs at the taking shutter speed.** With exposure simulation the body emits at roughly the shutter, so 1/15 gives exactly 15 fps and a fast shutter gives 96.5. If focusing feels choppy, check the shutter before suspecting the link.

## Capture formats

| Format | Decoder | Positive |
|---|---|---|
| RAW `.CR3`/`.CR2`, incl. CRAW | LibRaw — EDSDK cannot decode CR3, measured | 16-bit TIFF |
| HEIF `.HIF` | pillow-heif, 10-bit, PQ transfer read from the file | 16-bit TIFF |
| JPEG | OpenCV | JPEG |

Chosen by the file, not a setting. TIFF defaults to deflate — 2.4–2.6× lossless on real 32 MP positives, where LZW manages 1.1–1.2×.

Keeping the RAW to develop elsewhere? Set `capture.develop_positives: false` and the camera to RAW only: one ~35 MB `.CR3` per shot, and the preview still inverts.

## Rolls and naming

```
captures/Roll014/Roll014_Frame07.CR3
                 Roll014_Frame07-positive.tif
                 roll.json
```

Fields are `{roll}` `{frame}` `{original}` `{date}` `{time}` `{stock}`; `{frame:02d}` pads and `/` makes a folder. Leave the template empty to keep the camera's own names.

**A frame is one shutter release, not one file** — RAW+HEIF sends two files sharing one frame number. `roll.json` records stock, developer, date and notes, rewritten after every capture.

## Threading

One thread owns the SDK; everything else queues a command and waits. *Which* thread differs:

- **Windows** — any single-threaded-apartment thread running a message pump. Events arrive through COM.
- **macOS** — the **main thread**. `EdsGetCameraList` returns OK and *zero cameras* from anywhere else, and a worker hangs in `EdsTerminateSDK`. Not a run-loop problem. So the web server runs on a worker instead; see `edsdk/mainthread.py`. Events come from polling `EdsGetEvent`.

Live view is polled, so it keeps working when event dispatch is broken — the first thing that fails is a capture that never completes. Don't read a working preview as proof the threading is right.

## Limitations

- Corner-by-corner alignment isn't built; the per-region sharpness it needs is.
- No batch re-develop of a roll already scanned.
- Linux is refused outright rather than half-working.

## Layout

```
src/cefs/
├── backend.py       The contract both backends implement
├── config.py        Defaults, then config.yaml, then CEFS_* env vars
├── naming.py        Template -> Roll014/Roll014_Frame07.CR3
├── sidecar.py       The per-roll roll.json
├── edsdk/           ctypes bindings and the thread that owns the SDK
├── processing/      Pure numpy. No SDK, no UI.
├── app/             Local web server + browser UI
├── mock/            Camera-free backend
└── tools/           check_camera, against real hardware
tests/               213 tests, none needing a camera or the SDK
```

## Contributing

Keep the layers decoupled and add tests for anything in `processing/`.

**Never commit** EDSDK headers or binaries, Canon's reference text, camera serial numbers, or your own scans.

Got another body working? A short report with the latency numbers tells the next person whether this is worth it on theirs.

## Licence

Permissive open source, covering **this project's code only**. Canon's EDSDK and its documentation are Canon's property under separate terms.

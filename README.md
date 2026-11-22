# Canon EDSDK Film Scanner

[![tests](https://github.com/RafatH0ssain/Canon-EDSDK-Film-Scanner/actions/workflows/tests.yml/badge.svg)](https://github.com/RafatH0ssain/Canon-EDSDK-Film-Scanner/actions/workflows/tests.yml)

I scan my film negatives with a Canon R7 on a copy stand. This controls the camera over USB so I
can focus and shoot from my laptop, with the live view already inverted to a positive.

![Live view of an inverted B&W negative with focus peaking](LiveInversionFocusPeaking.png)

There's also a [Wi-Fi version](https://github.com/RafatH0ssain/Canon-Smart-Film-Scanner), but it
only got about 4 fps on my camera, so I made this one. After scanning, I use
[Film Scan Darkroom](https://github.com/RafatH0ssain/Film-Scan-Darkroom) to crop and invert the roll.

## Running it

You need Canon's EDSDK from their [developer site](https://developercommunity.usa.canon.com/).
Canon doesn't allow sharing it, so put it in `edsdk_sdk/` (git-ignored) and point
`edsdk.library_dir` in `config.yaml` at it.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -e .
.venv/bin/python -m cefs.app.server
```

Then open http://127.0.0.1:8000. Turn off the camera's Wi-Fi first or it won't show up over USB.

## Licence

MIT. Packaged builds are GPL v2 because of pillow-heif, see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

# Third-party notices

The packaged app bundles the native libraries below. Running from a source
checkout distributes none of them — pip fetches them from PyPI — so this
matters only if you hand someone a built `.app` or `.exe`.

This list is checked by `packaging/build_app.py`, which refuses to finish if
the build contains a native library not named here. That keeps it honest when
a dependency quietly adds one.

> **Not legal advice.** Verify the terms yourself before distributing binaries.
> Licences are stated as understood at the time of writing and can change
> between releases of the upstream projects.

## Copyleft — read these before distributing a binary

| Library | Licence | Comes from | Why it is here |
|---|---|---|---|
| **libx265** | **GPL v2 or later** (a commercial licence is available from MulticoreWare) | pillow-heif → libheif | HEIF. `libheif` hard-links it, so it is present even though this app only ever *decodes* HEIF and never encodes it. It cannot be removed without losing HEIF support. |
| libheif | LGPL v3 | pillow-heif | Reads the `.HIF` files an EOS body writes in HDR PQ mode |
| libde265 | LGPL v3 | pillow-heif | HEVC decoding inside HEIF |
| libraw | LGPL v2.1 or CDDL 1.0 | rawpy | Decodes CR3/CR2. EDSDK cannot — measured, see `src/cefs/edsdk/decode.py` |

Distributing a binary containing **libx265** brings GPL obligations, including
providing the corresponding source or a written offer for it. The LGPL entries
have lighter obligations, generally notice plus the ability to relink.

## Permissive

| Library | Licence | Comes from |
|---|---|---|
| libavif, libsharpyuv | BSD-2-Clause | Pillow |
| libwebp, libwebpdemux, libwebpmux | BSD-3-Clause | Pillow |
| libjpeg (libjpeg-turbo) | IJG / BSD-3-Clause / zlib | Pillow |
| libtiff | libtiff licence (BSD-style) | Pillow |
| liblcms2 | MIT | Pillow |
| libopenjp2 | BSD-2-Clause | Pillow |
| libjasper | JasPer License (MIT-style) | Pillow |
| libz (zlib-ng) | zlib | Python, Pillow |
| liblzma | 0BSD / public domain | Python |
| libmpdec | BSD-2-Clause | Python |
| libcrypto, libssl (OpenSSL 3) | Apache-2.0 | Python |
| libXau, libxcb | MIT | X11 client libraries |
| CPython runtime | PSF License | the interpreter itself |

Pure-Python dependencies — numpy (BSD-3), Pillow (MIT-CMU), tifffile (BSD-3),
rawpy (MIT), FastAPI, Starlette, uvicorn, pydantic, PyYAML — are permissively
licensed and carry their own notices in the bundle.

## What is deliberately absent

**OpenCV.** Its wheel bundled a GPL-licensed FFmpeg — `libx264`, `libx265`,
`libaribb24` — plus `libaom`, `SVT-AV1`, `SDL2`, `libass` and X11: roughly
49 MB of video codecs for a project that decodes JPEG, resizes, and runs a
Scharr gradient. Replacing it with Pillow, tifffile and numpy took the app from
176 MB to 66 MB and removed every one of those.

**Canon's EDSDK.** Licensed per developer and not redistributable. The app ships
without it; you supply your own copy. The build fails if any of it is found in
the output.

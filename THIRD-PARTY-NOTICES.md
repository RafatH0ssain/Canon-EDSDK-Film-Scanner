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

## Licence of the packaged binary: **GPL v2**

The source in this repository stays MIT. **A built `.app` or `.exe` does not.**

It bundles pillow-heif's binary wheel, and upstream states the position
plainly in `licenses/pillow-heif-BUNDLED-LICENSES.txt`:

> License for "pillow-heif" binary wheels: GPLv2, due to base library licenses.

That comes from **libx265**, which `libheif` hard-links even to *decode* HEIF.
HEIF is not optional here — a Canon body writes `.HIF` in HDR PQ mode and
reading it is a feature — so the library stays and the binary is GPL v2.

MIT is GPL-compatible, so there is no contradiction: the project's own code
remains MIT and can be used under those terms from source. Only the combined
binary is GPL.

### What that requires when you hand someone a build

- **Ship this file and `licenses/`.** Both are inside the bundle already.
- **Offer the corresponding source.** For the GPL and LGPL components that is
  upstream, pinned to the versions in the wheel — see the table below. For this
  project's own code it is the public repository.
- **Add no further restrictions** on redistribution.

**Written offer:** for three years from the date you received a binary of this
project, the distributor will supply the complete corresponding source for the
GPL and LGPL components below, on request, at no more than the cost of
distribution. In practice each is a public URL, listed here and in
`licenses/pillow-heif-BUNDLED-LICENSES.txt`.

| Library | Licence | Source |
|---|---|---|
| **libx265** | **GPL v2** | https://bitbucket.org/multicoreware/x265_git |
| libheif | LGPL v3 | https://github.com/strukturag/libheif |
| libde265 | LGPL v3 | https://github.com/strukturag/libde265 |
| libraw | LGPL v2.1 / CDDL 1.0 | https://www.libraw.org/download — full text in `licenses/LibRaw-LICENSE.txt` |

Exact pinned versions are in `licenses/pillow-heif-BUNDLED-LICENSES.txt`, copied
verbatim from the wheel this was built against.

If you would rather ship something permissive, the sibling **Canon Smart Film
Scanner** has no copyleft components at all — it has no HEIF path, so no
libx265.

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

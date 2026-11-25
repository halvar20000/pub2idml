# Third-party software

pub2idml's own code (`pub2idml.py`, `pub2json/`, `tests/`) is under the MIT
licence (see `LICENSE`). It does not contain code copied from other projects.

The `pub2json` helper links against the following libraries. The release
zips for Windows and macOS ship them unmodified as separate library files
(`.dll` / `.dylib`) next to the programs.

| Library | Use | Licence | Source code |
|---|---|---|---|
| libmspub | reads Microsoft Publisher files | MPL-2.0 | https://git.libreoffice.org/libmspub |
| librevenge | interface libmspub reports through | MPL-2.0 (or LGPL-2.1+) | https://sourceforge.net/projects/libwpd/files/librevenge/ |
| ICU | text encodings (dependency of libmspub) | Unicode License v3 | https://github.com/unicode-org/icu |
| zlib | compression | zlib License | https://zlib.net/ |
| GCC runtime (libstdc++, libgcc), winpthreads (Windows only) | C++ runtime | GPL-3.0 with GCC Runtime Library Exception / MIT+BSD | https://gcc.gnu.org/ , https://www.mingw-w64.org/ |

The MPL-2.0 is a file-level copyleft licence: the libraries above may be
distributed with MIT-licensed software. Their source code is available at the
links above; pub2idml does not modify them. The full MPL-2.0 text is at
https://www.mozilla.org/en-US/MPL/2.0/.

The programs `pub2idml`, `pub2idml.exe` and `pub2idml-app.exe` are built with
PyInstaller, which bundles the Python runtime (PSF License,
https://docs.python.org/3/license.html) and, for the windows, Tcl/Tk
(Tcl/Tk License, BSD-style, https://www.tcl.tk/software/tcltk/license.html).

The Linux AppImage is built with linuxdeploy and appimagetool; its runtime is
under the MIT licence (https://github.com/AppImage/type2-runtime). The macOS
app is an AppleScript droplet created with Apple's `osacompile`.

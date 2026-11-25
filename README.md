# pub2idml

**[English](#english) · [Deutsch](#deutsch)**

Convert Microsoft Publisher files (`.pub`) to IDML — without Publisher and without LibreOffice.
IDML opens in DesignCraft, Adobe InDesign, Affinity Publisher and others.

```
.pub ──(pub2json: libmspub)──▶ JSON events ──(pub2idml.py)──▶ .idml
```

---

## English

`pub2json` is a small C++ program built on **libmspub**, the library LibreOffice uses to read
Publisher files (about 1 MB instead of 300 MB of LibreOffice). It reports every page, shape, text
run and image as one JSON line; `pub2idml.py` turns that stream into an IDML package.

### Ready-made programs (Windows, macOS)

Download from **[Releases](https://github.com/halvar20000/pub2idml/releases/latest)**:

- `pub2idml-windows-x64.zip`
- `pub2idml-macos-arm64.zip` (Apple Silicon, M1–M4)
- `pub2idml-macos-x86_64.zip` (Intel Mac)

Unzip and keep the folder together (`pub2idml` looks for `pub2json` next to itself).
Python and LibreOffice are **not** required.

**Windows:** drag one or more `.pub` files onto `pub2idml.exe` — the `.idml` is written next to
each `.pub`. Or from a command prompt:

```bat
pub2idml.exe Label.pub
pub2idml.exe --batch C:\Data\Publisher -o C:\Data\IDML
```

**macOS** (Terminal):

```bash
xattr -dr com.apple.quarantine pub2idml-macos-arm64   # once: the programs are not notarized by Apple
./pub2idml-macos-arm64/pub2idml Label.pub
```

### Building from source

Requirements: C++ compiler, CMake, pkg-config, libmspub (with development files), Python 3.9+.

| System | Install libmspub |
|---|---|
| Debian/Ubuntu | `sudo apt install libmspub-dev librevenge-dev cmake pkg-config g++` |
| macOS | `brew install libmspub cmake pkg-config` |
| Windows | MSYS2 UCRT64: `pacman -S mingw-w64-ucrt-x86_64-{gcc,cmake,ninja,pkgconf,libmspub}` |

```bash
cmake -S pub2json -B pub2json/build -DCMAKE_BUILD_TYPE=Release
cmake --build pub2json/build
python3 pub2idml.py Label.pub -v
```

`pub2idml.py` finds `pub2json` in `pub2json/build/`, next to itself, on the `PATH`, or via the
`PUB2JSON` environment variable.

For debugging, save the intermediate stream and convert it separately:

```bash
pub2json Label.pub Label.jsonl
python3 pub2idml.py Label.jsonl
```

Tests: `python3 -m unittest discover -s tests`

### What is converted

| Element | Status |
|---|---|
| Pages, page size, multiple pages | ✅ |
| Text frames: font, size, bold/italic, underline, colour, alignment, insets, vertical alignment | ✅ |
| Rotated text frames | ✅ |
| Lines, polygons, polylines, rectangles, ellipses | ✅ |
| Bézier paths with arcs, compound paths | ✅ |
| Groups | ✅ (`--flatten-groups` ungroups them) |
| Fill colour, stroke colour, stroke weight, dashed lines | ✅ |
| Images / bitmap fills (embedded: PNG, JPEG, GIF, BMP) | ✅ |
| Gradients | ⚠️ approximated by a solid colour |
| Tables, shadows, WordArt effects, linked text frames | ❌ not yet (warning) |

Missing fonts (e.g. Calibri) are substituted by the target application; font names are kept.

### Tested

A 6-page A3 label drawing (~960 objects) opened and rendered in DesignCraft 0.21.1: visually
practically identical to how LibreOffice displays the original file.

### Licence

MIT (see `LICENSE`). Bundled libraries (libmspub, librevenge, ICU, …): see
`THIRD_PARTY_NOTICES.md`.

---

## Deutsch

`pub2json` ist ein kleines C++-Programm auf Basis von **libmspub** – derselben Bibliothek, mit der
LibreOffice Publisher-Dateien liest (rund 1 MB statt 300 MB LibreOffice). Es meldet jede Seite,
Form, jeden Text und jedes Bild als eine JSON-Zeile; `pub2idml.py` macht daraus ein IDML-Paket.

### Fertige Programme (Windows, macOS)

Download unter **[Releases](https://github.com/halvar20000/pub2idml/releases/latest)**:

- `pub2idml-windows-x64.zip`
- `pub2idml-macos-arm64.zip` (Apple Silicon, M1–M4)
- `pub2idml-macos-x86_64.zip` (Intel-Mac)

Zip entpacken, Ordner zusammenlassen (`pub2idml` sucht `pub2json` daneben). Python oder
LibreOffice sind **nicht** nötig.

**Windows:** eine oder mehrere `.pub`-Dateien einfach auf `pub2idml.exe` ziehen – die `.idml`
landet neben der jeweiligen `.pub`. Oder in der Eingabeaufforderung:

```bat
pub2idml.exe Etikett.pub
pub2idml.exe --batch C:\Daten\Publisher -o C:\Daten\IDML
```

**macOS** (Terminal):

```bash
xattr -dr com.apple.quarantine pub2idml-macos-arm64   # einmalig: Programme sind nicht von Apple notarisiert
./pub2idml-macos-arm64/pub2idml Etikett.pub
```

### Selbst bauen

Voraussetzungen: C++-Compiler, CMake, pkg-config, libmspub (mit Entwicklerdateien), Python 3.9+.

| System | libmspub installieren |
|---|---|
| Debian/Ubuntu | `sudo apt install libmspub-dev librevenge-dev cmake pkg-config g++` |
| macOS | `brew install libmspub cmake pkg-config` |
| Windows | MSYS2 UCRT64: `pacman -S mingw-w64-ucrt-x86_64-{gcc,cmake,ninja,pkgconf,libmspub}` |

```bash
cmake -S pub2json -B pub2json/build -DCMAKE_BUILD_TYPE=Release
cmake --build pub2json/build
python3 pub2idml.py Etikett.pub -v
```

`pub2idml.py` findet `pub2json` in `pub2json/build/`, neben sich selbst, im `PATH` oder über die
Umgebungsvariable `PUB2JSON`.

Zur Fehlersuche lässt sich der Zwischenschritt speichern und einzeln umwandeln:

```bash
pub2json Etikett.pub Etikett.jsonl
python3 pub2idml.py Etikett.jsonl
```

Tests: `python3 -m unittest discover -s tests`

### Was übertragen wird

| Element | Status |
|---|---|
| Seiten, Seitengröße, mehrere Seiten | ✅ |
| Textrahmen: Schrift, Größe, fett/kursiv, unterstrichen, Farbe, Ausrichtung, Innenabstand, vertikale Ausrichtung | ✅ |
| Gedrehte Textrahmen | ✅ |
| Linien, Polygone, Polylinien, Rechtecke, Ellipsen | ✅ |
| Bézier-Pfade mit Bögen, zusammengesetzte Pfade | ✅ |
| Gruppen | ✅ (`--flatten-groups` löst sie auf) |
| Füllfarben, Konturfarbe, Konturstärke, gestrichelte Linien | ✅ |
| Bilder / Bitmap-Füllungen (eingebettet: PNG, JPEG, GIF, BMP) | ✅ |
| Verläufe | ⚠️ als Vollfarbe angenähert |
| Tabellen, Schatten, WordArt-Effekte, verkettete Textrahmen | ❌ noch nicht (Warnung) |

Fehlende Schriften (z. B. Calibri) ersetzt das Zielprogramm; die Schriftnamen bleiben erhalten.

### Getestet

Eine 6-seitige A3-Etikettenzeichnung (~960 Objekte) in DesignCraft 0.21.1 geöffnet und gerendert:
visuell praktisch identisch mit der Darstellung der Originaldatei in LibreOffice.

### Lizenz

MIT (siehe `LICENSE`). Mitgelieferte Bibliotheken (libmspub, librevenge, ICU, …): siehe
`THIRD_PARTY_NOTICES.md`.

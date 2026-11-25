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

### Ready-made programs (Windows, macOS, Linux)

Download from **[Releases](https://github.com/halvar20000/pub2idml/releases/latest)**. Python and
LibreOffice are **not** required. All three work the same way: drop `.pub` files or a folder on the
icon (or start the program and choose files); the `.idml` files are written next to the originals
and a window shows the result.

| System | File |
|---|---|
| Windows 10/11 | `pub2idml-windows-x64.zip` |
| Mac with Apple Silicon (M1–M4) | `pub2idml-app-macos-arm64.zip` |
| Mac with Intel processor | `pub2idml-app-macos-x86_64.zip` |
| Linux (x86_64) | `pub2idml-x86_64.AppImage` |
| macOS command line only | `pub2idml-macos-arm64.zip` / `pub2idml-macos-x86_64.zip` |

**Windows**

1. Unzip `pub2idml-windows-x64.zip` to a permanent place, e.g. `C:\Tools\pub2idml`
   (keep all files together, including the `.dll` files).
2. Double-click **`Senden-an-einrichten.cmd`** once. It adds **Send to → pub2idml** to the right-click
   menu and a **pub2idml** icon on the desktop (no administrator rights; undo with
   `Senden-an-entfernen.cmd`).
3. Right-click `.pub` files → **Send to → pub2idml**, or drop files/folders on the desktop icon or on
   `pub2idml-app.exe`. Double-clicking the app opens a file chooser.

The first start may show *"Windows protected your PC"* because the program is not commercially
signed: click **More info → Run anyway**. `pub2idml.exe` is the command-line version:

```bat
pub2idml.exe Label.pub
pub2idml.exe --batch C:\Data\Publisher -o C:\Data\IDML
```

**Mac**

Unzip, move `pub2idml.app` to *Applications*, then drag `.pub` files or a folder onto the app icon
(in Finder or the Dock), or double-click the app and choose files. The app is not notarized by
Apple, so the first time macOS refuses to open it: open *System Settings → Privacy & Security* and
click **Open Anyway** – or once in Terminal:

```bash
xattr -dr com.apple.quarantine /Applications/pub2idml.app
```

The command-line zips contain `pub2idml`, `pub2json` and `libs/`; keep them together and run
`./pub2idml Label.pub` (same `xattr` command on the unzipped folder first).

**Linux**

An AppImage is a single file that runs on most distributions without installation:

```bash
chmod +x pub2idml-x86_64.AppImage
```

Double-click it to choose files, or drop `.pub` files on it (or on a launcher created from it, e.g.
with *Gear Lever* or *AppImageLauncher*). From a terminal it is the command-line tool:

```bash
./pub2idml-x86_64.AppImage Label.pub
```

If it does not start, install FUSE 2 (`sudo apt install libfuse2` on Ubuntu/Debian).

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

### Fertige Programme (Windows, macOS, Linux)

Download unter **[Releases](https://github.com/halvar20000/pub2idml/releases/latest)**. Python oder
LibreOffice sind **nicht** nötig. Alle drei funktionieren gleich: `.pub`-Dateien oder einen Ordner
auf das Symbol ziehen (oder das Programm starten und Dateien auswählen); die `.idml`-Dateien landen
neben den Originalen, ein Fenster zeigt das Ergebnis.

| System | Datei |
|---|---|
| Windows 10/11 | `pub2idml-windows-x64.zip` |
| Mac mit Apple Silicon (M1–M4) | `pub2idml-app-macos-arm64.zip` |
| Mac mit Intel-Prozessor | `pub2idml-app-macos-x86_64.zip` |
| Linux (x86_64) | `pub2idml-x86_64.AppImage` |
| nur macOS-Kommandozeile | `pub2idml-macos-arm64.zip` / `pub2idml-macos-x86_64.zip` |

**Windows**

1. `pub2idml-windows-x64.zip` an einen festen Ort entpacken, z. B. `C:\Tools\pub2idml`
   (alle Dateien zusammenlassen, auch die `.dll`-Dateien).
2. Einmal **`Senden-an-einrichten.cmd`** doppelklicken. Das legt im Rechtsklick-Menü
   **Senden an → pub2idml** an und ein **pub2idml**-Symbol auf dem Desktop (keine Admin-Rechte nötig;
   rückgängig mit `Senden-an-entfernen.cmd`).
3. Rechtsklick auf `.pub`-Dateien → **Senden an → pub2idml**, oder Dateien/Ordner auf das
   Desktop-Symbol bzw. auf `pub2idml-app.exe` ziehen. Doppelklick auf die App öffnet eine Dateiauswahl.

Beim ersten Start erscheint evtl. *„Der Computer wurde durch Windows geschützt“*, weil das Programm
nicht kostenpflichtig signiert ist: **Weitere Informationen → Trotzdem ausführen**.
`pub2idml.exe` ist die Kommandozeilen-Version:

```bat
pub2idml.exe Etikett.pub
pub2idml.exe --batch C:\Daten\Publisher -o C:\Daten\IDML
```

**Mac**

Entpacken, `pub2idml.app` in den Ordner *Programme* ziehen, dann `.pub`-Dateien oder einen Ordner auf
das App-Symbol ziehen (im Finder oder im Dock) – oder die App doppelklicken und Dateien auswählen.
Die App ist nicht von Apple notarisiert, deshalb verweigert macOS beim ersten Mal das Öffnen:
*Systemeinstellungen → Datenschutz & Sicherheit* öffnen und **Trotzdem öffnen** klicken – oder
einmalig im Terminal:

```bash
xattr -dr com.apple.quarantine /Applications/pub2idml.app
```

Die Kommandozeilen-Zips enthalten `pub2idml`, `pub2json` und `libs/`; zusammenlassen und
`./pub2idml Etikett.pub` aufrufen (vorher denselben `xattr`-Befehl auf den entpackten Ordner).

**Linux**

Ein AppImage ist eine einzelne Datei, die ohne Installation auf den meisten Distributionen läuft:

```bash
chmod +x pub2idml-x86_64.AppImage
```

Doppelklick öffnet eine Dateiauswahl; `.pub`-Dateien lassen sich auf die Datei ziehen (oder auf einen
Starter, den z. B. *Gear Lever* oder *AppImageLauncher* anlegt). Im Terminal ist es das
Kommandozeilen-Programm:

```bash
./pub2idml-x86_64.AppImage Etikett.pub
```

Startet es nicht, FUSE 2 installieren (`sudo apt install libfuse2` unter Ubuntu/Debian).

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

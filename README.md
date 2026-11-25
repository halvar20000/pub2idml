# pub2idml

Wandelt Microsoft-Publisher-Dateien (`.pub`) in IDML um – ohne Publisher und ohne LibreOffice.
IDML öffnen DesignCraft, Adobe InDesign, Affinity Publisher und andere.

```
.pub ──(pub2json: libmspub)──▶ JSON-Ereignisse ──(pub2idml.py)──▶ .idml
```

`pub2json` ist ein kleines C++-Programm auf Basis von **libmspub** – derselben
Bibliothek, mit der LibreOffice Publisher-Dateien liest (1 MB statt 300 MB LibreOffice).
Es meldet jede Seite, Form, jeden Text und jedes Bild als eine JSON-Zeile;
`pub2idml.py` macht daraus ein IDML-Paket.

## Fertige Programme (Windows, macOS)

Unter **Actions → build → Artifacts** (oder bei Releases unter **Releases**) liegen:

- `pub2idml-windows-x64.zip`
- `pub2idml-macos-arm64.zip` (Apple Silicon, M1–M4)
- `pub2idml-macos-x86_64.zip` (Intel-Mac)

Zip entpacken, Ordner zusammenlassen (`pub2idml` sucht `pub2json` daneben). Python oder LibreOffice
sind **nicht** nötig.

**Windows:** eine oder mehrere `.pub`-Dateien einfach auf `pub2idml.exe` ziehen – die `.idml`
landet neben der `.pub`. Oder in der Eingabeaufforderung:

```bat
pub2idml.exe Etikett.pub
pub2idml.exe --batch C:\Daten\Publisher -o C:\Daten\IDML
```

**macOS** (Terminal):

```bash
xattr -dr com.apple.quarantine pub2idml-macos-arm64   # einmalig: Programme sind nicht von Apple notarisiert
./pub2idml-macos-arm64/pub2idml Etikett.pub
```

## Selbst bauen

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

## Was übertragen wird

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

## Getestet

Eine 6-seitige A3-Etikettenzeichnung (~960 Objekte) in DesignCraft 0.21.1 geöffnet und gerendert:
visuell praktisch identisch mit der Darstellung der Originaldatei in LibreOffice.

## Lizenz

MIT (siehe `LICENSE`). Mitgelieferte Bibliotheken (libmspub, librevenge, ICU, …):
siehe `THIRD_PARTY_NOTICES.md`.

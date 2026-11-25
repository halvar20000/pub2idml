# pub2idml

Wandelt Microsoft-Publisher-Dateien (`.pub`) in IDML um, ohne Publisher.
IDML öffnen DesignCraft, Adobe InDesign, Affinity Publisher und andere.

```
.pub ──(LibreOffice headless, Import-Filter)──▶ .odg ──(pub2idml.py)──▶ .idml
```

## Voraussetzungen

- Python 3.9+ (nur Standardbibliothek)
- LibreOffice (kostenlos, https://www.libreoffice.org/), wird automatisch gefunden
  (Linux `soffice`, macOS `/Applications/LibreOffice.app`, Windows `C:\Program Files\LibreOffice`)

## Benutzung

```bash
python3 pub2idml.py Etikett.pub                 # -> Etikett.idml
python3 pub2idml.py Etikett.pub -o out.idml -v
python3 pub2idml.py --batch ./pub-ordner -o ./idml-ordner   # alle .pub eines Ordners
python3 pub2idml.py Datei.odg                   # ODG direkt (LibreOffice-Schritt entfällt)
```

Option `--flatten-groups` löst Gruppen auf (falls ein Zielprogramm Gruppen schlecht verarbeitet).

## Was übertragen wird

| Element | Status |
|---|---|
| Seiten, Seitengröße, mehrere Seiten | ✅ |
| Textrahmen: Schrift, Größe, fett/kursiv, unterstrichen, Farbe, Ausrichtung, Innenabstand, vertikale Ausrichtung | ✅ |
| Gedrehte Textrahmen | ✅ |
| Linien, Polygone, Polylinien, Rechtecke, Ellipsen | ✅ |
| Bézier-Pfade inkl. zusammengesetzter Pfade (SVG-Pfade aller Befehle) | ✅ |
| Gruppen | ✅ |
| Füllfarben, Konturfarbe, Konturstärke, gestrichelte Linien | ✅ |
| Bilder / Bitmap-Füllungen (eingebettet) | ✅ |
| Verläufe | ⚠️ als Vollfarbe angenähert |
| AutoFormen (custom-shape) | ⚠️ als umschließendes Rechteck |
| Tabellen, Schatten, WordArt-Effekte, verkettete Textrahmen | ❌ noch nicht |

Fehlende Schriften (z. B. Calibri) werden vom Zielprogramm ersetzt; Schriftnamen bleiben erhalten.

## Getestet

`04_publication__CHINA_Label_blau_W14.pub` (6 A3-Seiten, ~890 Objekte):
in DesignCraft 0.21.1 geöffnet und gerendert (`designcraft-cli run --in X.idml --all-pages DIR`),
Ergebnis visuell praktisch identisch mit dem LibreOffice-Rendering der Originaldatei.

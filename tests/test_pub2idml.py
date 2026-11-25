"""Tests for pub2idml: a synthetic pub2json event stream -> IDML.

The stream is built in code (no customer files in the repository). It covers
the event types libmspub emits: pages, groups (layers), polygons, paths with
curves and arcs, dashed lines, rotated text boxes and bitmap fills.

Run:  python -m unittest discover -s tests
"""
import base64
import json
import math
import os
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pub2idml  # noqa: E402


def inch(v):
    return {"v": v, "u": "in", "s": f"{v}in"}


def tiny_png(w=4, h=3):
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def pt(x, y, act="L", **extra):
    d = {"librevenge:path-action": act, "svg:x": inch(x), "svg:y": inch(y)}
    d.update(extra)
    return d


EVENTS = [
    {"op": "startDocument", "p": {}},
    {"op": "startPage", "p": {"svg:width": inch(8.5), "svg:height": inch(11)}},
    # group with a filled polygon and a text box
    {"op": "startLayer", "p": {}},
    {"op": "setStyle", "p": {"draw:fill": "solid", "draw:fill-color": "#ffc000", "draw:stroke": "none"}},
    {"op": "drawPolygon", "p": {"svg:points": [
        {"svg:x": inch(1), "svg:y": inch(1)}, {"svg:x": inch(3), "svg:y": inch(1)},
        {"svg:x": inch(3), "svg:y": inch(2)}, {"svg:x": inch(1), "svg:y": inch(2)},
        {"svg:x": inch(1), "svg:y": inch(1)}]}},
    {"op": "setStyle", "p": {"draw:fill": "none", "draw:stroke": "solid", "svg:stroke-color": "#ff0000",
                             "svg:stroke-width": inch(0.03)}},
    {"op": "startTextObject", "p": {"svg:x": inch(1), "svg:y": inch(1), "svg:width": inch(2),
                                    "svg:height": inch(1), "fo:padding-top": inch(0.04),
                                    "draw:textarea-vertical-align": "middle"}},
    {"op": "openParagraph", "p": {"fo:text-align": "center"}},
    {"op": "openSpan", "p": {"style:font-name": "Calibri", "fo:font-size": inch(10 / 72),
                             "fo:font-weight": "bold", "fo:color": "#0000ff"}},
    {"op": "insertText", "t": "Hallo & <Welt>"},
    {"op": "insertSpace"},
    {"op": "insertText", "t": "äöü\r"},
    {"op": "closeSpan"},
    {"op": "closeParagraph"},
    {"op": "endTextObject"},
    {"op": "endLayer"},
    # path with a cubic curve and an arc, dashed stroke
    {"op": "setStyle", "p": {"draw:fill": "none", "draw:stroke": "dash", "svg:stroke-color": "#000000",
                             "svg:stroke-width": inch(0.01), "draw:dots1": {"v": 1, "u": "", "s": "1"},
                             "draw:dots1-length": inch(0.03), "draw:distance": inch(0.02)}},
    {"op": "drawPath", "p": {"svg:d": [
        pt(4, 4, "M"),
        pt(5, 4, "C", **{"svg:x1": inch(4.3), "svg:y1": inch(3.5), "svg:x2": inch(4.7), "svg:y2": inch(3.5)}),
        pt(7, 4, "A", **{"svg:rx": inch(1), "svg:ry": inch(1), "librevenge:rotate": inch(0),
                         "librevenge:sweep": inch(1)}),
    ]}},
    # rotated text box (90 degrees counter-clockwise)
    {"op": "startTextObject", "p": {"svg:x": inch(2), "svg:y": inch(6), "svg:width": inch(1),
                                    "svg:height": inch(0.25), "librevenge:rotate": inch(-90)}},
    {"op": "openParagraph", "p": {}},
    {"op": "openSpan", "p": {"fo:font-size": inch(12 / 72)}},
    {"op": "insertText", "t": "20,0mm"},
    {"op": "closeSpan"},
    {"op": "closeParagraph"},
    {"op": "endTextObject"},
    # empty text box: must be dropped
    {"op": "startTextObject", "p": {"svg:x": inch(0), "svg:y": inch(0), "svg:width": inch(1), "svg:height": inch(1)}},
    {"op": "openParagraph", "p": {}},
    {"op": "insertText", "t": "\r"},
    {"op": "closeParagraph"},
    {"op": "endTextObject"},
    # bitmap fill
    {"op": "setStyle", "p": {"draw:fill": "bitmap", "draw:stroke": "none", "style:repeat": "stretch",
                             "librevenge:mime-type": "image/png",
                             "draw:fill-image": base64.b64encode(tiny_png()).decode()}},
    {"op": "drawPolygon", "p": {"svg:points": [
        {"svg:x": inch(5), "svg:y": inch(8)}, {"svg:x": inch(7), "svg:y": inch(8)},
        {"svg:x": inch(7), "svg:y": inch(9.5)}, {"svg:x": inch(5), "svg:y": inch(9.5)}]}},
    {"op": "endPage"},
    {"op": "startPage", "p": {"svg:width": inch(8.5), "svg:height": inch(11)}},
    {"op": "setStyle", "p": {"draw:fill": "none", "draw:stroke": "solid", "svg:stroke-color": "#00ff00",
                             "svg:stroke-width": inch(0.02)}},
    {"op": "drawPath", "p": {"svg:d": [pt(1, 1, "M"), pt(2, 2)]}},
    {"op": "endPage"},
    {"op": "endDocument"},
]


class ConvertTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        src = os.path.join(cls.tmp.name, "sample.jsonl")
        with open(src, "w", encoding="utf-8") as f:
            for e in EVENTS:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        cls.out = os.path.join(cls.tmp.name, "sample.idml")
        cls.warnings = pub2idml.convert(src, cls.out)
        cls.zip = zipfile.ZipFile(cls.out)
        cls.spreads = [ET.fromstring(cls.zip.read(n)) for n in sorted(cls.zip.namelist()) if n.startswith("Spreads/")]
        cls.stories = [ET.fromstring(cls.zip.read(n)) for n in cls.zip.namelist() if n.startswith("Stories/")]

    @classmethod
    def tearDownClass(cls):
        cls.zip.close()
        cls.tmp.cleanup()

    def items(self, tag, spread=0):
        return list(self.spreads[spread].iter(tag))

    def test_package_layout(self):
        first = self.zip.infolist()[0]
        self.assertEqual(first.filename, "mimetype")
        self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
        for n in self.zip.namelist():
            if n.endswith(".xml"):
                ET.fromstring(self.zip.read(n))  # well-formed

    def test_pages(self):
        self.assertEqual(len(self.spreads), 2)
        page = next(self.spreads[0].iter("Page"))
        self.assertEqual(page.get("GeometricBounds"), "0 0 792 612")

    def test_group_kept(self):
        groups = self.items("Group")
        self.assertEqual(len(groups), 1)
        self.assertEqual({c.tag for c in groups[0]}, {"Polygon", "TextFrame"})

    def test_text(self):
        texts = ["".join(c.text or "" for c in s.iter("Content")) for s in self.stories]
        self.assertIn("Hallo & <Welt> äöü", texts)
        self.assertIn("20,0mm", texts)
        self.assertEqual(len(texts), 2, "empty text box should be dropped")
        bold = [c for s in self.stories for c in s.iter("CharacterStyleRange") if c.get("FontStyle") == "Bold"]
        self.assertTrue(bold and bold[0].get("PointSize") == "10")

    def test_text_box_not_painted_by_previous_style(self):
        # the red stroke set before the first text box belongs to no shape, so no frame may use it
        for f in self.items("TextFrame"):
            self.assertEqual(f.get("StrokeColor"), "Swatch/None")

    def test_rotated_text_frame(self):
        frames = self.items("TextFrame")
        rotated = [f for f in frames if abs(float(f.get("ItemTransform").split()[0])) < 1e-6]
        self.assertEqual(len(rotated), 1)
        a, b, c, d, e, f = map(float, rotated[0].get("ItemTransform").split())
        self.assertAlmostEqual(b, -1.0)  # counter-clockwise 90 degrees on screen
        # rotation keeps the box centre in place
        cx, cy = 0.5 * 72, 0.125 * 72
        x, y = a * cx + c * cy + e, b * cx + d * cy + f
        self.assertAlmostEqual(x, 2.5 * 72, places=2)
        self.assertAlmostEqual(y, 6.125 * 72 - 396, places=2)  # spread origin is page centre height

    def test_curves_and_dashes(self):
        polys = [p for p in self.items("Polygon") if p.get("StrokeType", "").startswith("DashedStrokeStyle/")]
        self.assertEqual(len(polys), 1)
        pts = polys[0].findall(".//PathPointType")
        self.assertGreater(len(pts), 3, "arc should become several bezier segments")
        graphic = ET.fromstring(self.zip.read("Resources/Graphic.xml"))
        dashes = graphic.findall("DashedStrokeStyle")
        self.assertEqual(len(dashes), 1)
        self.assertEqual(dashes[0].get("DashArray"), "2.16 1.44")

    def test_image(self):
        images = self.items("Image")
        self.assertEqual(len(images), 1)
        data = base64.b64decode(images[0].find("Properties/Contents").text)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")

    def test_second_page_line(self):
        lines = self.items("GraphicLine", spread=1)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].get("StrokeColor").startswith("Color/R=0 G=255 B=0"))

    def test_arc_geometry(self):
        segs = pub2idml.arc_to_cubics(0, 0, 1, 1, 0, 0, 1, 2, 0)
        mid = segs[len(segs) // 2 - 1][4:]
        self.assertAlmostEqual(math.dist(mid, (1, 0)), 1.0, places=3)


class WindowModeHelpersTest(unittest.TestCase):
    def test_expand_inputs_and_convert_many(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample = os.path.join(tmp, "A label.jsonl")
            with open(sample, "w", encoding="utf-8") as f:
                f.writelines(json.dumps(e) + "\n" for e in EVENTS)
            sub = os.path.join(tmp, "folder")
            os.makedirs(sub)
            open(os.path.join(sub, "broken.PUB"), "wb").write(b"not a publisher file")
            open(os.path.join(sub, "notes.txt"), "w").write("x")
            files, skipped = pub2idml.expand_inputs([sample, sub, os.path.join(tmp, "x.docx")])
            self.assertEqual([os.path.basename(f) for f in files], ["A label.jsonl", "broken.PUB"])
            self.assertEqual(skipped, 1)
            seen = []
            r = pub2idml.convert_many(files, lambda i, n, name: seen.append((i, n)))
            self.assertEqual(seen, [(0, 2), (1, 2)])
            self.assertEqual(r["converted"], 1)
            self.assertEqual(len(r["failures"]), 1)
            self.assertTrue(r["failures"][0].startswith("broken.PUB: "))
            self.assertTrue(os.path.isfile(os.path.join(tmp, "A label.idml")))

    def test_window_mode_selection(self):
        self.assertTrue(pub2idml.wants_gui(["--gui", "x.pub"]))
        self.assertFalse(pub2idml.wants_gui(["x.pub"]))


if __name__ == "__main__":
    unittest.main()

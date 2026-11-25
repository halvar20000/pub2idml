#!/usr/bin/env python3
"""pub2idml - convert Microsoft Publisher (.pub) files to IDML.

Pipeline:  .pub --(LibreOffice headless, libmspub import)--> .odg --(this script)--> .idml

The .odg step only uses LibreOffice as an external program; this script itself
reads the ODF drawing XML and writes an IDML package (InDesign Markup Language),
which DesignCraft, InDesign, Affinity and others can open.

Usage:
    pub2idml.py INPUT.pub|INPUT.odg [-o OUTPUT.idml] [--flatten-groups] [-v]
    pub2idml.py --batch DIR [-o OUTDIR]

Supported (prototype): pages and page size, text frames (font, size, bold,
italic, underline, colour, alignment, rotation, insets), lines, polygons,
polylines, rectangles, ellipses, SVG paths (incl. compound paths), groups,
solid fills, strokes (weight, colour, dashes), bitmap fills / images.
Not yet: tables, gradients, shadows, WordArt effects, linked text-frame chains.
"""
from __future__ import annotations

import argparse
import base64
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

# --------------------------------------------------------------------------- #
# ODF namespaces
NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "loext": "urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0",
}


def q(tag: str) -> str:
    """'draw:line' -> '{urn...}line'"""
    p, local = tag.split(":")
    return "{%s}%s" % (NS[p], local)


def local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


PT_PER = {"cm": 72 / 2.54, "mm": 72 / 25.4, "in": 72.0, "inch": 72.0, "pt": 1.0, "pc": 12.0, "px": 0.75}


def length(v: str | None, default: float = 0.0) -> float:
    """ODF length ('1.5cm', '10pt') -> points."""
    if not v:
        return default
    m = re.match(r"\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)\s*([a-z%]*)", v)
    if not m:
        return default
    num, unit = float(m.group(1)), m.group(2) or "cm"
    if unit == "%":
        return default
    return num * PT_PER.get(unit, 72 / 2.54)


def fmt(x: float) -> str:
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


# --------------------------------------------------------------------------- #
# Geometry
Matrix = tuple  # (a, b, c, d, e, f): x' = a x + c y + e ; y' = b x + d y + f
IDENT: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mul(m1: Matrix, m2: Matrix) -> Matrix:
    """Apply m2 first, then m1."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def parse_odf_transform(s: str | None) -> Matrix:
    """ODF draw:transform, e.g. 'rotate (1.57) translate (8cm 10cm)'.

    Operations are applied left to right. ODF rotate() is counter-clockwise on
    screen (y axis points down)."""
    m = IDENT
    if not s:
        return m
    for op, args in re.findall(r"([a-zA-Z]+)\s*\(([^)]*)\)", s):
        vals = [a for a in re.split(r"[\s,]+", args.strip()) if a]
        op = op.lower()
        if op == "rotate":
            a = float(vals[0])
            c, sn = math.cos(a), math.sin(a)
            t = (c, -sn, sn, c, 0.0, 0.0)
        elif op == "translate":
            tx = length(vals[0])
            ty = length(vals[1]) if len(vals) > 1 else 0.0
            t = (1.0, 0.0, 0.0, 1.0, tx, ty)
        elif op == "scale":
            sx = float(vals[0])
            sy = float(vals[1]) if len(vals) > 1 else sx
            t = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif op == "skewx":
            t = (1.0, 0.0, math.tan(float(vals[0])), 1.0, 0.0, 0.0)
        elif op == "skewy":
            t = (1.0, math.tan(float(vals[0])), 0.0, 1.0, 0.0, 0.0)
        elif op == "matrix":
            n = [float(v) if re.match(r"^[-+0-9.eE]+$", v) else length(v) for v in vals]
            t = tuple(n[:6])
        else:
            continue
        m = mul(t, m)
    return m


# A path point in IDML terms: anchor + left (incoming) + right (outgoing) direction
@dataclass
class PPoint:
    x: float
    y: float
    lx: float
    ly: float
    rx: float
    ry: float


@dataclass
class SubPath:
    points: list[PPoint]
    closed: bool


def parse_svg_path(d: str) -> list[SubPath]:
    """Parse an SVG path into cubic-bezier subpaths (in path coordinates)."""
    tokens = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", d)
    subs: list[SubPath] = []
    pts: list[PPoint] = []
    cx = cy = sx = sy = 0.0
    last_c2 = None  # previous cubic control point (for S)
    last_q = None  # previous quad control point (for T)
    cmd = None
    i = 0

    def num():
        nonlocal i
        v = float(tokens[i])
        i += 1
        return v

    def finish(closed: bool):
        nonlocal pts
        if len(pts) >= 2:
            if closed:
                f, l = pts[0], pts[-1]
                if abs(f.x - l.x) < 1e-6 and abs(f.y - l.y) < 1e-6:
                    f.lx, f.ly = l.lx, l.ly
                    pts.pop()
            subs.append(SubPath(pts, closed))
        pts = []

    def line_to(x, y):
        nonlocal cx, cy
        pts.append(PPoint(x, y, x, y, x, y))
        cx, cy = x, y

    def cubic(x1, y1, x2, y2, x, y):
        nonlocal cx, cy
        if pts:
            pts[-1].rx, pts[-1].ry = x1, y1
        pts.append(PPoint(x, y, x2, y2, x, y))
        cx, cy = x, y

    while i < len(tokens):
        t = tokens[i]
        if re.match(r"[A-Za-z]", t):
            cmd = t
            i += 1
            if cmd in "Zz":
                finish(True)
                cx, cy = sx, sy
                last_c2 = last_q = None
                continue
        elif cmd is None:
            i += 1
            continue
        rel = cmd.islower()
        C = cmd.upper()
        ox, oy = (cx, cy) if rel else (0.0, 0.0)
        if C == "M":
            finish(False)
            x, y = num() + ox, num() + oy
            sx, sy = x, y
            line_to(x, y)
            cmd = "l" if rel else "L"  # subsequent pairs are lineto
            last_c2 = last_q = None
        elif C == "L":
            line_to(num() + ox, num() + oy)
            last_c2 = last_q = None
        elif C == "H":
            line_to(num() + ox, cy)
            last_c2 = last_q = None
        elif C == "V":
            line_to(cx, num() + oy)
            last_c2 = last_q = None
        elif C == "C":
            x1, y1, x2, y2, x, y = (num() + ox, num() + oy, num() + ox, num() + oy, num() + ox, num() + oy)
            cubic(x1, y1, x2, y2, x, y)
            last_c2, last_q = (x2, y2), None
        elif C == "S":
            x1, y1 = (2 * cx - last_c2[0], 2 * cy - last_c2[1]) if last_c2 else (cx, cy)
            x2, y2, x, y = num() + ox, num() + oy, num() + ox, num() + oy
            cubic(x1, y1, x2, y2, x, y)
            last_c2, last_q = (x2, y2), None
        elif C in "QT":
            if C == "Q":
                qx, qy = num() + ox, num() + oy
            else:
                qx, qy = (2 * cx - last_q[0], 2 * cy - last_q[1]) if last_q else (cx, cy)
            x, y = num() + ox, num() + oy
            cubic(cx + 2 / 3 * (qx - cx), cy + 2 / 3 * (qy - cy), x + 2 / 3 * (qx - x), y + 2 / 3 * (qy - y), x, y)
            last_q, last_c2 = (qx, qy), None
        elif C == "A":
            rx, ry, rot, large, sweep = num(), num(), num(), num(), num()
            x, y = num() + ox, num() + oy
            for seg in arc_to_cubics(cx, cy, rx, ry, rot, int(large), int(sweep), x, y):
                cubic(*seg)
            last_c2 = last_q = None
        else:
            i += 1
    finish(False)
    return subs


def arc_to_cubics(x1, y1, rx, ry, phi_deg, fa, fs, x2, y2):
    """SVG elliptical arc -> list of cubic segments (x1,y1,x2,y2,x,y)."""
    if rx == 0 or ry == 0:
        return [(x1, y1, x2, y2, x2, y2)]
    phi = math.radians(phi_deg)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    rx, ry = abs(rx), abs(ry)
    lam = x1p**2 / rx**2 + y1p**2 / ry**2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num_ = rx**2 * ry**2 - rx**2 * y1p**2 - ry**2 * x1p**2
    den = rx**2 * y1p**2 + ry**2 * x1p**2
    co = math.sqrt(max(0, num_ / den)) if den else 0
    if fa == fs:
        co = -co
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (x1 + x2) / 2
    cy = sp * cxp + cp * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not fs and dt > 0:
        dt -= 2 * math.pi
    elif fs and dt < 0:
        dt += 2 * math.pi
    n = max(1, int(math.ceil(abs(dt) / (math.pi / 2))))
    step = dt / n
    k = 4 / 3 * math.tan(step / 4)
    out = []

    def pt(t):
        return (cx + rx * math.cos(t) * cp - ry * math.sin(t) * sp, cy + rx * math.cos(t) * sp + ry * math.sin(t) * cp)

    def deriv(t):
        return (-rx * math.sin(t) * cp - ry * math.cos(t) * sp, -rx * math.sin(t) * sp + ry * math.cos(t) * cp)

    t = t1
    for _ in range(n):
        p0, p3 = pt(t), pt(t + step)
        d0, d3 = deriv(t), deriv(t + step)
        out.append((p0[0] + k * d0[0], p0[1] + k * d0[1], p3[0] - k * d3[0], p3[1] - k * d3[1], p3[0], p3[1]))
        t += step
    return out


def transform_subpaths(subs: list[SubPath], m: Matrix) -> list[SubPath]:
    out = []
    for s in subs:
        pts = []
        for p in s.points:
            x, y = apply(m, p.x, p.y)
            lx, ly = apply(m, p.lx, p.ly)
            rx, ry = apply(m, p.rx, p.ry)
            pts.append(PPoint(x, y, lx, ly, rx, ry))
        out.append(SubPath(pts, s.closed))
    return out


def rect_path(x, y, w, h) -> list[SubPath]:
    return [SubPath([PPoint(px, py, px, py, px, py) for px, py in ((x, y), (x + w, y), (x + w, y + h), (x, y + h))], True)]


def ellipse_path(x, y, w, h) -> list[SubPath]:
    k = 0.5522847498
    cx, cy, rx, ry = x + w / 2, y + h / 2, w / 2, h / 2
    return [
        SubPath(
            [
                PPoint(cx, cy - ry, cx - k * rx, cy - ry, cx + k * rx, cy - ry),
                PPoint(cx + rx, cy, cx + rx, cy - k * ry, cx + rx, cy + k * ry),
                PPoint(cx, cy + ry, cx + k * rx, cy + ry, cx - k * rx, cy + ry),
                PPoint(cx - rx, cy, cx - rx, cy + k * ry, cx - rx, cy - k * ry),
            ],
            True,
        )
    ]


def bbox(subs: list[SubPath]):
    xs = [p.x for s in subs for p in s.points]
    ys = [p.y for s in subs for p in s.points]
    return min(xs), min(ys), max(xs), max(ys)


# --------------------------------------------------------------------------- #
# ODF style resolution


class Styles:
    """Resolves ODF styles with inheritance (automatic -> named -> default)."""

    def __init__(self, content: ET.Element, styles: ET.Element | None):
        self.by_name: dict[tuple[str, str], ET.Element] = {}
        self.defaults: dict[str, ET.Element] = {}
        self.fill_images: dict[str, str] = {}
        self.dashes: dict[str, ET.Element] = {}
        roots = [r for r in (styles, content) if r is not None]
        for root in roots:
            for st in root.iter(q("style:style")):
                self.by_name[(st.get(q("style:family")), st.get(q("style:name")))] = st
            for st in root.iter(q("style:default-style")):
                self.defaults[st.get(q("style:family"))] = st
            for fi in root.iter(q("draw:fill-image")):
                self.fill_images[fi.get(q("draw:name"))] = fi.get(q("xlink:href"))
            for ds in root.iter(q("draw:stroke-dash")):
                self.dashes[ds.get(q("draw:name"))] = ds
        self.page_layouts = {}
        self.master_pages = {}
        if styles is not None:
            for pl in styles.iter(q("style:page-layout")):
                props = pl.find(q("style:page-layout-properties"))
                if props is not None:
                    self.page_layouts[pl.get(q("style:name"))] = props
            for mp in styles.iter(q("style:master-page")):
                self.master_pages[mp.get(q("style:name"))] = mp.get(q("style:page-layout-name"))

    def chain(self, family: str, name: str | None) -> list[ET.Element]:
        out, seen = [], set()
        while name and (family, name) in self.by_name and name not in seen:
            seen.add(name)
            st = self.by_name[(family, name)]
            out.append(st)
            name = st.get(q("style:parent-style-name"))
        if family in self.defaults:
            out.append(self.defaults[family])
        return out

    def props(self, family: str, name: str | None, kind: str) -> dict[str, str]:
        """Merged properties of child element `kind` (e.g. 'style:graphic-properties')."""
        merged: dict[str, str] = {}
        for st in reversed(self.chain(family, name)):
            for el in st.findall(q(kind)):
                for k, v in el.attrib.items():
                    merged[k] = v
        return merged


def prop(d: dict, key: str, default=None):
    return d.get(q(key), default)


# --------------------------------------------------------------------------- #
# Intermediate model


@dataclass
class Shape:
    kind: str  # 'Polygon' | 'GraphicLine' | 'Oval' | 'Rectangle' | 'TextFrame' | 'Group'
    paths: list[SubPath] = field(default_factory=list)
    item_transform: Matrix = IDENT  # for TextFrame (paths are in inner coords)
    fill: str | None = None  # '#rrggbb'
    fill_opacity: float = 1.0
    stroke: str | None = None
    stroke_weight: float = 0.0
    dash: tuple | None = None
    image: tuple | None = None  # (bytes, mime, px_w, px_h, frame bbox (x0,y0,x1,y1) in spread coords)
    story: list | None = None  # list of paragraphs
    insets: tuple = (0, 0, 0, 0)  # top left bottom right
    valign: str = "TopAlign"
    children: list["Shape"] = field(default_factory=list)
    name: str = ""


@dataclass
class Page:
    width: float
    height: float
    shapes: list[Shape]
    name: str = ""


@dataclass
class Run:
    text: str
    font: str = "Calibri"
    style: str = "Regular"
    size: float = 12.0
    color: str = "#000000"
    underline: bool = False
    strike: bool = False


@dataclass
class Para:
    runs: list[Run]
    align: str = "LeftAlign"


# --------------------------------------------------------------------------- #
# ODG reader


class OdgReader:
    def __init__(self, odg_path: str, verbose: bool = False, flatten_groups: bool = False):
        self.zip = zipfile.ZipFile(odg_path)
        self.content = ET.fromstring(self.zip.read("content.xml"))
        try:
            self.styles_root = ET.fromstring(self.zip.read("styles.xml"))
        except KeyError:
            self.styles_root = None
        self.st = Styles(self.content, self.styles_root)
        self.verbose = verbose
        self.flatten = flatten_groups
        self.warnings: list[str] = []
        self.skipped: dict[str, int] = {}

    def warn(self, msg: str):
        if msg not in self.warnings:
            self.warnings.append(msg)

    # ---- pages ----
    def pages(self) -> list[Page]:
        out = []
        drawing = self.content.find(".//" + q("office:drawing"))
        if drawing is None:
            raise SystemExit("no office:drawing in content.xml (is this an ODG file?)")
        for pg in drawing.findall(q("draw:page")):
            w, h = 595.28, 841.89
            layout = self.st.master_pages.get(pg.get(q("draw:master-page-name")))
            props = self.st.page_layouts.get(layout)
            if props is not None:
                w = length(props.get(q("fo:page-width")), w)
                h = length(props.get(q("fo:page-height")), h)
            shapes = self.children(pg, IDENT)
            out.append(Page(w, h, shapes, pg.get(q("draw:name"), "")))
        return out

    def children(self, parent: ET.Element, m: Matrix) -> list[Shape]:
        out = []
        for el in parent:
            res = self.shape(el, m)
            if res is None:
                continue
            if isinstance(res, list):
                out.extend(res)
            else:
                out.append(res)
        return out

    # ---- style helpers ----
    def gprops(self, el: ET.Element) -> dict:
        g = self.st.props("graphic", el.get(q("draw:style-name")), "style:graphic-properties")
        # libmspub sometimes also puts fill info on the paragraph style (loext:graphic-properties)
        tsn = el.get(q("draw:text-style-name"))
        if tsn and q("draw:fill") not in self.st.props("graphic", el.get(q("draw:style-name")), "style:graphic-properties"):
            p = self.st.props("paragraph", tsn, "loext:graphic-properties")
            for k, v in p.items():
                g.setdefault(k, v)
        return g

    def apply_paint(self, s: Shape, g: dict, allow_fill: bool = True):
        stroke = prop(g, "draw:stroke", "solid")
        if stroke != "none":
            s.stroke = prop(g, "svg:stroke-color", "#000000")
            w = length(prop(g, "svg:stroke-width"), 0.0)
            s.stroke_weight = w if w > 0 else 0.25  # ODF width 0 = hairline
            if stroke == "dash":
                ds = self.st.dashes.get(prop(g, "draw:stroke-dash"))
                if ds is not None:
                    dl = length(ds.get(q("draw:dots1-length")), s.stroke_weight * 3) or s.stroke_weight
                    gap = length(ds.get(q("draw:distance")), s.stroke_weight * 2) or s.stroke_weight
                    s.dash = (max(dl, 0.1), max(gap, 0.1))
                else:
                    s.dash = (3.0, 2.0)
        if not allow_fill:
            return
        fill = prop(g, "draw:fill", "none")
        if fill == "solid":
            s.fill = prop(g, "draw:fill-color", "#729fcf")
            op = prop(g, "draw:opacity")
            if op and op.endswith("%"):
                s.fill_opacity = float(op[:-1]) / 100
        elif fill == "gradient":
            s.fill = prop(g, "draw:fill-color", "#cccccc")
            self.warn("gradient fills are approximated by a solid colour")
        elif fill == "hatch":
            self.warn("hatch fills are not supported")
        elif fill == "bitmap":
            href = self.st.fill_images.get(prop(g, "draw:fill-image-name"))
            if href:
                img = self.load_image(href)
                if img:
                    s.image = img
            if prop(g, "style:repeat", "repeat") not in ("stretch", "no-repeat"):
                self.warn("tiled bitmap fills are placed once, stretched to the frame")

    def load_image(self, href: str):
        try:
            data = self.zip.read(href)
        except KeyError:
            self.warn(f"image {href} not found in package")
            return None
        size = image_size(data)
        if not size:
            self.warn(f"image {href}: unknown format, skipped")
            return None
        mime, w, h = size
        return (data, mime, w, h)

    # ---- geometry of a shape element ----
    def base_matrix(self, el: ET.Element, m: Matrix) -> Matrix:
        tr = el.get(q("draw:transform"))
        x = length(el.get(q("svg:x")))
        y = length(el.get(q("svg:y")))
        local_m = parse_odf_transform(tr) if tr else (1.0, 0.0, 0.0, 1.0, x, y)
        return mul(m, local_m)

    def viewbox_matrix(self, el: ET.Element) -> Matrix:
        w = length(el.get(q("svg:width")))
        h = length(el.get(q("svg:height")))
        vb = el.get(q("svg:viewBox"))
        if not vb:
            return IDENT
        vx, vy, vw, vh = [float(v) for v in vb.split()]
        sx = w / vw if vw else 1.0
        sy = h / vh if vh else 1.0
        return (sx, 0.0, 0.0, sy, -vx * sx, -vy * sy)

    # ---- shapes ----
    def shape(self, el: ET.Element, m: Matrix):
        tag = local(el.tag)
        ns = el.tag.split("}")[0][1:] if "}" in el.tag else ""
        if ns != NS["draw"]:
            return None
        if tag == "g":
            kids = self.children(el, m)
            if not kids:
                return None
            if self.flatten or len(kids) == 1:
                return kids
            return Shape("Group", children=kids)
        if tag == "line":
            p1 = apply(m, length(el.get(q("svg:x1"))), length(el.get(q("svg:y1"))))
            p2 = apply(m, length(el.get(q("svg:x2"))), length(el.get(q("svg:y2"))))
            s = Shape("GraphicLine", [SubPath([PPoint(*p1, *p1, *p1), PPoint(*p2, *p2, *p2)], False)])
            self.apply_paint(s, self.gprops(el), allow_fill=False)
            return self.with_text(s, el, m)
        if tag in ("polygon", "polyline"):
            pts = []
            for pair in (el.get(q("draw:points")) or "").split():
                a, b = pair.split(",")
                pts.append((float(a), float(b)))
            if len(pts) < 2:
                return None
            mm = mul(self.base_matrix(el, m), self.viewbox_matrix(el))
            closed = tag == "polygon"
            sub = SubPath([PPoint(*apply(mm, *p), *apply(mm, *p), *apply(mm, *p)) for p in pts], closed)
            kind = "Polygon" if closed or len(pts) > 2 else "GraphicLine"
            s = Shape(kind, [sub])
            self.apply_paint(s, self.gprops(el), allow_fill=closed)
            return self.with_text(s, el, m)
        if tag == "path":
            subs = parse_svg_path(el.get(q("svg:d")) or "")
            subs = [sp for sp in subs if len(sp.points) >= 2 or (sp.closed and len(sp.points) >= 1)]
            subs = [sp for sp in subs if len(sp.points) >= 2]
            if not subs:
                return None
            mm = mul(self.base_matrix(el, m), self.viewbox_matrix(el))
            subs = transform_subpaths(subs, mm)
            closed = any(sp.closed for sp in subs)
            kind = "Polygon"
            if not closed and len(subs) == 1 and len(subs[0].points) == 2:
                kind = "GraphicLine"
            s = Shape(kind, subs)
            self.apply_paint(s, self.gprops(el), allow_fill=closed)
            return self.with_text(s, el, m)
        if tag in ("rect", "ellipse", "circle"):
            w = length(el.get(q("svg:width")))
            h = length(el.get(q("svg:height")))
            bm = self.base_matrix(el, m)
            subs = rect_path(0, 0, w, h) if tag == "rect" else ellipse_path(0, 0, w, h)
            s = Shape("Rectangle" if tag == "rect" else "Oval", transform_subpaths(subs, bm))
            self.apply_paint(s, self.gprops(el))
            return self.with_text(s, el, m)
        if tag == "frame":
            return self.frame(el, m)
        if tag == "custom-shape":
            # fall back to the bounding box; enhanced geometry is not evaluated
            w = length(el.get(q("svg:width")))
            h = length(el.get(q("svg:height")))
            bm = self.base_matrix(el, m)
            s = Shape("Rectangle", transform_subpaths(rect_path(0, 0, w, h), bm))
            self.apply_paint(s, self.gprops(el))
            self.warn("custom shapes (AutoShapes) are converted to their bounding rectangle")
            return self.with_text(s, el, m)
        if tag in ("connector", "measure"):
            p1 = apply(m, length(el.get(q("svg:x1"))), length(el.get(q("svg:y1"))))
            p2 = apply(m, length(el.get(q("svg:x2"))), length(el.get(q("svg:y2"))))
            s = Shape("GraphicLine", [SubPath([PPoint(*p1, *p1, *p1), PPoint(*p2, *p2, *p2)], False)])
            self.apply_paint(s, self.gprops(el), allow_fill=False)
            return s
        self.skipped[tag] = self.skipped.get(tag, 0) + 1
        return None

    def with_text(self, s: Shape, el: ET.Element, m: Matrix):
        """Shapes can carry text (text:p children). Add a text frame on top if non-empty."""
        paras = self.paragraphs(el)
        if not paras:
            return s
        x0, y0, x1, y1 = bbox(s.paths)
        tf = Shape("TextFrame", rect_path(x0, y0, x1 - x0, y1 - y0), story=paras, valign="CenterAlign")
        return [s, tf]

    def frame(self, el: ET.Element, m: Matrix):
        w = length(el.get(q("svg:width")))
        h = length(el.get(q("svg:height")))
        bm = self.base_matrix(el, m)
        g = self.gprops(el)
        tb = el.find(q("draw:text-box"))
        img = el.find(q("draw:image"))
        if img is not None and tb is None:
            href = img.get(q("xlink:href"))
            data = self.load_image(href) if href else None
            s = Shape("Rectangle", transform_subpaths(rect_path(0, 0, w, h), bm))
            self.apply_paint(s, g)
            s.fill = None if s.fill == "#729fcf" else s.fill
            if data:
                s.image = data
            return s
        if tb is None:
            self.skipped["frame(" + ",".join(local(c.tag) for c in el) + ")"] = 1
            return None
        paras = self.paragraphs(tb)
        s = Shape("TextFrame", rect_path(0, 0, w, h), item_transform=bm, story=paras)
        self.apply_paint(s, g)
        s.insets = (
            length(prop(g, "fo:padding-top")),
            length(prop(g, "fo:padding-left")),
            length(prop(g, "fo:padding-bottom")),
            length(prop(g, "fo:padding-right")),
        )
        va = prop(g, "draw:textarea-vertical-align", "top")
        s.valign = {"middle": "CenterAlign", "bottom": "BottomAlign", "justify": "JustifyAlign"}.get(va, "TopAlign")
        has_text = any(r.text.strip() for p in paras for r in p.runs)
        if not has_text and not s.fill and not s.stroke:
            return None  # empty placeholder text box (libmspub emits these in groups)
        return s

    # ---- text ----
    def text_props(self, family: str, name: str | None) -> dict:
        return self.st.props(family, name, "style:text-properties")

    def paragraphs(self, container: ET.Element) -> list[Para]:
        out = []
        for p in container:
            if local(p.tag) == "p" or local(p.tag) == "h":
                out.append(self.paragraph(p))
            elif local(p.tag) == "list":
                for item in p.iter(q("text:p")):
                    out.append(self.paragraph(item))
        # drop trailing empty paragraphs
        while out and not any(r.text for r in out[-1].runs):
            out.pop()
        return out

    def paragraph(self, p: ET.Element) -> Para:
        pname = p.get(q("text:style-name"))
        pprops = self.st.props("paragraph", pname, "style:paragraph-properties")
        align = {
            "center": "CenterAlign",
            "end": "RightAlign",
            "right": "RightAlign",
            "justify": "LeftJustified",
        }.get(prop(pprops, "fo:text-align", "start"), "LeftAlign")
        base = self.text_props("paragraph", pname)
        runs: list[Run] = []
        self.collect(p, base, runs)
        # strip the trailing space libmspub appends to every paragraph
        if runs and runs[-1].text.endswith(" "):
            runs[-1].text = runs[-1].text.rstrip(" ")
        return Para([r for r in runs if r.text], align)

    def make_run(self, text: str, tp: dict) -> Run:
        weight = prop(tp, "fo:font-weight", "normal")
        italic = prop(tp, "fo:font-style", "normal") in ("italic", "oblique")
        bold = weight in ("bold", "600", "700", "800", "900")
        style = "Bold Italic" if bold and italic else "Bold" if bold else "Italic" if italic else "Regular"
        font = prop(tp, "style:font-name") or prop(tp, "fo:font-family") or "Calibri"
        font = font.strip("'\"")
        size_v = prop(tp, "fo:font-size", "12pt")
        size = length(size_v, 12.0) if not size_v.endswith("%") else 12.0
        return Run(
            text,
            font=font,
            style=style,
            size=size,
            color=prop(tp, "fo:color", "#000000"),
            underline=prop(tp, "style:text-underline-style", "none") != "none",
            strike=prop(tp, "style:text-line-through-style", "none") != "none",
        )

    def collect(self, el: ET.Element, tp: dict, runs: list[Run]):
        if el.text:
            runs.append(self.make_run(el.text, tp))
        for c in el:
            t = local(c.tag)
            if t == "span":
                ctp = dict(tp)
                ctp.update(self.text_props("text", c.get(q("text:style-name"))))
                self.collect(c, ctp, runs)
            elif t == "s":
                runs.append(self.make_run(" " * int(c.get(q("text:c"), "1")), tp))
            elif t == "tab":
                runs.append(self.make_run("\t", tp))
            elif t == "line-break":
                runs.append(self.make_run("\u2028", tp))
            elif t in ("a", "span"):
                self.collect(c, tp, runs)
            else:
                self.collect(c, tp, runs)
            if c.tail:
                runs.append(self.make_run(c.tail, tp))


def image_size(data: bytes):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return "image/png", w, h
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                return "image/jpeg", w, h
            seg = struct.unpack(">H", data[i + 2 : i + 4])[0]
            i += 2 + seg
        return None
    if data[:6] in (b"GIF87a", b"GIF89a"):
        w, h = struct.unpack("<HH", data[6:10])
        return "image/gif", w, h
    if data[:2] == b"BM":
        w, h = struct.unpack("<ii", data[18:26])
        return "image/bmp", w, abs(h)
    return None


# --------------------------------------------------------------------------- #
# IDML writer

IDML_NS = "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging"
DOM = "8.0"


def hex_rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


class IdmlWriter:
    def __init__(self, pages: list[Page]):
        self.pages = pages
        self.n = 0
        self.colors: dict[str, str] = {}  # '#rrggbb' -> Self id
        self.dashes: dict[tuple, str] = {}  # (dash, gap) -> DashedStrokeStyle Self id
        self.fonts: dict[str, set] = {}
        self.stories: list[tuple[str, str]] = []  # (id, xml)
        self.layer = "uLayer1"

    def fresh(self) -> str:
        self.n += 1
        return f"u{self.n:x}"

    def color(self, c: str | None) -> str:
        if not c:
            return "Swatch/None"
        c = c.lower()
        if c in ("#000000",):
            return "Color/Black"
        if c in ("#ffffff",):
            return "Color/Paper"
        if c not in self.colors:
            r, g, b = hex_rgb(c)
            self.colors[c] = f"Color/R={r} G={g} B={b}"
        return self.colors[c]

    # ---- geometry XML ----
    def path_geometry(self, subs: list[SubPath]) -> str:
        out = ["<PathGeometry>"]
        for s in subs:
            out.append(f'<GeometryPathType PathOpen="{"false" if s.closed else "true"}"><PathPointArray>')
            for p in s.points:
                out.append(
                    f'<PathPointType Anchor="{fmt(p.x)} {fmt(p.y)}" LeftDirection="{fmt(p.lx)} {fmt(p.ly)}" '
                    f'RightDirection="{fmt(p.rx)} {fmt(p.ry)}"/>'
                )
            out.append("</PathPointArray></GeometryPathType>")
        out.append("</PathGeometry>")
        return "".join(out)

    def paint_attrs(self, s: Shape) -> str:
        a = f' FillColor="{self.color(s.fill)}"'
        if s.stroke:
            a += f' StrokeColor="{self.color(s.stroke)}" StrokeWeight="{fmt(s.stroke_weight)}"'
            if s.dash:
                key = (round(s.dash[0], 3), round(s.dash[1], 3))
                if key not in self.dashes:
                    self.dashes[key] = f"DashedStrokeStyle/Dash {fmt(key[0])}x{fmt(key[1])}"
                a += f' StrokeType="{self.dashes[key]}"'
        else:
            a += ' StrokeColor="Swatch/None" StrokeWeight="0"'
        return a

    def transparency(self, s: Shape) -> str:
        if s.fill and s.fill_opacity < 0.999:
            return (
                f'<TransparencySetting><BlendingSetting Opacity="{fmt(s.fill_opacity * 100)}"/></TransparencySetting>'
            )
        return ""

    def item(self, s: Shape, ox: float, oy: float) -> str:
        """ox/oy: page origin in spread coordinates."""
        if s.kind == "Group":
            # the group itself is untransformed; its children carry the page offset
            inner = "".join(self.item(c, ox, oy) for c in s.children)
            return f'<Group Self="{self.fresh()}" ItemLayer="{self.layer}" ItemTransform="1 0 0 1 0 0">{inner}</Group>'
        sid = self.fresh()
        it = s.item_transform
        if s.kind == "TextFrame":
            m = mul((1, 0, 0, 1, ox, oy), it)
            story_id = self.fresh()
            self.stories.append((story_id, self.story_xml(story_id, s.story or [])))
            x0, y0, x1, y1 = bbox(s.paths)
            t, l, b, r = s.insets
            return (
                f'<TextFrame Self="{sid}" ParentStory="{story_id}" PreviousTextFrame="n" NextTextFrame="n" '
                f'ContentType="TextType" ItemLayer="{self.layer}" ItemTransform="{" ".join(fmt(v) for v in m)}"'
                f"{self.paint_attrs(s)}>"
                f"<Properties>{self.path_geometry(s.paths)}</Properties>"
                f"{self.transparency(s)}"
                f'<TextFramePreference TextColumnCount="1" VerticalJustification="{s.valign}" '
                f'FirstBaselineOffset="AscentOffset" AutoSizingType="Off" '
                f'InsetSpacing="{fmt(t)} {fmt(l)} {fmt(b)} {fmt(r)}"/>'
                f"</TextFrame>"
            )
        # spline items: geometry already in page coordinates; put the page offset in ItemTransform
        m = mul((1, 0, 0, 1, ox, oy), it)
        tf = " ".join(fmt(v) for v in m)
        content = "GraphicType" if s.image else "Unassigned"
        inner = ""
        if s.image:
            data, mime, pw, ph = s.image
            x0, y0, x1, y1 = bbox(s.paths)
            sx = (x1 - x0) / pw if pw else 1
            sy = (y1 - y0) / ph if ph else 1
            img_id, link_id = self.fresh(), self.fresh()
            fmt_name = {"image/png": "Portable Network Graphics (PNG)", "image/jpeg": "JPEG", "image/gif": "GIF"}.get(
                mime, "Windows Bitmap"
            )
            ext = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif"}.get(mime, "bmp")
            inner = (
                f'<Image Self="{img_id}" ItemTransform="{fmt(sx)} 0 0 {fmt(sy)} {fmt(x0)} {fmt(y0)}" '
                f'ActualPpi="{fmt(72 / sx if sx else 72)} {fmt(72 / sy if sy else 72)}">'
                f"<Properties><Contents><![CDATA[{base64.b64encode(data).decode()}]]></Contents>"
                f'<GraphicBounds Left="0" Top="0" Right="{pw}" Bottom="{ph}"/></Properties>'
                f'<Link Self="{link_id}" AssetURL="$ID/" AssetID="$ID/" LinkResourceURI="file:image_{img_id}.{ext}" '
                f'LinkResourceFormat="$ID/{fmt_name}" StoredState="Embedded" LinkClassID="35906" LinkClientID="257" '
                f'LinkResourceModified="false" LinkObjectModified="false" ShowInUI="true" CanEmbed="true" '
                f'CanUnembed="true" CanPackage="true" ImportPolicy="NoAutoImport" ExportPolicy="NoAutoExport"/>'
                f"</Image>"
            )
        tag = s.kind
        return (
            f'<{tag} Self="{sid}" ContentType="{content}" ItemLayer="{self.layer}" ItemTransform="{tf}"'
            f"{self.paint_attrs(s)}>"
            f"<Properties>{self.path_geometry(s.paths)}</Properties>"
            f"{self.transparency(s)}{inner}</{tag}>"
        )

    def story_xml(self, sid: str, paras: list[Para]) -> str:
        body = []
        for i, p in enumerate(paras):
            body.append(
                f'<ParagraphStyleRange AppliedParagraphStyle="ParagraphStyle/$ID/NormalParagraphStyle" Justification="{p.align}">'
            )
            runs = p.runs or [Run("")]
            for j, r in enumerate(runs):
                self.fonts.setdefault(r.font, set()).add(r.style)
                attrs = (
                    f' PointSize="{fmt(r.size)}" FontStyle={quoteattr(r.style)} FillColor="{self.color(r.color)}"'
                )
                if r.underline:
                    attrs += ' Underline="true"'
                if r.strike:
                    attrs += ' StrikeThru="true"'
                parts = r.text.split("\u2028")
                content = "<Br/>".join(f"<Content>{escape(pt)}</Content>" for pt in parts)
                # the paragraph break goes at the end of the last run of every paragraph except the last
                br = "<Br/>" if (j == len(runs) - 1 and i < len(paras) - 1) else ""
                body.append(
                    f'<CharacterStyleRange AppliedCharacterStyle="CharacterStyle/$ID/[No character style]"{attrs}>'
                    f'<Properties><AppliedFont type="string">{escape(r.font)}</AppliedFont></Properties>'
                    f"{content}{br}</CharacterStyleRange>"
                )
            body.append("</ParagraphStyleRange>")
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<idPkg:Story xmlns:idPkg="{IDML_NS}" DOMVersion="{DOM}">'
            f'<Story Self="{sid}" AppliedTOCStyle="n" TrackChanges="false" StoryTitle="$ID/" AppliedNamedGrid="n">'
            '<StoryPreference OpticalMarginAlignment="false" OpticalMarginSize="12" FrameType="TextFrameType" '
            'StoryOrientation="Horizontal" StoryDirection="LeftToRightDirection"/>'
            f'{"".join(body)}</Story></idPkg:Story>'
        )

    def spread_xml(self, sid: str, page_id: str, pg: Page, index: int) -> str:
        # Single-page spread: the spread origin is at the page's left edge, vertically centred.
        ox, oy = 0.0, -pg.height / 2
        items = [self.item(s, ox, oy) for s in pg.shapes]
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<idPkg:Spread xmlns:idPkg="{IDML_NS}" DOMVersion="{DOM}">'
            f'<Spread Self="{sid}" PageCount="1" BindingLocation="0" AllowPageShuffle="true" '
            f'ItemTransform="1 0 0 1 0 {fmt(index * (pg.height + 36))}" ShowMasterItems="true" PageTransitionType="None">'
            f'<Page Self="{page_id}" Name="{index + 1}" AppliedMaster="uMaster" '
            f'GeometricBounds="0 0 {fmt(pg.height)} {fmt(pg.width)}" ItemTransform="1 0 0 1 {fmt(ox)} {fmt(oy)}" '
            f'OverrideList="" TabOrder="" GridStartingPoint="TopOutside" UseMasterGrid="true">'
            f'<MarginPreference ColumnCount="1" ColumnGutter="12" Top="0" Bottom="0" Left="0" Right="0" '
            f'ColumnDirection="Horizontal" ColumnsPositions="0 {fmt(pg.width)}"/></Page>'
            f'{"".join(items)}</Spread></idPkg:Spread>'
        )

    def write(self, out_path: str):
        w = self.pages[0].width if self.pages else 595.28
        h = self.pages[0].height if self.pages else 841.89
        spreads = []
        for i, pg in enumerate(self.pages):
            sid, pid = f"uSpread{i + 1}", f"uPage{i + 1}"
            spreads.append((sid, self.spread_xml(sid, pid, pg, i)))
        story_ids = " ".join(s for s, _ in self.stories)

        designmap = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<?aid style="50" type="document" readerVersion="6.0" featureSet="257" product="8.0(370)" ?>\n'
            f'<Document xmlns:idPkg="{IDML_NS}" DOMVersion="{DOM}" Self="d" StoryList="{story_ids}" '
            f'ZeroPoint="0 0" ActiveLayer="{self.layer}" CMYKProfile="$ID/" RGBProfile="$ID/" SolidColorIntent="UseColorSettings" '
            'AfterBlendingIntent="UseColorSettings" DefaultImageIntent="UseColorSettings" RGBPolicy="PreserveEmbeddedProfiles" '
            'CMYKPolicy="CombinationOfPreserveAndSafeCmyk" AccurateLABSpots="false">'
            '<idPkg:Graphic src="Resources/Graphic.xml"/>'
            '<idPkg:Fonts src="Resources/Fonts.xml"/>'
            '<idPkg:Styles src="Resources/Styles.xml"/>'
            '<idPkg:Preferences src="Resources/Preferences.xml"/>'
            f'<Layer Self="{self.layer}" Name="Layer 1" Visible="true" Locked="false" IgnoreWrap="false" '
            'ShowGuides="true" LockGuides="false" UI="true" Expendable="true" Printable="true">'
            '<Properties><LayerColor type="enumeration">LightBlue</LayerColor></Properties></Layer>'
            '<idPkg:MasterSpread src="MasterSpreads/MasterSpread_uMaster.xml"/>'
            + "".join(f'<idPkg:Spread src="Spreads/Spread_{sid}.xml"/>' for sid, _ in spreads)
            + "".join(f'<idPkg:Story src="Stories/Story_{s}.xml"/>' for s, _ in self.stories)
            + "</Document>"
        )

        colors = "".join(
            f'<Color Self="{cid}" Model="Process" Space="RGB" ColorValue="{" ".join(map(str, hex_rgb(c)))}" '
            f'ColorOverride="Normal" AlternateSpace="NoAlternateColor" AlternateColorValue="" Name="{cid[6:]}" '
            f'ColorEditable="true" ColorRemovable="true" Visible="true" SwatchCreatorID="7937"/>'
            for c, cid in self.colors.items()
        )
        graphic = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<idPkg:Graphic xmlns:idPkg="{IDML_NS}" DOMVersion="{DOM}">'
            '<Color Self="Color/Black" Model="Process" Space="CMYK" ColorValue="0 0 0 100" ColorOverride="Specialblack" '
            'AlternateSpace="NoAlternateColor" AlternateColorValue="" Name="Black" ColorEditable="false" ColorRemovable="false" '
            'Visible="true" SwatchCreatorID="7937"/>'
            '<Color Self="Color/Paper" Model="Process" Space="CMYK" ColorValue="0 0 0 0" ColorOverride="Specialpaper" '
            'AlternateSpace="NoAlternateColor" AlternateColorValue="" Name="Paper" ColorEditable="true" ColorRemovable="false" '
            'Visible="true" SwatchCreatorID="7937"/>'
            '<Color Self="Color/Registration" Model="Registration" Space="CMYK" ColorValue="100 100 100 100" '
            'ColorOverride="Specialregistration" AlternateSpace="NoAlternateColor" AlternateColorValue="" Name="Registration" '
            'ColorEditable="false" ColorRemovable="false" Visible="true" SwatchCreatorID="7937"/>'
            f"{colors}"
            '<Swatch Self="Swatch/None" Name="None" ColorEditable="false" ColorRemovable="false" Visible="true" SwatchCreatorID="7937"/>'
            '<StrokeStyle Self="StrokeStyle/$ID/Solid" Name="$ID/Solid"/>'
            '<StrokeStyle Self="StrokeStyle/$ID/Dashed" Name="$ID/Dashed"/>'
            + "".join(
                f'<DashedStrokeStyle Self="{sid}" Name="{sid.split("/", 1)[1]}" DashArray="{fmt(d)} {fmt(g)}" '
                'EndcapType="ButtEndCap" CornerAdjustment="None"/>'
                for (d, g), sid in self.dashes.items()
            )
            + "</idPkg:Graphic>"
        )

        font_xml = []
        for fam, styles in sorted(self.fonts.items()):
            fid = f"di{abs(hash(fam)) % 10**8:x}"
            fonts = "".join(
                f'<Font Self="{fid}Font/{escape(fam)} {escape(st)}" FontFamily={quoteattr(fam)} Name={quoteattr(fam + " " + st)} '
                f'PostScriptName={quoteattr((fam + "-" + st).replace(" ", ""))} Status="NotAvailable" FontStyleName={quoteattr(st)} '
                f'FontType="OpenTypeTT" WritingScript="0" FullName={quoteattr(fam + " " + st)} FullNameNative={quoteattr(fam + " " + st)} '
                f'FontStyleNameNative={quoteattr(st)} PlatformName="$ID/" Version=""/>'
                for st in sorted(styles)
            )
            font_xml.append(f'<FontFamily Self="{fid}" Name={quoteattr(fam)}>{fonts}</FontFamily>')
        fonts_doc = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<idPkg:Fonts xmlns:idPkg="{IDML_NS}" DOMVersion="{DOM}">{"".join(font_xml)}</idPkg:Fonts>'
        )

        styles_doc = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<idPkg:Styles xmlns:idPkg="{IDML_NS}" DOMVersion="{DOM}">'
            '<RootCharacterStyleGroup Self="uCharGroup">'
            '<CharacterStyle Self="CharacterStyle/$ID/[No character style]" Imported="false" Name="$ID/[No character style]"/>'
            "</RootCharacterStyleGroup>"
            '<RootParagraphStyleGroup Self="uParaGroup">'
            '<ParagraphStyle Self="ParagraphStyle/$ID/[No paragraph style]" Name="$ID/[No paragraph style]" Imported="false" '
            'FillColor="Color/Black" FontStyle="Regular" PointSize="12" Leading="Auto" AutoLeading="120" '
            'Justification="LeftAlign" Hyphenation="false">'
            '<Properties><AppliedFont type="string">Calibri</AppliedFont></Properties></ParagraphStyle>'
            '<ParagraphStyle Self="ParagraphStyle/$ID/NormalParagraphStyle" Name="$ID/NormalParagraphStyle" Imported="false" '
            'Hyphenation="false" SpaceBefore="0" SpaceAfter="0">'
            '<Properties><BasedOn type="object">ParagraphStyle/$ID/[No paragraph style]</BasedOn></Properties></ParagraphStyle>'
            "</RootParagraphStyleGroup>"
            '<RootObjectStyleGroup Self="uObjGroup">'
            '<ObjectStyle Self="ObjectStyle/$ID/[None]" Name="$ID/[None]" FillColor="Swatch/None" StrokeColor="Swatch/None" StrokeWeight="0"/>'
            "</RootObjectStyleGroup>"
            "</idPkg:Styles>"
        )

        prefs = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<idPkg:Preferences xmlns:idPkg="{IDML_NS}" DOMVersion="{DOM}">'
            f'<DocumentPreference PageHeight="{fmt(h)}" PageWidth="{fmt(w)}" PagesPerDocument="{len(self.pages)}" '
            'FacingPages="false" DocumentBleedTopOffset="0" DocumentBleedBottomOffset="0" DocumentBleedInsideOrLeftOffset="0" '
            'DocumentBleedOutsideOrRightOffset="0" DocumentBleedUniformSize="true" AllowPageShuffle="true" '
            'PageBinding="LeftToRight" ColumnDirection="Horizontal" Intent="PrintIntent"/>'
            '<MarginPreference ColumnCount="1" ColumnGutter="12" Top="0" Bottom="0" Left="0" Right="0" ColumnDirection="Horizontal"/>'
            '<ViewPreference HorizontalMeasurementUnits="Millimeters" VerticalMeasurementUnits="Millimeters" '
            'RulerOrigin="PageOrigin"/>'
            "</idPkg:Preferences>"
        )

        master = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<idPkg:MasterSpread xmlns:idPkg="{IDML_NS}" DOMVersion="{DOM}">'
            '<MasterSpread Self="uMaster" Name="A-Master" NamePrefix="A" BaseName="Master" ShowMasterItems="true" '
            'PageCount="1" ItemTransform="1 0 0 1 0 0" OverriddenPageItemProps="">'
            f'<Page Self="uMasterPage" Name="A" AppliedMaster="n" GeometricBounds="0 0 {fmt(h)} {fmt(w)}" '
            f'ItemTransform="1 0 0 1 0 {fmt(-h / 2)}" OverrideList="" TabOrder="">'
            f'<MarginPreference ColumnCount="1" ColumnGutter="12" Top="0" Bottom="0" Left="0" Right="0" '
            f'ColumnDirection="Horizontal" ColumnsPositions="0 {fmt(w)}"/></Page>'
            "</MasterSpread></idPkg:MasterSpread>"
        )

        container = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="designmap.xml" media-type="text/xml"/></rootfiles></container>'
        )

        with zipfile.ZipFile(out_path, "w") as z:
            z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.adobe.indesign-idml-package", compress_type=zipfile.ZIP_STORED)
            zc = zipfile.ZIP_DEFLATED
            z.writestr("META-INF/container.xml", container, compress_type=zc)
            z.writestr("designmap.xml", designmap, compress_type=zc)
            z.writestr("Resources/Graphic.xml", graphic, compress_type=zc)
            z.writestr("Resources/Fonts.xml", fonts_doc, compress_type=zc)
            z.writestr("Resources/Styles.xml", styles_doc, compress_type=zc)
            z.writestr("Resources/Preferences.xml", prefs, compress_type=zc)
            z.writestr("MasterSpreads/MasterSpread_uMaster.xml", master, compress_type=zc)
            for sid, xml in spreads:
                z.writestr(f"Spreads/Spread_{sid}.xml", xml, compress_type=zc)
            for s, xml in self.stories:
                z.writestr(f"Stories/Story_{s}.xml", xml, compress_type=zc)


# --------------------------------------------------------------------------- #
# Driver


def find_soffice() -> str:
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    for p in (
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        if os.path.exists(p):
            return p
    raise SystemExit("LibreOffice (soffice) not found - install it from https://www.libreoffice.org/")


def pub_to_odg(pub: str, workdir: str) -> str:
    soffice = find_soffice()
    profile = os.path.join(workdir, "lo-profile")
    cmd = [
        soffice,
        f"-env:UserInstallation=file://{os.path.abspath(profile)}",
        "--headless",
        "--convert-to",
        "odg",
        "--outdir",
        workdir,
        pub,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    odg = os.path.join(workdir, os.path.splitext(os.path.basename(pub))[0] + ".odg")
    if not os.path.exists(odg):
        raise SystemExit(f"LibreOffice could not convert {pub}")
    return odg


def convert(src: str, out: str, flatten: bool = False, verbose: bool = False) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        odg = src if src.lower().endswith(".odg") else pub_to_odg(src, tmp)
        r = OdgReader(odg, verbose=verbose, flatten_groups=flatten)
        pages = r.pages()
        IdmlWriter(pages).write(out)
        msgs = list(r.warnings)
        for k, v in r.skipped.items():
            msgs.append(f"skipped {v} unsupported element(s): {k}")
        if verbose:
            n = sum(count(s) for p in pages for s in p.shapes)
            print(f"{src}: {len(pages)} page(s), {n} item(s) -> {out}")
        return msgs


def count(s: Shape) -> int:
    return 1 + sum(count(c) for c in s.children)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convert Microsoft Publisher (.pub) or ODG files to IDML.")
    ap.add_argument("input", nargs="?", help=".pub or .odg file")
    ap.add_argument("-o", "--output", help="output .idml (or output directory with --batch)")
    ap.add_argument("--batch", metavar="DIR", help="convert every .pub in DIR")
    ap.add_argument("--flatten-groups", action="store_true", help="do not keep groups")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    jobs = []
    if a.batch:
        outdir = a.output or a.batch
        os.makedirs(outdir, exist_ok=True)
        for f in sorted(os.listdir(a.batch)):
            if f.lower().endswith(".pub"):
                jobs.append((os.path.join(a.batch, f), os.path.join(outdir, os.path.splitext(f)[0] + ".idml")))
    elif a.input:
        jobs.append((a.input, a.output or os.path.splitext(a.input)[0] + ".idml"))
    else:
        ap.error("give an input file or --batch DIR")

    failed = 0
    for src, out in jobs:
        try:
            for m in convert(src, out, a.flatten_groups, a.verbose):
                print(f"  warning: {m}", file=sys.stderr)
            print(f"OK   {src} -> {out}")
        except Exception as e:  # keep going in batch mode
            failed += 1
            print(f"FAIL {src}: {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

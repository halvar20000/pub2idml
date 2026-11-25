#!/usr/bin/env python3
"""pub2idml - convert Microsoft Publisher (.pub) files to IDML.

Pipeline:  .pub --(pub2json: libmspub)--> JSON Lines --(this script)--> .idml

pub2json is a small C++ helper (see pub2json/) built on libmspub, the
Publisher reader also used by LibreOffice. It reports every page, shape, text
run and image as one JSON object per line; this script turns that stream into
an IDML package (InDesign Markup Language), which DesignCraft, InDesign,
Affinity and others can open. LibreOffice itself is not needed.

Usage:
    pub2idml INPUT.pub [-o OUTPUT.idml] [--flatten-groups] [-v]
    pub2idml --batch DIR [-o OUTDIR]
    pub2idml INPUT.jsonl            # a stream saved earlier with `pub2json IN.pub OUT.jsonl`

Supported: pages and page size, text frames (font, size, bold, italic,
underline, colour, alignment, rotation, insets, vertical alignment), lines,
polygons, polylines, rectangles, ellipses, Bezier paths with arcs, compound
paths, groups, solid fills, strokes (weight, colour, dashes), bitmap fills /
images. Not yet: tables, gradients, shadows, WordArt effects, linked text chains.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape, quoteattr

__version__ = "0.3.0"

PT_PER_UNIT = {"in": 72.0, "pt": 1.0, "twip": 1 / 20}


def num(v, default: float = 0.0) -> float:
    """A librevenge number {"v":..,"u":..} -> points (lengths) or plain value."""
    if isinstance(v, dict):
        return v["v"] * PT_PER_UNIT.get(v.get("u", ""), 1.0)
    if isinstance(v, (int, float)):
        return float(v)
    return default


def raw(v, default: float = 0.0) -> float:
    """The bare number, ignoring the unit (librevenge tags angles and counts as inches)."""
    if isinstance(v, dict):
        return float(v["v"])
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def text(v, default: str = "") -> str:
    if isinstance(v, dict):
        return v.get("s", default)
    return v if isinstance(v, str) else default


# --------------------------------------------------------------------------- #

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
# Reader for the pub2json event stream (librevenge drawing interface)


def path_from_actions(actions: list) -> list[SubPath]:
    """librevenge svg:d (absolute, inches) -> cubic subpaths in points."""
    subs: list[SubPath] = []
    pts: list[PPoint] = []
    cx = cy = sx = sy = 0.0

    def finish(closed: bool):
        nonlocal pts
        if len(pts) >= 2:
            if closed:
                f, l = pts[0], pts[-1]
                if abs(f.x - l.x) < 1e-4 and abs(f.y - l.y) < 1e-4:
                    f.lx, f.ly = l.lx, l.ly
                    pts.pop()
            if len(pts) >= 2:
                subs.append(SubPath(pts, closed))
        pts = []

    for a in actions:
        act = a.get("librevenge:path-action", "")
        x, y = num(a.get("svg:x"), cx), num(a.get("svg:y"), cy)
        if act == "M":
            finish(False)
            pts.append(PPoint(x, y, x, y, x, y))
            sx, sy = x, y
        elif act == "L":
            pts.append(PPoint(x, y, x, y, x, y))
        elif act == "H":
            y = cy
            pts.append(PPoint(x, y, x, y, x, y))
        elif act == "V":
            x = cx
            pts.append(PPoint(x, y, x, y, x, y))
        elif act in ("C", "S", "Q", "T"):
            if act == "C":
                x1, y1 = num(a.get("svg:x1")), num(a.get("svg:y1"))
                x2, y2 = num(a.get("svg:x2")), num(a.get("svg:y2"))
            elif act == "S":
                prev = pts[-1] if pts else None
                x1, y1 = (2 * cx - prev.lx, 2 * cy - prev.ly) if prev else (cx, cy)
                x2, y2 = num(a.get("svg:x2")), num(a.get("svg:y2"))
            else:
                qx, qy = (num(a.get("svg:x1")), num(a.get("svg:y1"))) if act == "Q" else (cx, cy)
                x1, y1 = cx + 2 / 3 * (qx - cx), cy + 2 / 3 * (qy - cy)
                x2, y2 = x + 2 / 3 * (qx - x), y + 2 / 3 * (qy - y)
            if not pts:
                pts.append(PPoint(cx, cy, cx, cy, cx, cy))
            pts[-1].rx, pts[-1].ry = x1, y1
            pts.append(PPoint(x, y, x2, y2, x, y))
        elif act == "A":
            if not pts:
                pts.append(PPoint(cx, cy, cx, cy, cx, cy))
            segs = arc_to_cubics(
                cx, cy,
                num(a.get("svg:rx")), num(a.get("svg:ry")),
                raw(a.get("librevenge:rotate")),
                int(raw(a.get("librevenge:large-arc"))), int(raw(a.get("librevenge:sweep"))),
                x, y,
            )
            for x1, y1, x2, y2, ex, ey in segs:
                pts[-1].rx, pts[-1].ry = x1, y1
                pts.append(PPoint(ex, ey, x2, y2, ex, ey))
        elif act == "Z":
            finish(True)
            x, y = sx, sy
        cx, cy = x, y
    finish(False)
    return subs


class PubReader:
    """Builds pages and shapes from the pub2json event stream."""

    def __init__(self, events, flatten_groups: bool = False):
        self.events = events
        self.flatten = flatten_groups
        self.warnings: list[str] = []
        self.skipped: dict[str, int] = {}
        self.style: dict = {}

    def warn(self, msg: str):
        if msg not in self.warnings:
            self.warnings.append(msg)

    # ---- paint ----
    def paint(self, s: Shape, allow_fill: bool = True):
        st = self.style
        stroke = st.get("draw:stroke", "none")
        if stroke != "none":
            s.stroke = st.get("svg:stroke-color", "#000000")
            w = num(st.get("svg:stroke-width"), 0.0)
            s.stroke_weight = w if w > 0 else 0.25  # 0 = hairline
            if stroke == "dash":
                dl = num(st.get("draw:dots1-length"), 0.0)
                if isinstance(st.get("draw:dots1-length"), dict) and st["draw:dots1-length"].get("u") == "%":
                    dl = raw(st["draw:dots1-length"]) / 100 * s.stroke_weight
                gap = num(st.get("draw:distance"), 0.0)
                if isinstance(st.get("draw:distance"), dict) and st["draw:distance"].get("u") == "%":
                    gap = raw(st["draw:distance"]) / 100 * s.stroke_weight
                s.dash = (max(dl or s.stroke_weight * 3, 0.1), max(gap or s.stroke_weight * 2, 0.1))
        if not allow_fill:
            return
        fill = st.get("draw:fill", "none")
        if fill == "solid":
            s.fill = st.get("draw:fill-color", "#000000")
            op = st.get("draw:opacity")
            if isinstance(op, dict) and op.get("u") == "%":
                s.fill_opacity = raw(op) / 100
        elif fill == "gradient":
            grad = st.get("svg:linearGradient") or st.get("svg:radialGradient")
            colour = st.get("draw:start-color") or st.get("draw:fill-color")
            if not colour and isinstance(grad, list) and grad:
                colour = grad[0].get("svg:stop-color")
            s.fill = colour or "#cccccc"
            self.warn("gradient fills are approximated by a solid colour")
        elif fill == "bitmap":
            data = st.get("draw:fill-image")
            if isinstance(data, str) and data:
                try:
                    blob = base64.b64decode(data)
                except ValueError:
                    blob = b""
                size = image_size(blob)
                if size:
                    mime, w, h = size
                    s.image = (blob, mime, w, h)
                else:
                    self.warn(f"image of type {st.get('librevenge:mime-type', '?')} is not supported and was skipped")
            if st.get("style:repeat", "stretch") not in ("stretch", "no-repeat"):
                self.warn("tiled bitmap fills are placed once, stretched to the frame")
        elif fill not in ("none", ""):
            self.warn(f"fill type '{fill}' is not supported")

    # ---- events ----
    def pages(self) -> list[Page]:
        pages: list[Page] = []
        stack: list[list[Shape]] = []  # current container (page / group)
        text_obj: Shape | None = None
        para: Para | None = None
        span_props: list[dict] = [{}]
        page_w = page_h = 0.0

        def add(s: Shape):
            if stack:
                stack[-1].append(s)

        for e in self.events:
            op, p = e["op"], e.get("p", {})
            if op == "startPage":
                page_w = num(p.get("svg:width"), 595.28)
                page_h = num(p.get("svg:height"), 841.89)
                stack = [[]]
            elif op == "endPage":
                pages.append(Page(page_w, page_h, stack[0] if stack else []))
                stack = []
            elif op in ("startLayer", "openGroup"):
                stack.append([])
            elif op in ("endLayer", "closeGroup"):
                if len(stack) > 1:
                    kids = stack.pop()
                    if not kids:
                        continue
                    if self.flatten or len(kids) == 1:
                        stack[-1].extend(kids)
                    else:
                        stack[-1].append(Shape("Group", children=kids))
            elif op == "setStyle":
                self.style = p
            elif op in ("drawPath", "drawPolygon", "drawPolyline"):
                if op == "drawPath":
                    subs = path_from_actions(p.get("svg:d", []))
                else:
                    raw_pts = [(num(q.get("svg:x")), num(q.get("svg:y"))) for q in p.get("svg:points", [])]
                    closed = op == "drawPolygon"
                    if closed and len(raw_pts) > 2 and math.dist(raw_pts[0], raw_pts[-1]) < 1e-4:
                        raw_pts.pop()
                    subs = [SubPath([PPoint(x, y, x, y, x, y) for x, y in raw_pts], closed)] if len(raw_pts) >= 2 else []
                if not subs:
                    continue
                closed = any(sp.closed for sp in subs)
                kind = "Polygon"
                if not closed and len(subs) == 1 and len(subs[0].points) == 2 and all(
                    (pt.x, pt.y) == (pt.lx, pt.ly) == (pt.rx, pt.ry) for pt in subs[0].points
                ):
                    kind = "GraphicLine"
                s = Shape(kind, subs)
                self.paint(s, allow_fill=closed)
                add(s)
            elif op in ("drawRectangle", "drawEllipse"):
                if op == "drawRectangle":
                    x, y = num(p.get("svg:x")), num(p.get("svg:y"))
                    w, h = num(p.get("svg:width")), num(p.get("svg:height"))
                    s = Shape("Rectangle", rect_path(x, y, w, h))
                else:
                    cx, cy = num(p.get("svg:cx")), num(p.get("svg:cy"))
                    rx, ry = num(p.get("svg:rx")), num(p.get("svg:ry"))
                    s = Shape("Oval", ellipse_path(cx - rx, cy - ry, 2 * rx, 2 * ry))
                    rot = raw(p.get("librevenge:rotate"))
                    if rot:
                        m = rotation_about(-math.radians(rot), cx, cy)
                        s.paths = transform_subpaths(s.paths, m)
                self.paint(s)
                add(s)
            elif op == "drawGraphicObject":
                x, y = num(p.get("svg:x")), num(p.get("svg:y"))
                w, h = num(p.get("svg:width")), num(p.get("svg:height"))
                s = Shape("Rectangle", rect_path(x, y, w, h))
                data = p.get("office:binary-data")
                blob = base64.b64decode(data) if isinstance(data, str) else b""
                size = image_size(blob)
                if size:
                    s.image = (blob, size[0], size[1], size[2])
                    add(s)
                else:
                    self.warn(f"embedded graphic of type {p.get('librevenge:mime-type', '?')} is not supported")
            elif op == "drawConnector":
                pts = [(num(q.get("svg:x")), num(q.get("svg:y"))) for q in p.get("svg:points", [])]
                if len(pts) >= 2:
                    s = Shape("GraphicLine", [SubPath([PPoint(x, y, x, y, x, y) for x, y in pts], False)])
                    self.paint(s, allow_fill=False)
                    add(s)
            elif op == "startTextObject":
                x, y = num(p.get("svg:x")), num(p.get("svg:y"))
                w, h = num(p.get("svg:width")), num(p.get("svg:height"))
                rot = raw(p.get("librevenge:rotate"))
                # librevenge: the box rotates about its centre; positive = counter-clockwise
                m = (1.0, 0.0, 0.0, 1.0, x, y)
                if rot:
                    m = mul(rotation_about(-math.radians(rot), x + w / 2, y + h / 2), m)
                text_obj = Shape("TextFrame", rect_path(0, 0, w, h), item_transform=m, story=[])
                # a text box is painted only by its own properties, not by the last setStyle
                saved, self.style = self.style, {"draw:fill": "none", "draw:stroke": "none", **p}
                self.paint(text_obj)
                self.style = saved
                text_obj.insets = (
                    num(p.get("fo:padding-top")),
                    num(p.get("fo:padding-left")),
                    num(p.get("fo:padding-bottom")),
                    num(p.get("fo:padding-right")),
                )
                va = text(p.get("draw:textarea-vertical-align"), "top")
                text_obj.valign = {"middle": "CenterAlign", "bottom": "BottomAlign", "justify": "JustifyAlign"}.get(
                    va, "TopAlign"
                )
            elif op == "endTextObject":
                if text_obj is not None:
                    paras = text_obj.story or []
                    for pa in paras:  # libmspub ends every paragraph with \r
                        while pa.runs and not pa.runs[-1].text.rstrip("\r\n"):
                            pa.runs.pop()
                        if pa.runs:
                            pa.runs[-1].text = pa.runs[-1].text.rstrip("\r\n")
                    while paras and not paras[-1].runs:
                        paras.pop()
                    has_text = any(r.text.strip() for pa in paras for r in pa.runs)
                    if has_text or text_obj.fill or text_obj.stroke:
                        add(text_obj)
                text_obj = None
            elif op == "openParagraph":
                align = {"center": "CenterAlign", "right": "RightAlign", "end": "RightAlign", "justify": "LeftJustified"}.get(
                    text(p.get("fo:text-align"), "left"), "LeftAlign"
                )
                para = Para([], align)
                span_props = [p]
            elif op == "closeParagraph":
                if text_obj is not None and para is not None:
                    text_obj.story.append(para)
                para = None
            elif op == "openSpan":
                merged = dict(span_props[0])
                merged.update(p)
                span_props.append(merged)
            elif op == "closeSpan":
                if len(span_props) > 1:
                    span_props.pop()
            elif op in ("insertText", "insertSpace", "insertTab", "insertLineBreak"):
                if para is None:
                    continue
                t = {"insertSpace": " ", "insertTab": "\t", "insertLineBreak": " "}.get(op, e.get("t", ""))
                para.runs.append(self.make_run(t, span_props[-1]))
            elif op == "startTableObject":
                self.warn("tables are not supported yet and were skipped")
                self.skipped["table"] = self.skipped.get("table", 0) + 1
            elif op in ("startDocument", "endDocument", "setDocumentMetaData", "closeLink", "openLink",
                        "openUnorderedListLevel", "openOrderedListLevel", "closeUnorderedListLevel",
                        "closeOrderedListLevel", "openListElement", "closeListElement", "defineParagraphStyle",
                        "defineCharacterStyle", "defineEmbeddedFont", "startMasterPage", "endMasterPage",
                        "startEmbeddedGraphics", "endEmbeddedGraphics", "openTableRow", "closeTableRow",
                        "openTableCell", "closeTableCell", "insertCoveredTableCell", "endTableObject", "insertField"):
                pass
            else:
                self.skipped[op] = self.skipped.get(op, 0) + 1
        return pages

    @staticmethod
    def make_run(t: str, sp: dict) -> Run:
        weight = text(sp.get("fo:font-weight"), "normal")
        bold = weight in ("bold", "600", "700", "800", "900")
        italic = text(sp.get("fo:font-style"), "normal") in ("italic", "oblique")
        style = "Bold Italic" if bold and italic else "Bold" if bold else "Italic" if italic else "Regular"
        size = num(sp.get("fo:font-size"), 12.0) if not (isinstance(sp.get("fo:font-size"), dict) and sp["fo:font-size"].get("u") == "%") else 12.0
        return Run(
            t,
            font=text(sp.get("style:font-name"), "Calibri") or "Calibri",
            style=style,
            size=size,
            color=text(sp.get("fo:color"), "#000000"),
            underline=text(sp.get("style:text-underline-type"), "none") not in ("none", "")
            or text(sp.get("style:text-underline-style"), "none") not in ("none", ""),
            strike=text(sp.get("style:text-line-through-type"), "none") not in ("none", "")
            or text(sp.get("style:text-line-through-style"), "none") not in ("none", ""),
        )


def rotation_about(a: float, cx: float, cy: float) -> Matrix:
    """Rotate by a radians (counter-clockwise on screen, y down) about (cx, cy)."""
    c, s = math.cos(a), math.sin(a)
    return mul((1.0, 0.0, 0.0, 1.0, cx, cy), mul((c, -s, s, c, 0.0, 0.0), (1.0, 0.0, 0.0, 1.0, -cx, -cy)))



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

EXE = ".exe" if os.name == "nt" else ""


def find_pub2json() -> str:
    """pub2json next to this program (or inside a PyInstaller bundle), then on PATH."""
    here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    candidates = [
        os.environ.get("PUB2JSON", ""),
        os.path.join(getattr(sys, "_MEIPASS", here), "pub2json" + EXE),
        os.path.join(here, "pub2json" + EXE),
        os.path.join(here, "pub2json", "build", "pub2json" + EXE),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            if os.name != "nt" and not os.access(c, os.X_OK):
                try:
                    os.chmod(c, 0o755)
                except OSError:
                    pass
            return c
    found = shutil.which("pub2json")
    if found:
        return found
    raise SystemExit("pub2json not found - build it from pub2json/ (see README) or put it next to pub2idml")


def read_events(src: str) -> list:
    if src.lower().endswith((".jsonl", ".json")):
        with open(src, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    r = subprocess.run([find_pub2json(), src], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300, **kwargs)
    if r.returncode != 0:
        msg = r.stderr.decode("utf-8", "replace").strip() or f"pub2json exited with code {r.returncode}"
        raise RuntimeError(msg)
    return [json.loads(line) for line in r.stdout.decode("utf-8", "replace").splitlines() if line.strip()]


def convert(src: str, out: str, flatten: bool = False, verbose: bool = False) -> list[str]:
    reader = PubReader(read_events(src), flatten_groups=flatten)
    pages = reader.pages()
    if not pages:
        raise RuntimeError("no pages found")
    IdmlWriter(pages).write(out)
    msgs = list(reader.warnings)
    for k, v in reader.skipped.items():
        msgs.append(f"skipped {v} unsupported element(s): {k}")
    if verbose:
        n = sum(count(s) for p in pages for s in p.shapes)
        print(f"{src}: {len(pages)} page(s), {n} item(s) -> {out}")
    return msgs


def count(s: Shape) -> int:
    return 1 + sum(count(c) for c in s.children)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pub2idml", description="Convert Microsoft Publisher (.pub) files to IDML.")
    ap.add_argument("input", nargs="?", help=".pub file (or a .jsonl stream from pub2json)")
    ap.add_argument("-o", "--output", help="output .idml (or output directory with --batch)")
    ap.add_argument("--batch", metavar="DIR", help="convert every .pub in DIR")
    ap.add_argument("--flatten-groups", action="store_true", help="do not keep groups")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=f"pub2idml {__version__}")
    a = ap.parse_args(argv)

    jobs = []
    if a.batch:
        outdir = a.output or a.batch
        os.makedirs(outdir, exist_ok=True)
        for f in sorted(os.listdir(a.batch)):
            if f.lower().endswith(".pub"):
                jobs.append((os.path.join(a.batch, f), os.path.join(outdir, os.path.splitext(f)[0] + ".idml")))
        if not jobs:
            print(f"no .pub files in {a.batch}", file=sys.stderr)
            return 1
    elif a.input:
        jobs.append((a.input, a.output or os.path.splitext(a.input)[0] + ".idml"))
    else:
        ap.print_help()
        return 2

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


def _own_console_on_windows() -> bool:
    """True when pub2idml.exe was started by double-click / drag-and-drop (it owns its console)."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    try:
        import ctypes

        return ctypes.windll.kernel32.GetConsoleProcessList((ctypes.c_uint * 2)(), 2) <= 1
    except Exception:
        return False


if __name__ == "__main__":
    pause = _own_console_on_windows()
    if pause and len(sys.argv) > 1 and all(a.lower().endswith(".pub") for a in sys.argv[1:]):
        # drag-and-drop of several files onto pub2idml.exe
        rc = 0
        for f in sys.argv[1:]:
            rc |= main([f, "-v"])
    else:
        rc = main()
    if pause:
        input("\nEnter druecken zum Beenden / press Enter to close ...")
    sys.exit(rc)

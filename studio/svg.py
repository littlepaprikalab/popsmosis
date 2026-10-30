"""Brain-drawn SVG art (docs/PLAN.md, step 3, until the Meta key arrives).

The brain draws each scene as one SVG, every structure as one element marked
data-structure="<name>". This module checks such a drawing, rasterises it, and finds
each structure in it, so the art step can write the art, the masks and locations.json
without a locate step.

    check(text, structures)        what is wrong with a drawing, before anything is drawn
    style(root, structures, graph) the drawing in the house look (below)
    rasterise(root)                the art: RGB on the house page, ART_WIDTH x ART_HEIGHT
    masks(root, names)             per structure, where it is visible in the art
    locate(mask)                   its box, centroid and label anchor
    values(root)                   the numbers it marks for the video to write (a bar's amount, an axis's tick)

A mask is the structure painted white and everything else black, in the drawing's own
order, so whatever is drawn on top of a structure is left out of its mask.

The house look. The brain decides what a scene draws and where; code decides how it looks, the same in every
scene of every droplet: drawn one scene to a call, the brain's own styling drifted (one plan's graphs came back
with axes in three greys and lines 16 to 22 px wide), and gradients, shadows and highlights were over half of
each drawing's tokens. Every drawing goes on PAGE, the dark slate blue the painted art is asked for and the
bundle's colours are picked to stand out on. A graph (a scene with axes, Scene.axes) also has its lines drawn
flat at one weight with a soft glow of their own colour, its axes (the brain marks them <g data-role="axes">)
pale with arrowheads over a faint grid, and its points (<circle data-role="point">) as rings. The art step keeps
the drawing as the brain made it and styles it before it is rasterised, so a new look is a new LOOK and no new
drawing: the step styles the kept drawings again, with no call to the brain (studio/steps/art.py).
"""

from __future__ import annotations

import copy
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np
import skia

from studio.media import ART_HEIGHT, ART_WIDTH
from studio.schemas import Box, Point, Structure, Value

NS = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
ET.register_namespace("", NS)
ET.register_namespace("xlink", XLINK)

MARK = "data-structure"
# A number the video writes on a scene drawn to scale, marked where it goes by an empty circle directly in the <svg>:
# <circle data-value="$1,210" data-side="above" cx="1230" cy="140" r="0"/>, or data-tick for an axis's tick ("Year 2").
VALUE, TICK, SIDE = "data-value", "data-tick", "data-side"
SIDES = ("above", "below", "left", "right")
VALUES_MAX = 16  # a scene's values: more is a table, not a chart
VIEWBOX = (0.0, 0.0, float(ART_WIDTH), float(ART_HEIGHT))
MAX_BYTES = 400_000  # the prompt asks for under 40 KB; far past that it is runaway output
MIN_AREA = 1500  # visible pixels a structure needs before a label can point at it (about 40 x 40)

SHAPES = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}
FILTERS = {"feGaussianBlur", "feOffset", "feFlood", "feComposite", "feMerge", "feMergeNode", "feBlend",
           "feColorMatrix"}
# Everything else is refused: text and images (no text in the art), scripts, styles
# (CSS would beat the colours the masks paint), <use> (skia does not draw it), links.
ALLOWED = {"svg", "g", "defs", "linearGradient", "radialGradient", "stop", "clipPath", "mask", "filter"} \
    | SHAPES | FILTERS
# Where nothing is drawn directly; their children are left alone when painting masks.
NOT_DRAWN = {"defs", "linearGradient", "radialGradient", "clipPath", "mask", "filter"}
PAINT_PROPS = ("fill", "stroke", "opacity", "fill-opacity", "stroke-opacity", "filter", "mask")
URL = re.compile(r"url\(\s*['\"]?([^)'\"]*)['\"]?\s*\)")


@dataclass
class Checked:
    """A drawing, parsed, and what is wrong with it.

    `problems` mean it cannot be used at all. `structures` are problems with one
    listed structure: that structure can be left out. `notes` are worth fixing on a
    retry but harmless (a name the scene does not list is drawn as plain art).
    """

    root: ET.Element | None
    problems: list[str] = field(default_factory=list)
    structures: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def all(self) -> list[str]:
        return self.problems + list(self.structures.values()) + self.notes


def extract(answer: str) -> str:
    """The <svg>...</svg> in the brain's answer, without code fences or talk around it."""
    start, end = answer.find("<svg"), answer.rfind("</svg>")
    if start < 0 or end < start:
        return answer.strip()
    return answer[start:end + len("</svg>")]


def check(text: str, structures: list[Structure]) -> Checked:
    """Parse a drawing and check it against the scene's structures."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        return Checked(None, [f"the SVG is {len(text) // 1000} KB; keep it under 40 KB with smooth curves"])
    # No DTD at all: that rules out entity expansion and external entities before parsing.
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.IGNORECASE):
        return Checked(None, ["the SVG must not have a DOCTYPE or entities"])
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        return Checked(None, [f"the SVG is not well-formed XML: {e}"])
    checked = Checked(root)
    problems = checked.problems
    if root.tag != f"{{{NS}}}svg":
        return Checked(None, ['the root must be <svg xmlns="http://www.w3.org/2000/svg">'])
    try:
        viewbox = tuple(float(v) for v in re.split(r"[\s,]+", root.get("viewBox", "").strip()))
    except ValueError:
        viewbox = ()
    if viewbox != VIEWBOX:
        problems.append(f'the viewBox must be "0 0 {ART_WIDTH} {ART_HEIGHT}", not {root.get("viewBox")!r}')

    refused, ids = set(), {}
    for el in root.iter():
        tag = _local(el.tag)
        if tag is None or tag not in ALLOWED:
            refused.add(f"<{tag or el.tag}>")
        if el.text and el.text.strip() and tag not in ("svg", "g", "defs"):
            refused.add(f"text inside <{tag}>")
        if el.get("id"):
            ids[el.get("id")] = el
        for key, value in el.attrib.items():
            name = _local(key) or key
            if name.lower().startswith("on"):
                refused.add(f"the {name} attribute")
            elif name == "href" and not value.startswith("#"):
                refused.add(f"an external link ({value[:40]})")
            for ref in URL.findall(value):
                if not ref.startswith("#"):
                    refused.add(f"an external reference ({ref[:40]})")
            if "@import" in value or "expression(" in value:
                refused.add(f"CSS in the {name} attribute")
    if refused:
        problems.append("the SVG must not contain " + ", ".join(sorted(refused))
                        + f"; allowed elements are {', '.join(sorted(ALLOWED))}")

    # A name marked with another case or spacing ("ATP Synthase") still counts, and is
    # rewritten to the listed name, which is what the masks look for.
    canon = {_squash(s.name): s.name for s in structures}
    marked: dict[str, list[ET.Element]] = {}
    for el in root.iter():
        if el.get(MARK) is not None:
            name = canon.get(_squash(el.get(MARK)), el.get(MARK))
            el.set(MARK, name)
            marked.setdefault(name, []).append(el)
    listed = {s.name for s in structures}
    for s in structures:
        found = marked.get(s.name, [])
        if not found:
            checked.structures[s.name] = f'{s.name} is not drawn: no element has {MARK}="{s.name}"'
        elif len(found) > 1:
            checked.structures[s.name] = (f'{s.name} is marked {len(found)} times; put all of it in one '
                                          f'<g {MARK}="{s.name}">')
        elif s.color_hex not in _colors(found[0], ids):
            checked.structures[s.name] = (f"{s.name} is not drawn in its colour {s.color_hex}: use it as the "
                                          "main fill, or as a stop of the gradient that fills it")
    unknown = sorted(set(marked) - listed)
    if unknown:
        checked.notes.append(f"{', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} marked but not in "
                             f"this scene's list; mark only {', '.join(sorted(listed))}")
    return checked


def rasterise(root: ET.Element) -> np.ndarray:
    """The art: ART_HEIGHT x ART_WIDTH x 3, RGB on PAGE."""
    r, g, b = (int(PAGE[i:i + 2], 16) for i in (1, 3, 5))
    return _render(_prepared(root), skia.Color(r, g, b))[:, :, :3].copy()


def masks(root: ET.Element, names: list[str]) -> dict[str, np.ndarray]:
    """Per structure, a bool array: True where it is visible in the art.

    A soft shadow or glow drawn inside the structure's own group (see-through, under a
    filter that only blurs) is not the structure, so it is left out of the mask, unless
    the structure is nothing else. The house's glow (style(): a layer marked data-role="glow",
    each copy in it marked data-glow-of its structure) is part of how the structure looks, so it
    is in the mask: the renderer's washes then dim it with its line while the line is a ghost,
    and leave it lit while the line is the spotlight.
    """
    prepared = _prepared(root)
    found = {}
    for name in names:
        mask = _render(_painted(prepared, name), skia.ColorBLACK)[:, :, 0] >= 128
        if not mask.any():
            mask = _render(_painted(prepared, name, keep_soft=True), skia.ColorBLACK)[:, :, 0] >= 128
        found[name] = mask
    return found


@dataclass
class Found:
    box: Box
    centroid: Point
    label_anchor: Point
    area: int


def locate(mask: np.ndarray, anchor_in: np.ndarray | None = None) -> Found | None:
    """Where a structure is, from its mask; None when too little of it shows to label. Its label anchor is found in
    `anchor_in` instead when that holds enough of it (a line without the points marked on it, which are wider than
    the line, so the anchor and the pill beside it would sit on a point)."""
    area = int(mask.sum())
    if area < MIN_AREA:
        return None
    ys, xs = np.nonzero(mask)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    cx, cy = float(xs.mean()) + 0.5, float(ys.mean()) + 0.5
    within = mask if anchor_in is None or int((anchor_in & mask).sum()) < MIN_AREA else anchor_in & mask
    anchor = _deepest(within[y0:y1, x0:x1], cx - x0, cy - y0)
    return Found(box=Box(x0=x0, y0=y0, x1=x1, y1=y1), centroid=Point(x=round(cx, 1), y=round(cy, 1)),
                 label_anchor=Point(x=round(anchor[0] + x0, 1), y=round(anchor[1] + y0, 1)), area=area)


def png(pixels: np.ndarray) -> bytes:
    """An 8-bit PNG: greyscale from a 2-D array, RGB from an H x W x 3 one."""
    if pixels.dtype == bool:
        pixels = pixels.astype(np.uint8) * 255
    if pixels.ndim == 2:
        image = skia.Image.fromarray(np.ascontiguousarray(pixels), colorType=skia.kGray_8_ColorType)
    else:
        rgbx = np.dstack([pixels, np.full(pixels.shape[:2], 255, np.uint8)])
        image = skia.Image.fromarray(rgbx, colorType=skia.kRGB_888x_ColorType)
    return bytes(image.encodeToData(skia.kPNG, 100))


def to_text(root: ET.Element) -> str:
    return ET.tostring(root, encoding="unicode")


# --- the house look ---------------------------------------------------------------------

LOOK = "2"  # the look below, kept with each scene's locations: change it whenever what it draws changes (a
# test pins the look's code and numbers to it, studio/tests/test_art.py)
PAGE = "#28374B"  # the dark slate blue behind every drawing
ROLE = "data-role"  # what a graph's context is: "axes" on the group of its axes, "point" on a point's circle
LINE_WIDTH = 8.0  # a graph's lines, whatever width they were drawn
BAND = 28.0  # a stroke wider than this is a band (a region drawn as one stroke), not a line: it keeps its width
AXIS_WIDTH = 3.0
AXIS_SHARE = 0.3  # an axis spans this share of the canvas's width (across) or height (up) at least; shorter is a tick
POINT_WIDTH = 5.0  # a point's ring
ARROW = 16.0  # an axis's arrowhead, from its tip back
GRID_STEP = 72.0  # between a graph's grid lines, from where its axes cross
GLOW = "house-glow"  # the id of the glow's filter
GLOW_OF = "data-glow-of"  # on each structure's copy in the glow: whose glow it is
GLOW_BLUR, GLOW_SPREAD, GLOW_OPACITY = 7.0, 3.0, 0.45  # its blur, its width as a multiple of the line's, how strong
# A filled shape's outline, this share of the way from its colour to the page: in its own colour, the year-by-year
# blocks of round 22's compound-interest bars (one structure, "Interest") ran together into one slab, and the chart was
# about each block being a little taller than the last.
EDGE_SHADE = 0.4
# What a repaint replaces. A mask or a clip, which shape what is drawn, and a dash, which may mean something (an
# asymptote), are kept.
REPAINTED = ("fill", "stroke", "opacity", "fill-opacity", "stroke-opacity", "filter", "stroke-width", "stroke-linecap",
             "stroke-linejoin", "stroke-dasharray")
NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
PATH_TOKEN = re.compile(rf"[A-Za-z]|{NUMBER.pattern}")


def _mix(color: str, white: float, toward: str = "#FFFFFF") -> str:
    """The colour that share of the way to white, or to another colour."""
    return "#" + "".join(f"{round(int(color[i:i + 2], 16) * (1 - white) + int(toward[i:i + 2], 16) * white):02X}"
                         for i in (1, 3, 5))


AXIS_INK = _mix(PAGE, 0.55)  # pale: studio/render/axes.py finds the axes as lines well apart from the page
GRID_INK = _mix(PAGE, 0.07)  # a shade over the page, too faint to be found as an axis


def values(root: ET.Element) -> list[Value]:
    """The values a drawing marks for the video to write (VALUE, TICK), in its order: each where its circle is, on
    the side it says (above, when it says none). Only marks directly in the <svg> count, as a group's move would
    shift them; at most VALUES_MAX."""
    found = []
    for el in root:
        text = (el.get(VALUE) or el.get(TICK) or "").strip()
        if _local(el.tag) != "circle" or not text:
            continue
        x, y = _number(el.get("cx")), _number(el.get("cy"))
        if not (0 <= x <= ART_WIDTH and 0 <= y <= ART_HEIGHT):
            continue
        side = el.get(SIDE) if el.get(SIDE) in SIDES else "above"
        found.append(Value(text=" ".join(text.split()), at=Point(x=x, y=y), side=side, tick=el.get(VALUE) is None))
    return found[:VALUES_MAX]


def style(root: ET.Element, structures: list[Structure], graph: bool) -> ET.Element:
    """A copy of a checked drawing in the house look: on PAGE, without a background of its own, and a graph's
    lines, axes and points drawn as the house draws them (the module's docstring). Shapes keep their places, so
    masks found on the copy are where the brain drew each structure."""
    root = copy.deepcopy(root)
    parent = {child: el for el in root.iter() for child in el}
    for el in [el for el in root.iter(f"{{{NS}}}rect")
               if _drawn(el, parent) and _owner(el, parent) is None and _covers_page(el)]:
        parent[el].remove(el)  # a page of its own would hide PAGE, which the renderer reads off the art's edge
    for el in [el for el in root.iter() if el.get(VALUE) is not None or el.get(TICK) is not None]:
        parent[el].remove(el)  # a value's mark: the renderer writes the value there (values())
    if graph:
        _style_graph(root, parent, {s.name: s.color_hex for s in structures})
    return root


def _style_graph(root: ET.Element, parent: dict, colours: dict[str, str]) -> None:
    defs = root.find(f"{{{NS}}}defs")
    if defs is None:
        defs = ET.Element(f"{{{NS}}}defs")
        root.insert(0, defs)
    # On the whole canvas: a line across has no height, so a filter on its own box would draw nothing.
    glow = ET.SubElement(defs, f"{{{NS}}}filter", id=GLOW, filterUnits="userSpaceOnUse", x="0", y="0",
                         width=str(ART_WIDTH), height=str(ART_HEIGHT))
    ET.SubElement(glow, f"{{{NS}}}feGaussianBlur", stdDeviation=f"{GLOW_BLUR:g}")
    # The brain's shading: shadows and highlights, see-through or blurred shapes in black or white.
    for el in [el for el in root.iter() if _local(el.tag) in SHAPES and _drawn(el, parent) and _shading(el, parent)]:
        parent[el].remove(el)
    for axes in [g for g in root.iter(f"{{{NS}}}g")
                 if g.get(ROLE) == "axes" and _drawn(g, parent) and _owner(g, parent) is None]:
        _style_axes(root, defs, axes, parent)
    groups = [g for g in root.iter() if g.get(MARK) in colours and _drawn(g, parent)]
    for group in groups:
        _style_structure(group, colours[group.get(MARK)], parent)
    if groups:
        _glow(root, groups, parent)


def _style_axes(root: ET.Element, defs: ET.Element, axes: ET.Element, parent: dict) -> None:
    """Pale, thin axes with an arrowhead at the far end of each (right, up), over a faint grid from where they
    cross. An axis is a straight line, path or polyline across or up most of the canvas; a tick is too short."""
    across, up = [], []
    for el in [el for el in axes.iter() if _local(el.tag) in SHAPES and _drawn(el, parent)]:
        _repaint(el, fill="none", stroke=AXIS_INK, **{"stroke-width": f"{AXIS_WIDTH:g}", "stroke-linecap": "round"})
        if _moved(el, axes, parent):
            continue  # its coordinates are not the axes group's
        for x1, y1, x2, y2 in _segments(el):
            if abs(y2 - y1) <= 0.05 * abs(x2 - x1) and abs(x2 - x1) >= AXIS_SHARE * ART_WIDTH:
                across.append((min(x1, x2), max(x1, x2), (y1 + y2) / 2))
            elif abs(x2 - x1) <= 0.05 * abs(y2 - y1) and abs(y2 - y1) >= AXIS_SHARE * ART_HEIGHT:
                up.append((min(y1, y2), max(y1, y2), (x1 + x2) / 2))
    a, w = ARROW, ARROW * 0.6
    for _, x1, y in across:
        ET.SubElement(axes, f"{{{NS}}}polygon", points=f"{x1 + a:g},{y:g} {x1:g},{y - w:g} {x1:g},{y + w:g}",
                      fill=AXIS_INK)
    for y0, _, x in up:
        ET.SubElement(axes, f"{{{NS}}}polygon", points=f"{x:g},{y0 - a:g} {x - w:g},{y0:g} {x + w:g},{y0:g}",
                      fill=AXIS_INK)
    if not (across and up):
        return
    left, right = min(x0 for x0, _, _ in across), max(x1 for _, x1, _ in across)
    top, bottom = min(y0 for y0, _, _ in up), max(y1 for _, y1, _ in up)
    cx = max(up, key=lambda line: line[1] - line[0])[2]  # where the longest of each crosses
    cy = max(across, key=lambda line: line[1] - line[0])[2]
    grid = ET.Element(f"{{{NS}}}g")
    # Each line carries its own paint: skia does not hand a group's stroke down to the lines in it.
    paint = {"stroke": GRID_INK, "stroke-width": "1.5"}
    for x in _steps(cx, left, right):
        ET.SubElement(grid, f"{{{NS}}}line", x1=f"{x:g}", y1=f"{top:g}", x2=f"{x:g}", y2=f"{bottom:g}", **paint)
    for y in _steps(cy, top, bottom):
        ET.SubElement(grid, f"{{{NS}}}line", x1=f"{left:g}", y1=f"{y:g}", x2=f"{right:g}", y2=f"{y:g}", **paint)
    root.insert(list(root).index(defs) + 1, _placed(grid, axes, parent, root))  # under everything


def _steps(origin: float, low: float, high: float) -> list[float]:
    """Every GRID_STEP from origin strictly inside (low, high), the origin left out: an axis is drawn there."""
    first, last = math.ceil((low - origin) / GRID_STEP), math.floor((high - origin) / GRID_STEP)
    return [origin + k * GRID_STEP for k in range(first, last + 1) if k and low < origin + k * GRID_STEP < high]


def _style_structure(group: ET.Element, colour: str, parent: dict) -> None:
    """A graph's structure, flat in its colour: a line at LINE_WIDTH (a band keeps its width), a filled shape (a
    region, a marked corner) filled with its colour where it had a gradient, a point a ring on the page. A line's
    dash and a see-through line or region (an earlier secant drawn faded) keep what they mean."""
    for el in group.iter():
        if not _drawn(el, parent):
            continue  # in a mask, a clip or a gradient: its paint shapes what is drawn
        el.attrib.pop("filter", None)  # the brain's own shadow or glow: the house draws its own
        if _local(el.tag) not in SHAPES:
            continue
        fill = _inherited(el, parent, "fill", "none" if _local(el.tag) == "line" else "black")
        stroke = _inherited(el, parent, "stroke", "none")
        kept = {k: v for k in ("opacity", "fill-opacity", "stroke-opacity", "stroke-dasharray")
                if (v := _prop(el, k)) is not None}
        if el.get(ROLE) == "point":
            _repaint(el, fill=PAGE, stroke=colour, **{"stroke-width": f"{POINT_WIDTH:g}"}, **kept)
        elif fill == "none":
            width = _number(_inherited(el, parent, "stroke-width", "1"), 1.0)
            _repaint(el, fill="none", stroke=colour, **{"stroke-width": f"{width if width > BAND else LINE_WIDTH:g}",
                                                        "stroke-linecap": "round", "stroke-linejoin": "round"}, **kept)
        else:
            if (width := _prop(el, "stroke-width")) is not None:
                kept["stroke-width"] = width  # an outline keeps its width, a shade darker than the structure
            _repaint(el, fill=colour if URL.search(fill) else fill,
                     stroke=_mix(colour, EDGE_SHADE, PAGE) if stroke != "none" else "none", **kept)


def _glow(root: ET.Element, groups: list[ET.Element], parent: dict) -> None:
    """One layer under every structure, of a copy of each in its colour, wider, blurred and see-through: one blur
    for the whole graph, however many structures it has (a blur each took 0.2 s apiece). Each copy names its
    structure (GLOW_OF), and masks() counts it as that structure's and no other's."""
    layer = ET.Element(f"{{{NS}}}g", {ROLE: "glow", "filter": f"url(#{GLOW})", "opacity": f"{GLOW_OPACITY:g}"})
    for group in groups:
        halo = copy.deepcopy(group)
        halo.set(GLOW_OF, halo.attrib.pop(MARK))
        for el in halo.iter():
            el.attrib.pop("id", None)
            if el is not halo:
                el.attrib.pop(MARK, None)  # a point's copy stays a point, so without_points() leaves out its glow too
            if _local(el.tag) in SHAPES and el.get("stroke", "none") != "none":
                el.set("stroke-width", f"{_number(el.get('stroke-width'), 1.0) * GLOW_SPREAD:g}")
        layer.append(_placed(halo, parent.get(group), parent, root))
    top = groups[0]
    while parent.get(top) is not root:
        top = parent[top]
    root.insert(list(root).index(top), layer)


def _placed(el: ET.Element, inside: ET.Element | None, parent: dict, root: ET.Element) -> ET.Element:
    """el, to go at the root, wrapped in the transforms of `inside` and the groups it is in, so it lands where it
    would inside `inside`."""
    at = inside
    while at is not None and at is not root:
        if at.get("transform"):
            wrapper = ET.Element(f"{{{NS}}}g", transform=at.get("transform"))
            wrapper.append(el)
            el = wrapper
        at = parent.get(at)
    return el


def _segments(el: ET.Element) -> list[tuple[float, float, float, float]]:
    """The straight pieces of a line, a polyline or a path of straight moves only (M, L, H, V, Z), each as
    (x1, y1, x2, y2); [] for anything with a curve in it, or that does not read."""
    tag = _local(el.tag)
    try:
        if tag == "line":
            return [(_number(el.get("x1")), _number(el.get("y1")), _number(el.get("x2")), _number(el.get("y2")))]
        if tag == "polyline":
            v = [float(n) for n in NUMBER.findall(el.get("points", ""))]
            points = list(zip(v[0::2], v[1::2]))
            return [(*a, *b) for a, b in zip(points, points[1:])]
        if tag != "path":
            return []
        tokens, i, out = PATH_TOKEN.findall(el.get("d", "")), 0, []
        x = y = 0.0
        start, command = (0.0, 0.0), None
        while i < len(tokens):
            if tokens[i].isalpha():
                command, i = tokens[i], i + 1
            if command is None or command.upper() not in "MLHVZ":
                return []
            relative, kind = command.islower(), command.upper()
            if kind == "Z":
                out.append((x, y, *start))
                (x, y), command = start, None
            elif kind in "ML":
                nx, ny = float(tokens[i]), float(tokens[i + 1])
                i += 2
                if relative:
                    nx, ny = x + nx, y + ny
                if kind == "M":
                    start, command = (nx, ny), ("l" if relative else "L")  # pairs after a move are lines
                else:
                    out.append((x, y, nx, ny))
                x, y = nx, ny
            else:
                n = float(tokens[i])
                i += 1
                nx, ny = ((x + n if relative else n), y) if kind == "H" else (x, (y + n if relative else n))
                out.append((x, y, nx, ny))
                x, y = nx, ny
        return out
    except (ValueError, IndexError):
        return []


def without_points(root: ET.Element) -> ET.Element:
    """A copy without the points marked on its lines (data-role="point") and their glow: where a line's label
    anchor is found (locate's anchor_in)."""
    root = copy.deepcopy(root)
    parent = {child: el for el in root.iter() for child in el}
    for el in [el for el in root.iter() if el.get(ROLE) == "point"]:
        parent[el].remove(el)
    return root


def _prop(el: ET.Element, key: str) -> str | None:
    """A presentation property of the element itself, its style attribute first."""
    value = _style(el).get(key)
    return value if value is not None else el.get(key)


def _inherited(el: ET.Element, parent: dict, key: str, default: str) -> str:
    """A property the element is drawn with, its own or the nearest group's that sets it."""
    at = el
    while at is not None:
        value = _prop(at, key)
        if value is not None and value != "inherit":
            return value
        at = parent.get(at)
    return default


def _repaint(el: ET.Element, **props: str) -> None:
    """Set an element's paint, dropping whatever REPAINTED it had, in attributes or its style."""
    for key in REPAINTED:
        el.attrib.pop(key, None)
    rest = [p for p in el.get("style", "").split(";") if p.strip() and p.partition(":")[0].strip() not in REPAINTED]
    if rest:
        el.set("style", ";".join(rest))
    else:
        el.attrib.pop("style", None)
    for key, value in props.items():
        el.set(key, value)


def _drawn(el: ET.Element, parent: dict) -> bool:
    """Whether an element is drawn where it is, rather than kept in defs, a gradient, a clip or a mask."""
    while el is not None:
        if _local(el.tag) in NOT_DRAWN:
            return False
        el = parent.get(el)
    return True


def _owner(el: ET.Element, parent: dict) -> str | None:
    """The structure an element belongs to, or None for context."""
    while el is not None:
        if el.get(MARK) is not None:
            return el.get(MARK)
        el = parent.get(el)
    return None


def _moved(el: ET.Element, within: ET.Element, parent: dict) -> bool:
    """Whether a transform moves the element inside `within`: on itself or a group between them."""
    while el is not None and el is not within:
        if el.get("transform"):
            return True
        el = parent.get(el)
    return False


NAMED = {"black": "#000000", "white": "#FFFFFF"}


def _shading(el: ET.Element, parent: dict) -> bool:
    """A shadow or a highlight: black or near white, and see-through or blurred, itself or by a group it is in."""
    fill = _inherited(el, parent, "fill", "none" if _local(el.tag) == "line" else "black")
    key = "fill" if fill != "none" else "stroke"
    paint = fill if fill != "none" else _inherited(el, parent, "stroke", "none")
    color = _hex(NAMED.get(paint, paint))
    if color is None or not (color == "#000000" or min(int(color[i:i + 2], 16) for i in (1, 3, 5)) >= 0xC8):
        return False
    if _opacity(_inherited(el, parent, f"{key}-opacity", "1")) < 1:
        return True
    at = el
    while at is not None:
        if _opacity(_prop(at, "opacity")) < 1 or _prop(at, "filter"):
            return True
        at = parent.get(at)
    return False


def _covers_page(rect: ET.Element) -> bool:
    """A rectangle over the whole canvas: a background."""
    def size(key: str, whole: float) -> float:
        value = rect.get(key, "0").strip()
        return whole * _number(value[:-1]) / 100 if value.endswith("%") else _number(value)
    x, y = size("x", ART_WIDTH), size("y", ART_HEIGHT)
    return (x <= 0 and y <= 0 and x + size("width", ART_WIDTH) >= ART_WIDTH
            and y + size("height", ART_HEIGHT) >= ART_HEIGHT)


def _number(value: str | None, default: float = 0.0) -> float:
    try:
        return float(re.sub(r"(px)?\s*$", "", value or ""))
    except ValueError:
        return default


# --- drawing ------------------------------------------------------------------------


def _local(tag: str) -> str | None:
    """An element or attribute's name without the SVG namespace; None for another namespace."""
    if not isinstance(tag, str):
        return None  # a comment or processing instruction
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return name if ns in (NS, XLINK) else None
    return tag


def _prepared(root: ET.Element) -> ET.Element:
    """A copy ready for skia: the canvas size fixed, and every <ellipse> made a <path>.

    skia gets an ellipse's bounding box wrong, so a gradient on one is drawn black, and a
    filter on one, or on a group holding one, draws nothing. The same shape as a path
    is drawn right.
    """
    root = copy.deepcopy(root)
    for key in ("width", "height", "preserveAspectRatio"):
        root.attrib.pop(key, None)
    root.set("width", str(ART_WIDTH))
    root.set("height", str(ART_HEIGHT))
    for el in root.iter(f"{{{NS}}}ellipse"):
        try:
            cx, cy, rx, ry = (float(el.get(k, "0")) for k in ("cx", "cy", "rx", "ry"))
        except ValueError:
            continue  # leave it as it is; skia ignores what it cannot read
        for k in ("cx", "cy", "rx", "ry"):
            el.attrib.pop(k, None)
        el.tag = f"{{{NS}}}path"
        el.set("d", f"M{cx - rx:g} {cy:g} A{rx:g} {ry:g} 0 1 0 {cx + rx:g} {cy:g} "
                    f"A{rx:g} {ry:g} 0 1 0 {cx - rx:g} {cy:g} Z")
    return root


def _render(root: ET.Element, background) -> np.ndarray:
    data = ET.tostring(root, encoding="utf-8")
    dom = skia.SVGDOM.MakeFromStream(skia.MemoryStream(data))
    if dom is None:
        raise ValueError("skia could not read the SVG")
    dom.setContainerSize(skia.Size(ART_WIDTH, ART_HEIGHT))
    surface = skia.Surface.MakeRaster(
        skia.ImageInfo.Make(ART_WIDTH, ART_HEIGHT, skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType))
    canvas = surface.getCanvas()
    canvas.clear(background)
    dom.render(canvas)
    return surface.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType)


def _style(el: ET.Element) -> dict[str, str]:
    """The element's presentation attributes, with its style attribute on top."""
    props = {k: v.strip() for k, v in el.attrib.items() if k in PAINT_PROPS}
    for part in el.get("style", "").split(";"):
        key, _, value = part.partition(":")
        if value.strip():
            props[key.strip()] = value.strip()
    return props


def _opacity(value: str | None) -> float:
    if value is None:
        return 1.0
    try:
        number = float(value[:-1]) / 100 if value.endswith("%") else float(value)
    except ValueError:
        return 1.0
    return min(max(number, 0.0), 1.0)


def _soft_filters(root: ET.Element) -> set[str]:
    """The ids of filters that only blur what they are on, never drawing it crisp again: a soft
    shadow or glow. A drop shadow, which merges the source graphic back on top, is not one."""
    soft = set()
    for f in root.iter(f"{{{NS}}}filter"):
        steps = [el for el in f.iter() if el is not f]
        blurs = any(_local(el.tag) == "feGaussianBlur" for el in steps)
        crisp = any(_local(el.tag) != "feGaussianBlur" and "SourceGraphic" in (el.get("in"), el.get("in2"))
                    for el in steps)
        if blurs and not crisp and f.get("id"):
            soft.add(f.get("id"))
    return soft


def _painted(root: ET.Element, name: str, keep_soft: bool = False) -> ET.Element:
    """A copy with the structure white and every other painted thing black, all opaque.

    Gradients, filters and SVG masks go, so edges stay where the shapes are. Something
    drawn mostly see-through is left out unless it belongs to the structure, since the
    structure shows through it in the art; so is the structure's own soft shadow or glow
    (see-through, under a filter that only blurs), unless `keep_soft`, or it is the house's glow.
    """
    root = copy.deepcopy(root)
    soft = _soft_filters(root)

    def walk(el: ET.Element, fill: str, stroke: str, fill_op: float, stroke_op: float, opacity: float,
             owner: str | None, blurred: bool = False, glow: bool = False) -> None:
        tag = _local(el.tag)
        if tag in NOT_DRAWN:
            return
        props = _style(el)
        fill = props.get("fill", fill) if props.get("fill") != "inherit" else fill
        stroke = props.get("stroke", stroke) if props.get("stroke") != "inherit" else stroke
        fill_op = _opacity(props.get("fill-opacity")) if "fill-opacity" in props else fill_op
        stroke_op = _opacity(props.get("stroke-opacity")) if "stroke-opacity" in props else stroke_op
        opacity *= _opacity(props.get("opacity"))
        blurred = blurred or any(ref.lstrip("#") in soft for ref in URL.findall(props.get("filter", "")))
        owner = el.get(MARK, el.get(GLOW_OF, owner))
        glow = glow or el.get(ROLE) == "glow"
        for key in PAINT_PROPS:
            el.attrib.pop(key, None)
        rest = [p for p in el.get("style", "").split(";") if p.partition(":")[0].strip() not in PAINT_PROPS]
        if el.get("style") is not None:
            el.set("style", ";".join(p for p in rest if p.strip()))
        if tag in SHAPES:
            mine = owner == name
            color = "#FFFFFF" if mine else "#000000"
            faint_kept = mine and (keep_soft or glow or not blurred)
            el.set("fill", "none" if fill == "none" or (not faint_kept and opacity * fill_op < 0.5) else color)
            el.set("stroke", "none" if stroke == "none" or (not faint_kept and opacity * stroke_op < 0.5) else color)
        for child in el:
            walk(child, fill, stroke, fill_op, stroke_op, opacity, owner, blurred, glow)

    walk(root, "black", "none", 1.0, 1.0, 1.0, None)
    return root


def _squash(name: str) -> str:
    return " ".join(name.split()).casefold()


def _hex(value: str) -> str | None:
    """#RRGGBB from #RRGGBB, #RGB, #RRGGBBAA or rgb(r, g, b); None for anything else."""
    value = value.strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", value):
        return value[:7].upper()
    if re.fullmatch(r"#[0-9A-Fa-f]{3}", value):
        return "#" + "".join(c * 2 for c in value[1:]).upper()
    rgb = re.fullmatch(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(,[^)]*)?\)", value)
    if rgb and all(int(c) <= 255 for c in rgb.groups()[:3]):
        return "#" + "".join(f"{int(c):02X}" for c in rgb.groups()[:3])
    return None


def _colors(group: ET.Element, ids: dict[str, ET.Element]) -> set[str]:
    """Every #RRGGBB a structure paints with, its gradients' stops included."""
    found: set[str] = set()
    seen: set[str] = set()

    def add(el: ET.Element) -> None:
        values = [el.get("stop-color", "")] + list(_style(el).values())  # the style attribute's stop-color too
        for value in values:
            color = _hex(value)
            if color:
                found.add(color)
            for ref in URL.findall(value):
                gradient(ref.lstrip("#"))

    def gradient(ref: str) -> None:
        if ref in seen or ref not in ids:
            return
        seen.add(ref)
        target = ids[ref]
        for el in target.iter():
            add(el)
        href = target.get("href") or target.get(f"{{{XLINK}}}href")
        if href:
            gradient(href.lstrip("#"))

    for el in group.iter():
        add(el)
    return found


# --- finding a structure -------------------------------------------------------------


def _deepest(mask: np.ndarray, cx: float, cy: float) -> tuple[float, float]:
    """A point well inside the mask, far from its edges, near (cx, cy).

    The mask is shrunk one pixel at a time (alternating 4- and 8-neighbour steps, close
    to a true distance); a pixel's depth is how many steps it survives. The anchor is
    the point nearest (cx, cy) among those at least 80% as deep as the deepest. Big
    masks are first scaled down by keeping only blocks wholly inside, so the point
    found is inside at full size too.
    """
    h, w = mask.shape
    scale = max(1, math.ceil(max(h, w) / 256))
    while True:
        small = _blocks(mask, scale)
        if small.any() or scale == 1:
            break
        scale = max(1, scale // 2)  # thinner than a block: look closer
    padded = np.pad(small, 1)
    depth = np.zeros(padded.shape, np.int32)
    current, step = padded, 0
    while current.any():
        step += 1
        depth[current] = step
        shrunk = current.copy()
        shrunk[1:, :] &= current[:-1, :]
        shrunk[:-1, :] &= current[1:, :]
        shrunk[:, 1:] &= current[:, :-1]
        shrunk[:, :-1] &= current[:, 1:]
        if step % 2 == 0:
            shrunk[1:, 1:] &= current[:-1, :-1]
            shrunk[1:, :-1] &= current[:-1, 1:]
            shrunk[:-1, 1:] &= current[1:, :-1]
            shrunk[:-1, :-1] &= current[1:, 1:]
        current = shrunk
    depth = depth[1:-1, 1:-1]
    ys, xs = np.nonzero(depth >= max(1, math.ceil(0.8 * depth.max())))
    px, py = (xs + 0.5) * scale, (ys + 0.5) * scale  # block centres, in mask pixels
    best = int(np.argmin((px - cx) ** 2 + (py - cy) ** 2))
    return float(px[best]), float(py[best])


def _blocks(mask: np.ndarray, scale: int) -> np.ndarray:
    """The mask scaled down by `scale`: a block is True only when all of it is."""
    if scale == 1:
        return mask
    h, w = mask.shape
    padded = np.zeros((math.ceil(h / scale) * scale, math.ceil(w / scale) * scale), bool)
    padded[:h, :w] = mask
    return padded.reshape(padded.shape[0] // scale, scale, padded.shape[1] // scale, scale).all(axis=(1, 3))

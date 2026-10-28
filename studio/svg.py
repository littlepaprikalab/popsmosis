"""Brain-drawn SVG art (docs/PLAN.md, step 3, until the Meta key arrives).

The brain draws each scene as one SVG, every structure as one element marked
data-structure="<name>". This module checks such a drawing, rasterises it, and finds
each structure in it, so the art step can write the art, the masks and locations.json
without a locate step.

    check(text, structures)        what is wrong with a drawing, before anything is drawn
    rasterise(root)                the art: RGB on white, ART_WIDTH x ART_HEIGHT
    masks(root, names)             per structure, where it is visible in the art
    locate(mask)                   its box, centroid and label anchor

A mask is the structure painted white and everything else black, in the drawing's own
order, so whatever is drawn on top of a structure is left out of its mask.
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
from studio.schemas import Box, Point, Structure

NS = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
ET.register_namespace("", NS)
ET.register_namespace("xlink", XLINK)

MARK = "data-structure"
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
    """The art: ART_HEIGHT x ART_WIDTH x 3, RGB on white."""
    return _render(_prepared(root), skia.ColorWHITE)[:, :, :3].copy()


def without(root: ET.Element, name: str) -> np.ndarray:
    """The art with one structure left out (its group, as `rasterise` draws it otherwise):
    what lies under the structure, shown in its place while it moves."""
    bare = copy.deepcopy(root)
    for parent in bare.iter():
        for child in list(parent):
            if _squash(child.get(MARK, "")) == _squash(name):
                parent.remove(child)
    return rasterise(bare)


def alone(root: ET.Element, name: str) -> np.ndarray:
    """One structure drawn on its own, on transparent (RGBA, premultiplied), with the smooth
    edges the art has: its group, the groups around it, and what it refers to in <defs>."""
    bare = copy.deepcopy(root)
    wanted = [el for el in bare.iter() if _squash(el.get(MARK, "")) == _squash(name)]
    parents = {child: parent for parent in bare.iter() for child in parent}
    around = set()
    for el in wanted:
        while el in parents:
            el = parents[el]
            around.add(el)

    def prune(parent: ET.Element) -> None:
        for child in list(parent):
            if child in wanted or _local(child.tag) == "defs":
                continue
            if child in around:
                prune(child)
            else:
                parent.remove(child)

    prune(bare)
    return _render(_prepared(bare), skia.ColorTRANSPARENT)


def masks(root: ET.Element, names: list[str]) -> dict[str, np.ndarray]:
    """Per structure, a bool array: True where it is visible in the art.

    A soft shadow or glow drawn inside the structure's own group (see-through, under a
    filter that only blurs) is not the structure, so it is left out of the mask, unless
    the structure is nothing else.
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


def locate(mask: np.ndarray) -> Found | None:
    """Where a structure is, from its mask; None when too little of it shows to label."""
    area = int(mask.sum())
    if area < MIN_AREA:
        return None
    ys, xs = np.nonzero(mask)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    cx, cy = float(xs.mean()) + 0.5, float(ys.mean()) + 0.5
    anchor = _deepest(mask[y0:y1, x0:x1], cx - x0, cy - y0)
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
    (see-through, under a filter that only blurs), unless `keep_soft`.
    """
    root = copy.deepcopy(root)
    soft = _soft_filters(root)

    def walk(el: ET.Element, fill: str, stroke: str, fill_op: float, stroke_op: float, opacity: float,
             owner: str | None, blurred: bool = False) -> None:
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
        owner = el.get(MARK, owner)
        for key in PAINT_PROPS:
            el.attrib.pop(key, None)
        rest = [p for p in el.get("style", "").split(";") if p.partition(":")[0].strip() not in PAINT_PROPS]
        if el.get("style") is not None:
            el.set("style", ";".join(p for p in rest if p.strip()))
        if tag in SHAPES:
            mine = owner == name
            color = "#FFFFFF" if mine else "#000000"
            faint_kept = mine and (keep_soft or not blurred)
            el.set("fill", "none" if fill == "none" or (not faint_kept and opacity * fill_op < 0.5) else color)
            el.set("stroke", "none" if stroke == "none" or (not faint_kept and opacity * stroke_op < 0.5) else color)
        for child in el:
            walk(child, fill, stroke, fill_op, stroke_op, opacity, owner, blurred)

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

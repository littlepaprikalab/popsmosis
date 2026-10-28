# 3D schematics: plan

Status: a working prototype (`studio/schematic3d`) that draws one shot, ATP synthase with its rotor
turning, in the textbook style. This page is the plan for making it a kind of scene the studio
makes on its own. Nothing in the pipeline uses it yet.

## 1. Why

The diagrams that teach mechanisms best look like the ones in Campbell Biology. Illustrators call
the style a stylised 3D schematic:

- each protein is a simple smooth shape (a capsule, a cylinder, a rounded lobe)
- one soft colour with gentle shading and a dark outline
- flat colour for the compartments, and a membrane of grey heads with yellow tails
- red arrows carry the story: where the protons go, which way the rotor turns

Simplified diagrams teach how something works better than realistic ones, and motion helps most
when the motion is the thing being learned (Höffler & Leutner, 2007). This style gives both. Drawn
from the real structure, the shapes sit where the real subunits are, and a rotor turns about its
real axle.

## 2. What we tried

All measured on a 4-core cloud container with no GPU, for the same 7-second ATP synthase shot.

| Approach | Per frame | The 7 s shot | Notes |
|---|---|---|---|
| Blender, every atom as a surface (Molecular Nodes) | 50–100 s | ~2.4 h | Realistic, but not appealing as teaching art |
| Blender, textbook style, whole scene each frame | ~8 s | ~23 min | The look we want |
| Blender, still parts once, rotor layer per frame, 6 fps | ~0.6 s | ~1 min, plus ~1 min of labels | Rough edges where layers meet |
| **Our own renderer (`studio/schematic3d`)** | **~0.05 s at 1080p** | **~12 s, labels included** | **Chosen: no Blender, no GPU** |

The style is made of capsules, spheres and tubes seen through an orthographic camera, so each
shape projects to a simple 2D one: a capsule to a rounded bar, a sphere to a circle, a tube to a
thick curve. Keeping the shapes' 3D positions, turning the moving ones in 3D each frame, then
projecting and painting them far to near gives true 3D motion for almost nothing. The same skia
library popsmosis already renders with draws it all.

## 3. The prototype

```
studio/schematic3d/
  fit.py                    structure file + recipe -> shapes (nm, in the machine's own frame)
  draw.py                   shapes + recipe -> a still or a video, with popsmosis's labels and band
  recipes/atp_synthase.json what to draw: chains to parts, axle, colours, compartments, ions, arrows
  shapes/atp_synthase.json  the shapes fitted to PDB 6FKF, kept so drawing needs no download
studio/tests/test_schematic3d.py
```

```bash
# fit (needs the structure file: https://files.rcsb.org/download/6FKF.cif)
python -m studio.schematic3d.fit studio/schematic3d/recipes/atp_synthase.json 6FKF.cif studio/schematic3d/shapes/atp_synthase.json
# draw a still, or the whole shot
python -m studio.schematic3d.draw studio/schematic3d/recipes/atp_synthase.json studio/schematic3d/shapes/atp_synthase.json out --still 6
python -m studio.schematic3d.draw studio/schematic3d/recipes/atp_synthase.json studio/schematic3d/shapes/atp_synthase.json out
```

- **Fitting.** Each chain's alpha carbons become a capsule along their long axis (the axis that
  explains most of their spread), or for a long thin chain a tube along its smoothed path. The
  recipe names the ring that defines the axle, the part that goes below it and the part that goes
  to the right, which fixes the machine's own frame: axle up the z axis, ring at the origin.
- **Drawing.** Every frame turns the recipe's spinning parts about the axle, projects every shape
  and paints them far to near (by each shape's mean depth). A part the recipe says is `inside`
  another (the rod inside the knob) goes behind all of it, as the book draws it. Shading is the
  book's: an outline, a darker side, a soft lit side and a shine, lit from the upper left. The
  compartments and the membrane never move and are drawn once. Ions flow down the red path while
  the rotor turns.
- **Limits.** Painting by mean depth can get a pair of long interleaved shapes wrong (the
  `inside` rule covers the one case so far). No cast shadows, which the book does not use either.
  The membrane is flat rows of heads and tails. The recipe is hand-written, including the label
  anchors, the arrows, and the lines the demo sings. **The rotor's turning direction is set from
  memory** (clockwise seen from the membrane's top, as when it makes ATP); it has to come from the
  lesson's facts before anything ships.

## 4. Making it part of popsmosis

### 4.1 A scene kind

A scene today is an SVG the illustrator draws (step 3, `studio/steps/art.py`). A 3D schematic
scene is a second kind:

- The bundle's scene gains `kind` (`"svg"`, or `"schematic3d"`) and, for a schematic, the recipe
  it uses. The scene's `structures` are the recipe's parts (Rotor, Internal rod, Catalytic knob,
  Stator), so the lyrics reveal them by name exactly as they reveal an SVG's structures, and the
  name checks (`studio/checks.py`) work unchanged.
- The writer chooses a schematic only from the recipe library (4.2), by name. Anything else stays
  an SVG scene.

### 4.2 A library of checked recipes first

Letting the writer pick any PDB entry and map its chains is the riskiest step for accuracy: the
wrong entry, or a chain given the wrong role, would teach something false in a confident picture.
So the first version uses a small curated library, each recipe checked once by a person and reused
by every lesson on its topic:

- ATP synthase (done), then for example the sodium-potassium pump, a voltage-gated channel,
  aquaporin, hemoglobin, the ribosome, DNA polymerase, and photosystem II with the electron
  transport chain.
- Each recipe records its PDB entry and title, and a check compares each part's chains with the
  entry's own entity names (e.g. "ATP synthase subunit c" for the ring), as the prototype's fit
  does by hand today. A mismatch fails the recipe, not a lesson.

Later, the writer could propose a new recipe from an entry it names. It would go through the
same entity-name check plus a fact-check of the part roles, and stay out of the library until a
person accepts it.

### 4.3 The fit step

For a schematic scene, step 3 fits instead of drawing: it fetches the structure file (cached by
PDB id; the teacher's network policy must allow `files.rcsb.org`), runs `fit.py` and writes the
shapes into the job. A recipe in the library can ship its fitted shapes, as ATP synthase does, so
most jobs need no download at all.

### 4.4 The renderer

`studio/render/renderer.py` draws a scene from its art and masks (`SceneArt`). A schematic scene
draws itself for any time t instead, through `draw.Scene`:

- **Reveal and spotlight.** Unrevealed parts are drawn pale (the ghost) and a spotlight washes
  every other part. This is simpler than with masks, since each part is drawn separately.
- **Labels.** A label's anchor is its part's point, projected at time t. Labels move with their
  parts and use the same pills and placement as now.
- **Camera.** The direction's camera target and zoom become the orthographic view's centre and
  scale, eased as the renderer eases them now.
- **The timeline contract.** The timeline step needs each scene's structures located. For a
  schematic, the locations come from the shapes rather than from masks.

### 4.5 Motions in the direction

The writer already plans `particles` per line. Schematic scenes add motions from a short list,
each tied to a line:

- `spin`: parts that turn about the axle. The direction has to be in the facts, so the
  fact-check covers it.
- `flow`: ions or molecules moving along a path between parts. This is the particles of
  littlepaprikalab/popsmosis#36, drawn as the ions here.
- Later: `slide` (a vesicle to a membrane), `open` (a channel's gate), `bind`.

Only what the lines sing moves, and only while they sing it.

### 4.6 Membrane and compartments

The recipe names the compartments ("THYLAKOID SPACE", "STROMA") and which parts sit in the
membrane. For membrane proteins, where the membrane lies can come from the OPM database
(Orientations of Proteins in Membranes) instead of a hand rule. Soluble machines (hemoglobin, the
ribosome) have no membrane and a single background.

### 4.7 Cost

About 50 ms a frame at 1080p on the 4-core container, plus a fraction of a second once per scene
for the membrane. A 3-minute video entirely in 3D schematics would be about 4,300 frames, roughly 4
minutes of rendering. That's an estimate from the prototype; the real figure depends on how many
shapes a scene has. No GPU is needed.

## 5. Coverage

1. Molecular machines with a published structure, from the curated library (4.2).
2. Cell-level scenes with no single structure (a mitochondrion, a synapse): the illustrator places
   the same kit of parts (capsules, spheres, tubes, membranes, ions, arrows) in the same style.
3. Everything else (history, math, economics) stays an SVG scene.

## 6. Milestones

1. **One recipe end to end.** The `schematic3d` scene kind, ATP synthase from the library, the
   renderer drawing it with reveals, labels, spotlight and camera, inside a real job.
2. **Motions from the direction.** `spin` and `flow` per line, with the spin direction coming
   from the facts.
3. **The library.** About ten recipes with their entity-name checks, and the writer choosing from
   them.
4. **Scenes built from parts.** Cell-level scenes placed by the illustrator from the kit.

## 7. Open questions

- Which ten recipes first? Ideally the topics learners ask for most.
- The teacher's network policy: whether `files.rcsb.org` (and OPM) are allowed, or whether every
  recipe ships its shapes.
- Depth-ordering errors on denser machines (the ribosome). Splitting long shapes into pieces, or
  ordering by nearest point rather than mean, may be needed.
- Licences: PDB data is CC0 (commercial use allowed; credit the entry's authors where we can).
  gemmi is MPL-2.0. No GPL code is involved; Blender was only used for the experiments in
  section 2.

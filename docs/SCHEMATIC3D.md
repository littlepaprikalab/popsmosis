# 3D schematics

Status: built, end to end in a studio job. A scene that names a molecular machine is drawn from
the machine's published 3D structure, found and fitted for that lesson, in the textbook's style;
reveals, labels, spotlight and camera work as on any scene. The machine stands still. When it
cannot be built, the scene is drawn as an illustration, as before.

## 1. Why

The diagrams that teach mechanisms best look like the ones in Campbell Biology. Illustrators call
the style a stylised 3D schematic:

- each protein is a simple smooth shape (a capsule, a cylinder, a rounded lobe)
- one soft colour with gentle shading and a dark outline
- flat colour for the compartments, and a membrane of grey heads with yellow tails
- red arrows carry the story: where the protons go, which way the rotor turns

Simplified diagrams teach how something works better than realistic ones. Drawn from the real
structure, the shapes sit where the real subunits are, in the right proportions, which a drawing
from memory rarely manages.

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

## 3. How it works

```
studio/schematic3d/
  sources.py   the Protein Data Bank (RCSB) and OPM: search, entry details, coordinates
  build.py     the machine for one scene: find, choose, fit, review, frame
  fit.py       a structure's chains -> smooth shapes in the machine's own frame
  model.py     a built machine (Built): what art/<scene>.schematic.json holds
  draw.py      a built machine -> its still, its masks, and the renderer's live drawing
studio/prompts/schematic_find.md, schematic_choose.md, schematic_review.md
studio/tests/test_schematic3d.py, sample_schematic.py (a made-up machine, no network)
```

Nothing is written for one machine or one topic, and nothing is kept between lessons.

1. **Bundle.** A scene's `schematic` names the one machine it shows up close, in the course's
   words ("chloroplast ATP synthase", "human hemoglobin"), or is "" for an illustration. Its
   structures are the machine's parts as the course names them, with their colours, written by
   the writer like any scene's. The prompt keeps a schematic for the machine alone: what flows
   through it, the cell around it and failures stay illustrations.
2. **Art step: build the machine** (`build.py`), per scene:
   - *Find.* The brain gives search phrases in the words of entry titles and entries it knows.
     RCSB's search returns candidates; their details give each entity's name and chains, limited
     to the entry's first biological assembly (one machine, not a crystal's copies). For a machine
     in a membrane, OPM says which candidates it has placed in their membrane.
   - *Choose.* The brain picks the entry (the whole machine, the lesson's organism, placed by OPM
     when it sits in a membrane) and the entities that make up each of the scene's structures.
   - *Fit.* Code downloads the coordinates, OPM's oriented ones when there are, and fits shapes
     with no hand-set numbers (`fit.py`): a capsule per small chain, a capsule per lobe of a big
     one, a tube for a long thin one. OPM's frame puts the membrane in the plane z = 0; without
     OPM, the machine stands along its longest axis.
   - *Review.* A fresh brain call judges the entry and each structure's subunits, and names the
     compartments above and below the membrane from where code found the parts. A structure judged
     wrong is drawn but never labelled.
   - *Frame.* The camera fits the machine with room for labels.
   - The result (`art/<scene>.schematic.json`) is kept with the job and uploaded with the art, so a
     re-render draws the same machine. The step writes its still as the art and a mask per
     structure, so the timeline and the renderer treat it like any scene.
   - **Fallback.** Nothing found, the entry or more than half the structures judged wrong, a source
     that does not answer: the scene is drawn as an illustration, and the step says why.
3. **Render.** `SchematicArt` draws the machine live under the scene's camera, so it stays sharp
   at any zoom. Unrevealed parts are washed 55% toward white (not the 85% of drawn art), so the
   machine reads from the first frame; the spotlight washes everything but its structure. Labels
   sit beside the machine rather than on it. A frame whose camera and washes repeat is reused.

### 3.1 Why it stands still

The first version turned ATP synthase's rotor, its direction checked against the lesson's facts.
It was dropped: rigid shapes can turn or travel along a path, but most of what machines do is
change shape (the knob's subunits making ATP, a pump flipping, a channel opening), each motion had
to be choreographed and checked per machine, and a lesson's facts seldom say which way a rotor
turns. The video still moves: the camera, the reveals, the spotlight, and the particles that
travel between structures as a line sings it.

### 3.2 Cost

Building a machine takes three brain calls and about 30 seconds (most of it the calls), and a few
cents. Drawing it costs about 26 ms a frame at zoom 1, 84 ms at 2x and 142 ms at 3x on the 4-core
container, only when the camera or the washes change.

## 4. What it needs

- The teacher routine's network policy allows search.rcsb.org, data.rcsb.org, files.rcsb.org,
  opm-back.cc.lehigh.edu and opm-assets.storage.googleapis.com.
- gemmi (studio/requirements.txt) reads the structure files.
- Licences: PDB data is CC0 (credit the entry's authors where we can). OPM's data is reported
  to be CC BY 3.0 (credit Lomize et al.); confirm on OPM's site before relying on it. gemmi is
  MPL-2.0. No GPL code is involved.

## 5. Coverage

1. Any lesson whose scene shows one molecular machine with a published structure: enzymes,
   pumps, channels, receptors, motor proteins, hemoglobin, polymerases.
2. Cell-level scenes with no single structure (a mitochondrion, a synapse) and whole pathways
   (the electron transport chain) stay illustrations; they could later be placed from the same
   kit of shapes.
3. Everything else (history, math, economics) stays an illustration.

## 6. Next

- Run it on many machines and read the failures: entries that are only a fragment, assemblies a
  textbook draws differently (aquaporin's tetramer is one chain in some entries), ribosomes too
  big to fit.
- Still annotations, each a fact-checked claim: arrows for what flows through, small labelled
  molecules ("ADP + Pi → ATP").
- Depth order by nearest point rather than mean, for parts inside others (a rod in a knob).

## 7. History

- The prototype drew ATP synthase from a hand-written recipe, its rotor turning (section 2).
- Milestone 1 made it a scene kind from a curated, human-signed recipe library with fact-checked
  motions. Real jobs showed the limits (3.1) and that hand-written recipes cannot cover every
  topic, so the library was replaced by the build above.

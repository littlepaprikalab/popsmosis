# 3D schematics

Status: milestone 1 is built. A scene can be drawn from the schematic library instead of an
illustration, end to end in a studio job: the writer picks the machine, the fact-check decides how
it may move, the art step needs no model call, and the renderer draws it live with reveals,
labels, spotlight and camera like any other scene. The library has one recipe, ATP synthase,
which is not yet signed by a person (4.2), so the writer is not offered it outside a local run.

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

## 3. How it works

```
studio/schematic3d/
  library.py                 the recipe format (Recipe), the library, the writer's menu, the motion claims
  fit.py                     structure file + recipe -> shapes (nm, in the machine's own frame)
  draw.py                    Machine: shapes + recipe + motions -> drawn in the art's pixels at any time t
  recipes/atp_synthase.json  everything about one machine (below)
  shapes/atp_synthase.json   the shapes fitted to PDB 6FKF, kept so a job needs no download
studio/tests/test_schematic3d.py, sample_schematic.py, schematic/ (a made-up second recipe)
```

Nothing in the pipeline is written for one machine or one topic. Everything about a machine is in
its recipe, which `library.Recipe` validates:

- **parts**: which chains of the entry each part is, with the entry's own names for those chains
  (`structure.entities`, as RCSB names them). Each part is fitted as capsules, or a tube.
- **structures**: what a scene reveals and labels, each one or more parts, with its name, aliases,
  colour, what it looks like, and where its label points (a fixed point, a point along a chain, or
  a part's middle). These become the scene's structures, so the lyrics reveal them by name and the
  name checks, the timeline and the labels work as for any scene.
- **page and membrane**: one flat compartment, or one above and one below a membrane, with their
  names. A soluble machine has no membrane.
- **camera**: the orthographic view that frames the whole scene.
- **motions**: `spin` (parts turning about the axle) and `flow` (particles along a path), each
  written as the claims it would make. The recipe never says which way.

### 3.1 The pipeline

1. **Bundle.** `Scene.schematic` is "" (an illustration) or a recipe's name; the schema's enum
   offers only the recipes a person has signed. The prompt lists them, generated from the library
   (`library.writer_section`), and says the hook can open on the machine when the lesson is about
   it. `checks.review` gives a schematic scene exactly its recipe's structures (keeping any aliases
   the writer added, dropping what the recipe does not draw) and gives the same structure the
   recipe's colour on every other scene.
2. **Fact-check.** Each recipe the bundle uses adds its motion claims as lines of kind `motion`:
   every way a spin could turn, seen from each end of the axle, and every way a flow could go. They
   are judged against the facts like any line, never corrected, and never fail the check.
3. **Art.** No model call. The step resolves the motions from `factcheck.json`
   (`library.resolve`: a motion is drawn only when the facts support exactly one way of it), keeps
   a snapshot of the recipe, shapes and motions in `art/<scene>.schematic.json`, writes the
   machine's still as the art and a mask per structure, and locates each structure there. The
   snapshot is uploaded with the other intermediates, so a re-render draws what was checked.
4. **Timeline.** Unchanged: the structures are located like any others.
5. **Render.** `SchematicArt` draws the machine on every frame under the scene's camera. The
   art's pixels are the world, so camera aim, zoom, reveals, spotlight, labels and the exam
   question's panel all work as on drawn art. Unrevealed parts are washed 55% toward white (not
   the 85% of drawn art), so the machine reads from the first frame; the spotlight washes
   everything but its structure, page included. Labels ride along with turning parts and sit
   beside the machine rather than on it. Motions run from the first frame at full speed.

### 3.2 Which way it moves

The user-facing rule: the picture moves only as the lesson's facts say. Concretely:

- The recipe's spin claim has `{direction}` and `{end}` in it, and the axle names its two ends
  ("the thylakoid space", "the stroma"). The fact-check gets all four sentences. Clockwise from one
  end and counterclockwise from the other are the same turn, so either is understood.
- One way supported: the parts turn that way and the red arcs show it. No way, or contradicting
  ways: the parts stand still and no arcs are drawn. A flow likewise, its arrow and waiting
  particles included; `moves_with` keeps particles from streaming past a rotor that is not turning.
- The prompt tells the writer to state a motion in a fact only when it is sure. What the facts
  say is as trustworthy as every other line: a wrong fact gives a wrong spin, as it would give a
  wrong lyric. A person-checked way with a source could be added to each recipe later, to refuse
  a lesson whose facts disagree with it.

### 3.3 Cost

About 26 ms a frame for the machine at zoom 1, 84 ms at 2x and 142 ms at 3x (its soft shading is
blurred, and the blur grows with the zoom), on the 4-core container, plus the rest of the frame.
The membrane is drawn once per scale it is needed at and kept. The sample job's 53 s song,
opening on a schematic, rendered in 33 s with 4 workers. Nothing needs a GPU.

## 4. Adding a machine

1. Pick an entry (the structure that shows the machine as textbooks draw it) and write
   `recipes/<name>.json`: parts with their chains and entity names, structures, page, camera,
   motions with their claims, `shows` and `use_for` for the writer.
2. Fit it: `python -m studio.schematic3d.fit recipes/<name>.json <entry>.cif shapes/<name>.json`.
   The fit refuses a recipe whose recorded entity names differ from the file's.
3. Look at it: `python -m studio.schematic3d.draw <name> out --still 0 --motion <id>=1`.
4. Have a person check it, then fill in `checked.by` and `checked.on`.

### 4.1 Who checks what

`checked` holds the sign-off. The checker confirms that each part's chains are what its
`entities` say (the fit has already compared them with the file), that the structure names and
what they cover are the textbook's, that the compartments are right for where the machine works,
and that each motion's sentences say what the picture would show. Until then the writer is not
offered the recipe; `STUDIO_SCHEMATIC_UNCHECKED=1` offers it in a local run, for trying it out.

### 4.2 ATP synthase

The chloroplast enzyme, from 6FKF (Hahn et al., Science 2018). Rotor: the fourteen c subunits.
Internal rod: gamma and epsilon. Catalytic knob: alpha3 beta3. Stator: a and delta, with the
peripheral stalk b and b'. Its compartments are the thylakoid space and the stroma, so it does
not fit a lesson set in mitochondria; that needs a mitochondrial recipe. Not yet signed.

## 5. Coverage

1. Molecular machines with a published structure, from the library: any lesson that uses one.
   ATP synthase alone serves the light reactions, chemiosmosis and photophosphorylation.
2. Cell-level scenes with no single structure (a mitochondrion, a synapse): the illustrator places
   the same kit of parts (capsules, spheres, tubes, membranes, particles, arrows) in the same style.
3. Everything else (history, math, economics) stays an illustration.

## 6. Milestones

1. **One recipe end to end.** Done: the scene kind, ATP synthase from the library, the renderer
   drawing it with reveals, labels, spotlight and camera, inside a real job, its motions from the
   facts.
2. **Motions from the direction.** `spin` and `flow` per line, so only what the lines sing moves,
   and only while they sing it. Today a supported motion runs whenever its scene is on screen.
3. **The library.** About ten recipes, signed, and their structures fetched and checked by
   script. Candidates: the sodium-potassium pump, a voltage-gated channel, aquaporin, hemoglobin,
   the ribosome, DNA polymerase, photosystem II, a mitochondrial ATP synthase.
4. **Scenes built from parts.** Cell-level scenes placed by the illustrator from the kit.

## 7. Open questions

- Who signs recipes, and should a signed recipe also record a checked way for each motion?
- Which ten recipes first? Ideally the topics learners ask for most.
- Depth-ordering errors on denser machines (the ribosome). Splitting long shapes into pieces, or
  ordering by nearest point rather than mean, may be needed.
- Licences: PDB data is CC0 (commercial use allowed; credit the entry's authors where we can).
  gemmi is MPL-2.0. No GPL code is involved; Blender was only used for the experiments in
  section 2.

# Image prompts (Muse Image)

How the studio builds each picture's prompt, and each round-6 scene's prompt as a first try sends it now (copy-paste ready; a repaint adds the checker's complaints).
The pictures are in each job's `art/` folder, which git ignores; docs/test-rounds.md (round 6) describes them.
This is a snapshot from Oct 28: the code (studio/steps/art.py, studio/prompts/bundle.md) wins where they differ.

## How a prompt is put together

Each scene's prompt is three parts, joined with spaces (studio/steps/art.py, muse_prompt()):

1. **The scene's request**, written by the brain (Claude) in the lesson plan, as a teacher asks for a textbook
   diagram: the kind of diagram, what it shows and where each part is, then the look for that kind of subject.
2. **The colours**: `Colours: <part> <colour name>, ...`, each labelled part's colour by name (never a hex code).
3. **The style line**, the same on every picture:

   > On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Every part sits inside whatever holds it in life, as an organelle sits inside its cell. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.

On a repaint, a sentence goes between 2 and 3: `The last picture of this scene was refused: <what the checker
found>. Make it again with these fixed.`

The call: `POST https://api.meta.ai/v1/images/generations`, model `muse-image-1.0`, size
`1792x1024` (an aspect ratio; Muse Image returns 2016x1152), n 1,
response_format b64_json, output_format png, tool_enablement {enable_web_search: false, enable_shell: false}.

The look the lesson prompt tells the brain to end a cell-biology request with (studio/prompts/bundle.md):

```
  - A cell or molecular process (membranes, organelles, proteins, molecules): "a clean,
    modern biology textbook illustration: semi-realistic organelles, each protein complex a
    smooth rounded shape, soft gradient shading, clean outlines, vibrant colour coding; the
    membrane a phospholipid bilayer; ions and small molecules as small glossy spheres."
    This is the figure students know from a modern textbook or study guide, not a
    detailed molecular rendering: "semi-realistic proteins" came back as ribbon models.
  - Anatomy (the brain, organs, body systems): "a softly shaded medical textbook
    illustration."
  - Astronomy, Earth science, physics or chemistry: "a clean textbook schematic, each
    object simplified but recognisable" (a globe with its continents, a Sun with its glow).
  - An everyday hook or analogy: "a friendly textbook illustration of the real thing."
```

## cellular respiration (r8-respiration)

### hook

Picture: jobs/r8-respiration/art/hook.png

```
Educational diagram, as a biology textbook chapter opener draws it, of a sprinter running on a track, seen from the side in mid-stride. The sprinter wears a white running outfit; the large thigh muscle of the front leg is shown through the skin in red, the way anatomy figures show muscle. A friendly textbook illustration of the real thing. Colours: leg muscle red. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Every part sits inside whatever holds it in life, as an organelle sits inside its cell. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

### overview

Picture: jobs/r8-respiration/art/overview.png

```
Educational diagram, as a Campbell Biology figure draws it, of an overview of cellular respiration in an animal cell. On the left, the cytoplasm, where glycolysis is shown as a short chain of yellow arrows splitting one glucose molecule into two pyruvate. In the middle and on the right, a large mitochondrion cut open lengthwise, its outer membrane pale beige and its inner membrane folded into cristae; an arrow carries pyruvate from glycolysis into it. The Krebs cycle is an orange ring of arrows inside the mitochondrion's matrix. The electron transport chain is a row of purple protein complexes along the folded inner membrane, with ATP synthase as a green mushroom-shaped protein beside them. Oxygen molecules as small white spheres drift in from the right toward the electron transport chain. A clean, modern biology textbook illustration: semi-realistic organelles and proteins, soft gradient shading, clean outlines, vibrant colour coding; the membrane a phospholipid bilayer; ions and small molecules as small glossy spheres. Colours: glycolysis yellow, Krebs cycle orange, electron transport chain purple, ATP synthase green, oxygen white. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Every part sits inside whatever holds it in life, as an organelle sits inside its cell. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

### analogy

Picture: jobs/r8-respiration/art/analogy.png

```
Educational diagram, as a physical science textbook draws it, of a hydroelectric dam in cross-section. A grey concrete dam wall stands in the middle; on the left, a deep blue reservoir held high behind it; on the right, a low river below. A green turbine sits inside the base of the dam, where water rushes from the reservoir through it into the river. A purple water pump on the right lifts river water through a pipe back up into the reservoir. A friendly textbook illustration of the real thing. Colours: water pump purple, reservoir blue, turbine green. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Every part sits inside whatever holds it in life, as an organelle sits inside its cell. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

### etc-detail

Picture: jobs/r8-respiration/art/etc-detail.png

```
Educational diagram, as a Campbell Biology figure draws it, of the inner mitochondrial membrane in cross-section: the membrane running across the middle, the intermembrane space above it tinted blue, the matrix below it tinted pink. From left to right along the membrane, the electron transport chain as three purple protein complexes passing through it; at the right end of the chain, in the matrix, an oxygen molecule as two white spheres meeting electrons and hydrogen ions to form water; then ATP synthase on the far right as a green mushroom-shaped protein passing through the membrane, its round head in the matrix. Hydrogen ions as small gold spheres are pumped up through the purple complexes, crowd the intermembrane space, and flow back down through ATP synthase into the matrix. A clean, modern biology textbook illustration: semi-realistic organelles and proteins, soft gradient shading, clean outlines, vibrant colour coding; the membrane a phospholipid bilayer; ions and small molecules as small glossy spheres. Colours: electron transport chain purple, ATP synthase green, oxygen white, intermembrane space blue, matrix pink. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Every part sits inside whatever holds it in life, as an organelle sits inside its cell. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

### cyanide-detail

Picture: jobs/r8-respiration/art/cyanide-detail.png

```
Educational diagram, as a Campbell Biology figure draws it, of the inner mitochondrial membrane in cross-section with its electron transport chain blocked: the membrane running across the middle, the intermembrane space above, the matrix below. From left to right along the membrane, the electron transport chain as three purple protein complexes passing through it, with a magenta wedge-shaped cyanide molecule jammed into the last complex; beside it in the matrix, an oxygen molecule as two white spheres, left unused; then ATP synthase on the far right as a green mushroom-shaped protein passing through the membrane, its round head in the matrix, standing still with no hydrogen ions passing through it. Only a few small gold hydrogen ions are scattered on both sides. A clean, modern biology textbook illustration: semi-realistic organelles and proteins, soft gradient shading, clean outlines, vibrant colour coding; the membrane a phospholipid bilayer; ions and small molecules as small glossy spheres. Colours: electron transport chain purple, cyanide pink, oxygen white, ATP synthase green. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Every part sits inside whatever holds it in life, as an organelle sits inside its cell. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

## the light reactions of photosynthesis (r8-photosynthesis)

### hook

Picture: jobs/r8-photosynthesis/art/hook.png

```
Educational illustration, as a biology textbook opens its photosynthesis chapter, of a pondweed growing underwater in a sunny pond: the yellow Sun in the upper left with its rays shining down into the water, the green pondweed rising from the pond floor in the middle, and a stream of white oxygen bubbles rising from its leaves toward the surface on the right. A friendly textbook illustration of the real thing. Colours: Sun yellow, Pondweed green, Oxygen bubbles white. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Every part sits inside whatever holds it in life, as an organelle sits inside its cell. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

### overview

Picture: jobs/r8-photosynthesis/art/overview.png

```
Educational diagram, as a Campbell Biology figure draws it, of a chloroplast cut open lengthwise to show its inside: the oval green chloroplast with its smooth outer membranes, stacks of green thylakoid discs on the left and in the middle, the pale fluid of the stroma around them, and on the right, in that fluid, a ring of pink arrows standing for the Calvin cycle. A clean, modern biology textbook illustration: semi-realistic organelles and proteins, soft gradient shading, clean outlines, vibrant colour coding; the membrane a phospholipid bilayer; ions and small molecules as small glossy spheres. Colours: Thylakoid green, Calvin cycle pink. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Every part sits inside whatever holds it in life, as an organelle sits inside its cell. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

### membrane-detail

Picture: jobs/r8-photosynthesis/art/membrane-detail.png

```
Educational diagram, as a Campbell Biology figure draws it, of a thylakoid membrane in cross-section showing the light reactions: the membrane running across the middle, the stroma above it tinted soft yellow, the thylakoid lumen below it tinted blue. Four protein complexes pass through the membrane, left to right in the order the electrons reach them: photosystem II in green, with a water molecule being split into oxygen and protons on its lumen side; the cytochrome b6f complex in red, with protons passing through it from the stroma down into the lumen; photosystem I in purple, with NADPH forming on its stroma side; and ATP synthase in orange, its head in the stroma, with protons flowing up through it from the lumen. Light arrives from above onto both photosystems. A clean, modern biology textbook illustration: semi-realistic organelles and proteins, soft gradient shading, clean outlines, vibrant colour coding; the membrane a phospholipid bilayer; ions and small molecules as small glossy spheres. Colours: Stroma yellow, Photosystem II green, Cytochrome b6f red, Photosystem I light purple, ATP synthase orange, Thylakoid lumen blue. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Every part sits inside whatever holds it in life, as an organelle sits inside its cell. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

### analogy

Picture: jobs/r8-photosynthesis/art/analogy.png

```
Educational illustration, as a science textbook draws it, of a small dam with a water wheel: a blue reservoir of water held behind a stone dam on the left, and water pouring through a channel in the dam onto a wooden water wheel painted orange on the right, turning it. A friendly textbook illustration of the real thing. Colours: Reservoir blue, Water wheel orange. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Every part sits inside whatever holds it in life, as an organelle sits inside its cell. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

### synthase-detail

Picture: jobs/r8-photosynthesis/art/synthase-detail.png

```
Educational diagram, as a Campbell Biology figure draws it, of ATP synthase in a thylakoid membrane, close up: the membrane across the middle, the stroma above tinted soft yellow, the thylakoid lumen below tinted blue and crowded with protons; ATP synthase in orange, its rotor in the membrane and its head in the stroma; protons flowing up from the lumen through its rotor, and ADP and phosphate joining into ATP at its head in the stroma. A clean, modern biology textbook illustration: semi-realistic organelles and proteins, soft gradient shading, clean outlines, vibrant colour coding; the membrane a phospholipid bilayer; ions and small molecules as small glossy spheres. Colours: ATP synthase orange, Thylakoid lumen blue, Stroma yellow. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Every part sits inside whatever holds it in life, as an organelle sits inside its cell. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

### herbicide-detail

Picture: jobs/r8-photosynthesis/art/herbicide-detail.png

```
Educational diagram, as a Campbell Biology figure draws it, of the same thylakoid membrane in cross-section with electron flow blocked by a herbicide: the membrane across the middle, the stroma above tinted soft yellow, the thylakoid lumen below tinted blue, and left to right photosystem II in green, the cytochrome b6f complex in red, photosystem I in purple and ATP synthase in orange. A small white herbicide molecule sits wedged against photosystem II, on the side facing the cytochrome b6f complex. Leave out any electrons or protons moving between the complexes and any protons flowing through ATP synthase. A clean, modern biology textbook illustration: semi-realistic organelles and proteins, soft gradient shading, clean outlines, vibrant colour coding; the membrane a phospholipid bilayer; ions and small molecules as small glossy spheres. Colours: Photosystem II green, Cytochrome b6f red, Photosystem I light purple, ATP synthase orange, Herbicide white. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Every part sits inside whatever holds it in life, as an organelle sits inside its cell. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

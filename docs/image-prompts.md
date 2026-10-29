# Image prompts (Muse Image)

How the studio builds each picture's prompt, and each round-8 scene's prompt as a first try sends it now
(copy-paste ready; a repaint adds the checker's complaints). The pictures are in each job's `art/` folder,
which git ignores; docs/test-rounds.md (round 8) describes them. This is a snapshot from Oct 28: the code
(studio/steps/art.py, studio/prompts/bundle.md) wins where they differ.

## How a prompt is put together

Each scene's prompt is three parts, joined with spaces (studio/steps/art.py, muse_prompt()):

1. **The scene's request**, written by the brain (Claude) in the lesson plan, as a teacher asks for a textbook
   diagram: the kind of diagram, where everything is (never what it does), then the look for that kind of subject.
2. **The colours**: `Colours: <part> <colour name>, ...`, each labelled part's colour by name (never a hex code).
3. **The style line**. A diagram's:

   > On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.

   The hook's and the analogy's, which leaves the molecules out (they scattered molecules round a sprinter):

   > On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. No text, labels, letters or numbers anywhere in the picture.

On a repaint, a sentence goes between 2 and 3: `The last picture of this scene was refused: <what the checker
found>. Make it again with these fixed.`

The call: `POST https://api.meta.ai/v1/images/generations`, model `muse-image-1.0`, size
`1792x1024` (an aspect ratio; Muse Image returns 2016x1152), n 1,
response_format b64_json, output_format png, tool_enablement {enable_web_search: false, enable_shell: false}.

What the lesson prompt tells the brain about a picture (studio/prompts/bundle.md):

```
- The picture is a still stage, and the video does the acting. Ask for where everything
  is, never for what it does: nothing pumped, flowing, carried, meeting or turning into
  something, no arrows from one part to another, no streams or crowds of ions. The video
  shows each of those as dots moving between the parts (`particles`, under Direction) and
  labels everything. A picture asked to show ions pumped up, crowding a space, flowing
  back down and oxygen turning into water came back cluttered with dots and tangled
  arrows; the same layout without them came back clean. A stage that textbooks draw as a
  shape of its own is that shape, where it happens (the Krebs cycle as a ring of arrows in
  the matrix, glycolysis as a short chain of arrows in the cytoplasm); a molecule the scene
  names is one small shape where it sits. Where a space must look crowded, ask for a soft
  tint or a light sprinkle, never a number. A hook or an analogy is the same: its things
  in place (the reservoir behind the dam, the chute down to the wheel), the dots showing
  what moves.
- Say where everything is, region by region: first the regions ("the membrane across the
  middle, the intermembrane space above it tinted blue, the matrix below it tinted pink"),
  then each part in its region, how it sits and which way it faces ("ATP synthase passing
  through the membrane, its round head in the matrix below"). For a whole system:
  "a cell's cytoplasm on the left with a glucose molecule in it; inside the same cell, on
  the right, a mitochondrion cut open lengthwise, its inner membrane folded into cristae;
  the Krebs cycle as a ring of arrows in its matrix; a row of protein complexes along the
  folded inner membrane, ATP synthase among them." Wherever the request leaves the layout
  out, the illustrator invents one, and an invented one is often wrong (the Krebs cycle
  drawn in the cytoplasm, the chain along the cell's outer membrane, ATP synthase with
  its head on the wrong side). Put every part inside what holds it (a mitochondrion
  inside its cell, not beside it). Say only what is true and what the scene needs.
```

The look it ends a request with, by subject:

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

## cellular respiration (r9-respiration)

### hook

Picture: jobs/r9-respiration/art/hook.png

```
Educational diagram, as a biology textbook chapter opener draws it, of a sprinter on a running track, seen from the side in a mid-stride pose. The sprinter wears a white running outfit; the large thigh muscle of the front leg is shown through the skin in red, the way anatomy figures show muscle. A friendly textbook illustration of the real thing. Colours: leg muscle red. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. No text, labels, letters or numbers anywhere in the picture.
```

### overview

Picture: jobs/r9-respiration/art/overview.png

```
Educational diagram, as a Campbell Biology figure draws it, of an overview of cellular respiration inside one animal cell. The cell's cytoplasm fills the frame; on the left side of the cytoplasm, glycolysis as a short chain of yellow arrows. Inside the same cell, in the middle and on the right, a large mitochondrion cut open lengthwise, its outer membrane pale beige and its inner membrane folded into cristae. The Krebs cycle as an orange ring of arrows in the mitochondrion's matrix. Along the folded inner membrane, the electron transport chain as a row of purple protein complexes passing through it, and beside them ATP synthase as a green mushroom-shaped protein passing through the inner membrane, its round head in the matrix. One oxygen molecule as two small white spheres in the matrix beside the last purple complex. A clean, modern biology textbook illustration: semi-realistic organelles, each protein complex a smooth rounded shape, soft gradient shading, clean outlines, vibrant colour coding; the membrane a phospholipid bilayer; ions and small molecules as small glossy spheres. Colours: glycolysis yellow, Krebs cycle orange, electron transport chain purple, ATP synthase green, oxygen white. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

### analogy

Picture: jobs/r9-respiration/art/analogy.png

```
Educational diagram, as a physical science textbook draws it, of a hydroelectric dam in cross-section. A grey concrete dam wall stands in the middle; on the left, a deep blue reservoir held high behind the wall; on the right, a low river at the foot of the dam. Inside the base of the dam, a chute runs from the bottom of the reservoir down to the river, with a green turbine sitting in the chute. On the right bank beside the river, a purple water pump, with a grey pipe running from it up over the dam wall to the reservoir. A friendly textbook illustration of the real thing. Colours: water pump purple, reservoir blue, turbine green. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. No text, labels, letters or numbers anywhere in the picture.
```

### etc-detail

Picture: jobs/r9-respiration/art/etc-detail.png

```
Educational diagram, as a Campbell Biology figure draws it, of the inner mitochondrial membrane in cross-section: the membrane running across the middle, the intermembrane space above it tinted blue, the matrix below it tinted pink. On the left and centre of the membrane, the electron transport chain as three purple protein complexes in a row, each passing through the membrane. One oxygen molecule as two small white spheres in the matrix just below the last purple complex. On the far right, ATP synthase as a green mushroom-shaped protein passing through the membrane, its stalk in the membrane and its round head in the matrix below. A light sprinkle of small gold hydrogen ions in the intermembrane space. A clean, modern biology textbook illustration: semi-realistic organelles, each protein complex a smooth rounded shape, soft gradient shading, clean outlines, vibrant colour coding; the membrane a phospholipid bilayer; ions and small molecules as small glossy spheres. Colours: electron transport chain purple, ATP synthase green, oxygen white, intermembrane space blue, matrix pink. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

### cyanide-detail

Picture: jobs/r9-respiration/art/cyanide-detail.png

```
Educational diagram, as a Campbell Biology figure draws it, of the inner mitochondrial membrane in cross-section: the membrane running across the middle, the intermembrane space above it, the matrix below it. On the left and centre of the membrane, the electron transport chain as three purple protein complexes in a row, each passing through the membrane, with a magenta wedge-shaped cyanide molecule lodged in the last complex. One oxygen molecule as two small white spheres in the matrix just below the last purple complex. On the far right, ATP synthase as a green mushroom-shaped protein passing through the membrane, its stalk in the membrane and its round head in the matrix below. A clean, modern biology textbook illustration: semi-realistic organelles, each protein complex a smooth rounded shape, soft gradient shading, clean outlines, vibrant colour coding; the membrane a phospholipid bilayer; ions and small molecules as small glossy spheres. Colours: electron transport chain purple, cyanide pink, oxygen white, ATP synthase green. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

## the light reactions of photosynthesis (r9-photosynthesis)

### hook

Picture: jobs/r9-photosynthesis/art/hook.png

```
Educational illustration, as a biology textbook opens its photosynthesis chapter, of a pondweed growing underwater in a sunny pond, seen from the side: the water surface across the top, the yellow Sun with its glow in the sky at the upper left above the water, the pond floor along the bottom, and a green pondweed rising from the pond floor in the middle with its narrow leaves spread through the water. A few round white oxygen bubbles cling to its leaves, and a few more sit in the water just above them. A friendly textbook illustration of the real thing. Colours: Sun yellow, Pondweed green, Oxygen bubbles white. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. No text, labels, letters or numbers anywhere in the picture.
```

### overview

Picture: jobs/r9-photosynthesis/art/overview.png

```
Educational diagram, as a Campbell Biology figure draws it, of a chloroplast cut open lengthwise to show its inside: the oval chloroplast filling the frame, its smooth double outer membrane a pale outline, the stroma inside it a pale, nearly colourless fluid. Inside the stroma, on the left and in the middle, several stacks of green thylakoid discs lie flat, each stack like a pile of coins. On the right, inside the same stroma and clear of the stacks, the Calvin cycle as a ring of pink arrows. A clean, modern biology textbook illustration: semi-realistic organelles, each protein complex a smooth rounded shape, soft gradient shading, clean outlines, vibrant colour coding; the membrane a phospholipid bilayer; ions and small molecules as small glossy spheres. Colours: Thylakoid green, Calvin cycle pink. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

### membrane-detail

Picture: jobs/r9-photosynthesis/art/membrane-detail.png

```
Educational diagram, as a Campbell Biology figure draws it, of a thylakoid membrane in cross-section: the membrane running across the middle, the stroma above it tinted soft yellow, the thylakoid lumen below it tinted blue. Four protein complexes pass through the membrane, spaced apart from left to right: photosystem II in green on the far left; the cytochrome b6f complex in red to its right; photosystem I in purple to the right of that; and ATP synthase in orange on the far right, a mushroom-shaped complex with its stalk in the membrane and its round head in the stroma above. A clean, modern biology textbook illustration: semi-realistic organelles, each protein complex a smooth rounded shape, soft gradient shading, clean outlines, vibrant colour coding; the membrane a phospholipid bilayer; ions and small molecules as small glossy spheres. Colours: Stroma yellow, Photosystem II green, Cytochrome b6f red, Photosystem I light purple, ATP synthase orange, Thylakoid lumen blue. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

### analogy

Picture: jobs/r9-photosynthesis/art/analogy.png

```
Educational illustration, as a science textbook draws it, of a small stone dam with a water wheel, seen from the side: a reservoir of blue water on the left, held behind a grey stone dam in the middle; a wooden chute running from an opening low in the dam down to the right; and a wooden water wheel painted orange standing on the right at the end of the chute, its paddles beneath the chute's mouth. A friendly textbook illustration of the real thing. Colours: Reservoir blue, Water wheel orange. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. No text, labels, letters or numbers anywhere in the picture.
```

### synthase-detail

Picture: jobs/r9-photosynthesis/art/synthase-detail.png

```
Educational diagram, as a Campbell Biology figure draws it, of ATP synthase in a thylakoid membrane, close up: the membrane across the middle, the stroma above it tinted soft yellow, the thylakoid lumen below it tinted a deep, rich blue. ATP synthase in orange stands in the centre, a mushroom-shaped complex: its ring-shaped rotor set in the membrane, a stalk rising from it, and its large round head in the stroma above. A clean, modern biology textbook illustration: semi-realistic organelles, each protein complex a smooth rounded shape, soft gradient shading, clean outlines, vibrant colour coding; the membrane a phospholipid bilayer; ions and small molecules as small glossy spheres. Colours: ATP synthase orange, Thylakoid lumen blue, Stroma yellow. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

### herbicide-detail

Picture: jobs/r9-photosynthesis/art/herbicide-detail.png

```
Educational diagram, as a Campbell Biology figure draws it, of a thylakoid membrane in cross-section with a herbicide molecule bound to photosystem II: the membrane running across the middle, the stroma above it tinted soft yellow, the thylakoid lumen below it tinted blue. Four protein complexes pass through the membrane, spaced apart from left to right: photosystem II in green on the far left; the cytochrome b6f complex in red to its right; photosystem I in purple to the right of that; and ATP synthase in orange on the far right, a mushroom-shaped complex with its stalk in the membrane and its round head in the stroma above. A small white block-shaped herbicide molecule sits wedged into the right side of photosystem II, within the membrane, facing the cytochrome b6f complex. A clean, modern biology textbook illustration: semi-realistic organelles, each protein complex a smooth rounded shape, soft gradient shading, clean outlines, vibrant colour coding; the membrane a phospholipid bilayer; ions and small molecules as small glossy spheres. Colours: Photosystem II green, Cytochrome b6f red, Photosystem I light purple, ATP synthase orange, Herbicide white. On a plain, flat dark slate blue background, every part well inside the frame, with clear space around each part for its label. Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms. No text, labels, letters or numbers anywhere in the picture.
```

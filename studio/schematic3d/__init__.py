"""Textbook-style 3D schematics, drawn without a 3D engine (prototype; docs/SCHEMATIC3D.md).

A molecular machine is drawn the way textbooks draw it: each subunit a smooth capsule or tube in
soft lavender, outlined, on flat-coloured compartments with a membrane of grey heads and yellow
tails. The shapes are fitted to the real structure (from the Protein Data Bank), so they sit where
the real subunits are, and the parts that move (a rotor) turn in true 3D. Every frame projects the
3D shapes through the camera and paints them back to front, which takes about 50 ms at 1080p on a
CPU.

    fit.py      structure file + recipe -> shapes (nm, in the machine's own frame)
    draw.py     shapes + recipe -> frames, a still or a video, with popsmosis's labels and lyric band
    recipes/    what to draw for one machine: chains to parts, axle, colours, compartments, arrows
    shapes/     fitted shapes kept for each recipe, so drawing needs no download
"""

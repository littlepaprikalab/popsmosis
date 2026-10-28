"""Textbook-style 3D schematics of molecular machines, built for each lesson (docs/SCHEMATIC3D.md).

A scene that names a molecular machine is drawn the way textbooks draw one: each subunit a smooth
shaded shape, outlined, on flat-coloured compartments with a membrane of grey heads and yellow
tails. The machine's published structure is found and fitted for the lesson, so the shapes sit
where the real subunits are. Nothing here is written for one machine or one topic.

    sources.py  the Protein Data Bank (RCSB) and OPM: searching, entry details, coordinates
    build.py    the machine for one scene: find, choose and review with the brain; fit; frame
    fit.py      a structure's chains -> smooth shapes in the machine's own frame
    model.py    a built machine: what art/<scene>.schematic.json holds
    draw.py     a built machine -> its still, its masks, and the renderer's live drawing
"""

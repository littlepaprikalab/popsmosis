import math

import numpy as np
import pytest

from studio.render import routing


def test_a_hand_off_crosses_the_join_from_one_tube_into_the_next():
    first, second = np.zeros((300, 600), bool), np.zeros((300, 600), bool)
    first[130:170, 20:300] = True  # a tube from the left...
    second[130:170, 300:580] = True  # ...joined end to end to one going on to the right
    (tx, ty), (hx, hy) = routing.handoff(routing.cells(first), routing.cells(second), (580.0, 150.0))
    assert tx < 300 < hx and abs(ty - 150) < 2 * routing.GRID and abs(hy - 150) < 2 * routing.GRID


def test_a_hand_off_along_a_long_edge_runs_inside_the_first_part_toward_where_the_flow_goes():
    # Magma rising in its conduit up through a volcano, then out as lava at the top.
    volcano, magma = np.zeros((600, 600), bool), np.zeros((600, 600), bool)
    volcano[100:500, 100:500] = True
    magma[100:560, 290:310] = True  # the conduit, the cone's whole height, and the chamber below
    volcano[100:560, 290:310] = False
    (tx, ty), (hx, hy) = routing.handoff(routing.cells(magma), routing.cells(volcano), (300.0, 40.0))
    assert hy < ty and abs(hx - tx) < abs(hy - ty)  # up, along the conduit
    assert 270 <= tx <= 330 and 270 <= hx <= 330  # inside it


def test_a_hand_off_into_a_wall_whose_next_stop_lies_across_it_crosses_it():
    # A gas from the alveolus into the membrane it shares with a capillary: across the long edge, not along it.
    alveolus, membrane = np.zeros((600, 600), bool), np.zeros((600, 600), bool)
    alveolus[50:550, 50:290] = True
    membrane[50:550, 290:310] = True
    (tx, ty), (hx, hy) = routing.handoff(routing.cells(alveolus), routing.cells(membrane), (450.0, 300.0))
    assert hx > tx and abs(hy - ty) < abs(hx - tx)


def test_a_hand_off_between_parts_apart_spans_the_gap():
    a, b = np.zeros((200, 600), bool), np.zeros((200, 600), bool)
    a[60:140, 20:150] = True
    b[60:140, 400:560] = True
    (tx, _), (hx, _) = routing.handoff(routing.cells(a), routing.cells(b), (480.0, 100.0))
    assert 130 <= tx <= 160 and 390 <= hx <= 420


def test_parts_meet_when_they_touch_and_a_path_is_measured_inside_a_part():
    a = np.zeros((20, 40), bool)
    b = np.zeros((20, 40), bool)
    a[5:15, 2:12] = True
    b[5:15, 13:25] = True  # one cell apart: they meet
    assert routing.meet(a, b)
    far = np.zeros((20, 40), bool)
    far[5:15, 30:38] = True
    assert not routing.meet(a, far)
    g = routing.GRID
    across = [(0.0, 10 * g), (20 * g, 10 * g), (40 * g, 10 * g)]  # two long, straight steps along row 10
    # Only what runs inside each part counts, not the whole of a step that ends in it: a straight step from well
    # outside a carrier to its middle is not a run along it.
    assert routing.inside(across, a) == pytest.approx(10 * g) and routing.inside(across, far) == pytest.approx(8 * g)
    assert routing.inside(across, b) == pytest.approx(12 * g)


def test_a_part_with_nothing_on_the_grid_gets_no_arrow():
    a = np.zeros((20, 40), bool)
    a[5:15, 2:12] = True
    assert routing.handoff(a, np.zeros((20, 40), bool), (100.0, 60.0)) == ((0.0, 0.0), (0.0, 0.0))


def test_parts_that_nearly_touch_get_an_arrow_as_long_as_any_other_across_their_gap():
    a = np.zeros((40, 60), bool)
    b = np.zeros((40, 60), bool)
    a[10:30, 5:20] = True
    b[10:30, 25:40] = True  # five cells apart: no meeting, a gap too short for an arrow of its own
    (x0, y0), (x1, y1) = routing.handoff(a, b, (60 * routing.GRID, 20 * routing.GRID))
    assert math.hypot(x1 - x0, y1 - y0) == pytest.approx(2 * routing.ARROW_CELLS * routing.GRID)
    assert x1 > x0 and 20 * routing.GRID <= (x0 + x1) / 2 <= 25 * routing.GRID  # across the gap, toward b

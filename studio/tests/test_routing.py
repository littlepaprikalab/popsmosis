import math

import numpy as np
import pytest

from studio.render import renderer, routing


def region(mask: np.ndarray) -> np.ndarray:
    return routing.joined(routing.cells(mask))


def inside(mask: np.ndarray, q, slack: int = routing.GRID * 2) -> bool:
    """q lies in the mask, or within a cell or two of it (the grid and the smoothing)."""
    x, y = int(q[0]), int(q[1])
    return mask[max(0, y - slack):y + slack + 1, max(0, x - slack):x + slack + 1].any()


def test_a_leg_runs_through_a_bent_tube_round_its_corner_not_across_it():
    tube = np.zeros((400, 400), bool)
    tube[40:80, 40:360] = True  # along the top, left to right...
    tube[40:360, 320:360] = True  # ...then down the right side
    a, b = (50.0, 60.0), (340.0, 350.0)
    path = routing.route(region(tube), a, b)
    assert path[0] == a and path[-1] == b
    assert all(inside(tube, q) for q in path)
    assert routing.length(path) > 1.3 * math.dist(a, b)  # round the corner, not the diagonal
    assert min(math.dist(q, (340, 60)) for q in path) < 30


def test_a_leg_keeps_to_the_middle_of_a_wide_part():
    band = np.zeros((300, 600), bool)
    band[60:240, :] = True
    path = routing.route(region(band), (20.0, 150.0), (580.0, 150.0))
    middle = [q for q in path if 100 < q[0] < 500]
    assert middle and all(abs(q[1] - 150) < 2 * routing.GRID for q in middle)


def test_parts_that_do_not_join_or_join_only_the_long_way_round_have_no_route():
    apart = np.zeros((200, 400), bool)
    apart[50:150, 20:120] = True
    apart[50:150, 280:380] = True
    assert routing.route(region(apart), (70.0, 100.0), (330.0, 100.0)) is None
    hairpin = np.zeros((120, 1200), bool)
    hairpin[0:30, :] = True
    hairpin[70:100, :] = True
    hairpin[0:100, 1160:1200] = True  # joined only at the far end
    assert routing.route(region(hairpin), (20.0, 15.0), (20.0, 85.0)) is None


def test_two_parts_drawn_edge_to_edge_are_joined():
    left, right = np.zeros((120, 240), bool), np.zeros((120, 240), bool)
    left[20:100, 10:118] = True
    right[20:100, 121:230] = True  # a hairline apart
    both = routing.joined(routing.cells(left), routing.cells(right))
    assert routing.route(both, (30.0, 60.0), (210.0, 60.0)) is not None


def test_particles_follow_a_route_and_take_as_long_as_it_is():
    points = [(0.0, 0.0), (300.0, 0.0)]
    detour = [(0.0, 0.0), (0.0, 300.0), (300.0, 300.0), (300.0, 0.0)]
    assert renderer.trip_s(points, [detour]) == pytest.approx(
        renderer.FLOW_PAUSE_S + 900 / renderer.FLOW_SPEED)
    dots = [q for q, _ in renderer.stream_at(points, 1.0, [detour])]
    assert dots and all(q[0] in (0.0, 300.0) or q[1] == pytest.approx(300.0) for q in dots)
    halfway, _ = renderer.lead_at(points, renderer.FLOW_PAUSE_S + 900 / renderer.FLOW_SPEED / 2, [detour])
    assert halfway[1] > 0  # off the straight line, along the route


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

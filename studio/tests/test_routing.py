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

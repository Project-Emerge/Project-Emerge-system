"""Coverage guidance as data, with the Italian wizard overlay left byte-identical."""

import numpy as np
import pytest

from vision_system.calibration.samples import Coverage, CoverageGap


def _full() -> Coverage:
    coverage = Coverage()
    coverage.grid = np.ones((3, 3), dtype=np.int32)
    coverage.scales = {"far": 99, "medium": 99, "near": 99}
    coverage.tilts = dict.fromkeys(coverage.tilts, 99)
    return coverage


def test_an_empty_grid_cell_is_reported_with_its_position():
    coverage = _full()
    coverage.grid[0, 2] = 0
    assert coverage.gap() == CoverageGap("grid", "top right")


def test_the_grid_is_reported_before_scale_or_tilt():
    coverage = Coverage()          # everything empty
    assert coverage.gap().kind == "grid"


@pytest.mark.parametrize("scale", ["far", "medium", "near"])
def test_each_missing_distance_is_named(scale):
    coverage = _full()
    coverage.scales[scale] = 0
    assert coverage.gap() == CoverageGap("scale", scale)


@pytest.mark.parametrize("direction", ["left", "right", "up", "down"])
def test_each_missing_tilt_is_named(direction):
    coverage = _full()
    coverage.tilts[direction] = 0
    assert coverage.gap() == CoverageGap("tilt", direction)


def test_full_coverage_has_no_gap():
    assert _full().gap() is None


# The wizards draw instruction() into their OpenCV overlay and stay Italian; the
# refactor that introduced gap() must not have changed a single one of those strings.
@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda c: c.grid.__setitem__((0, 0), 0), "Sposta la board: alto sinistra"),
        (lambda c: c.grid.__setitem__((1, 1), 0), "Sposta la board: centro centro"),
        (lambda c: c.grid.__setitem__((2, 2), 0), "Sposta la board: basso destra"),
        (lambda c: c.scales.__setitem__("far", 0), "Allontana la board"),
        (lambda c: c.scales.__setitem__("medium", 0), "Porta la board a distanza media"),
        (lambda c: c.scales.__setitem__("near", 0), "Avvicina la board"),
        (lambda c: c.tilts.__setitem__("left", 0), "Inclina il lato sinistro verso la camera"),
        (lambda c: c.tilts.__setitem__("right", 0), "Inclina il lato destro verso la camera"),
        (lambda c: c.tilts.__setitem__("up", 0), "Inclina il lato superiore verso la camera"),
        (lambda c: c.tilts.__setitem__("down", 0), "Inclina il lato inferiore verso la camera"),
        (lambda c: None, "Copertura completa: attendi il calcolo"),
    ],
)
def test_the_italian_overlay_wording_is_unchanged(mutate, expected):
    coverage = _full()
    mutate(coverage)
    assert coverage.instruction() == expected

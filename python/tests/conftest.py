"""Shared fixtures for the physics, analyser and AI tests.

Run from the ``python/`` directory: ``python -m pytest``.
"""

import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from track_analyzer import analyze  # noqa: E402

AURORA = {
    "id": "aurora-park",
    "version": 1,
    "name": "Aurora Park",
    "width": 13,
    "points": [
        {"x": -170, "y": 1, "z": 70},
        {"x": -170, "y": 1, "z": -65},
        {"x": -125, "y": 2, "z": -120},
        {"x": -10, "y": 4, "z": -120},
        {"x": 90, "y": 7, "z": -100},
        {"x": 150, "y": 8, "z": -45},
        {"x": 150, "y": 7, "z": 45},
        {"x": 115, "y": 6, "z": 70},
        {"x": 60, "y": 5, "z": 50},
        {"x": 20, "y": 4, "z": 90},
        {"x": 0, "y": 3, "z": 125},
        {"x": -35, "y": 1, "z": 150},
        {"x": -115, "y": 1, "z": 125},
    ],
}


def circle_document(radius=100.0, width=20.0, points=32, runoff=30.0, bank_deg=0.0, elevation=None):
    """Closed circle (clockwise in the editor's top view, i.e. a right-hand turn)."""
    pts = []
    for i in range(points):
        a = 2.0 * math.pi * i / points
        y = 0.0 if elevation is None else elevation(a)
        pts.append({"x": radius * math.cos(a), "y": y, "z": radius * math.sin(a), "bank": bank_deg})
    return {
        "id": "circle",
        "version": 1,
        "name": "Circle",
        "width": width,
        "points": pts,
        "runoff": {"type": "asphalt", "width": runoff},
    }


def oval_document(straight=1200.0, radius=150.0, width=20.0, runoff=30.0, elevation=0.0):
    """Stadium oval: two long straights joined by half circles (control points)."""
    pts = []
    half = straight / 2.0
    for k in range(9):
        pts.append({"x": -half + straight * k / 8.0, "y": elevation, "z": -radius})
    for k in range(1, 8):
        a = -math.pi / 2 + math.pi * k / 8.0
        pts.append({"x": half + radius * math.cos(a), "y": elevation, "z": radius * math.sin(a)})
    for k in range(9):
        pts.append({"x": half - straight * k / 8.0, "y": elevation, "z": radius})
    for k in range(1, 8):
        a = math.pi / 2 + math.pi * k / 8.0
        pts.append({"x": -half + radius * math.cos(a), "y": elevation, "z": radius * math.sin(a)})
    return {
        "id": "oval",
        "version": 1,
        "name": "Oval",
        "width": width,
        "points": pts,
        "runoff": {"type": "asphalt", "width": runoff},
    }


@pytest.fixture(scope="session")
def aurora_doc():
    return dict(AURORA)


@pytest.fixture(scope="session")
def aurora_full():
    return analyze(AURORA, "full")


@pytest.fixture(scope="session")
def aurora_quick():
    return analyze(AURORA, "quick")


@pytest.fixture(scope="session")
def circle100():
    return analyze(circle_document(100.0), "quick")


@pytest.fixture(scope="session")
def oval_quick():
    return analyze(oval_document(), "quick")

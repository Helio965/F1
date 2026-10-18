"""Historical circuit records and analysis-cache integrity (code verification).

The records below wrap synthetic geometry (the test oval); they are not real
circuits. What is verified is the bookkeeping: provenance rules, computed
validation state, flagged estimates and that a historical record goes through
exactly the same analyser as any other document.
"""

import copy
import json

import pytest

from conftest import oval_document
from apex_bridge import api
from track_analyzer import analyze
from track_analyzer.catalog import CATALOG_VERSION, LENGTH_TOLERANCE, analyze_record, validate_record

pytestmark = pytest.mark.verification


@pytest.fixture(scope="module")
def oval_length():
    return analyze(oval_document(), "quick")["length"]


def record(length, **changes):
    base = {
        "catalog": CATALOG_VERSION,
        "id": "test-oval-1990",
        "name": "Oval de teste",
        "layout": "Completo",
        "country": "BR",
        "years": {"from": 1990, "to": 1994},
        "reference_length_m": length,
        "provenance": {
            "geometry": "published-map",
            "sources": [{"title": "Mapa sintético usado nos testes"}],
            "estimated": ["width", "runoff"],
        },
        "document": oval_document(),
    }
    base.update(changes)
    return base


def test_matching_length_with_sources_is_length_matched(oval_length):
    out = analyze_record(record(oval_length), "quick", current_year=2026)
    assert out["state"] == "length-matched"
    assert out["estimated"] == ["width", "runoff"]
    assert abs(out["length_error"]) < 1e-6
    # Same analyser, same hash as analysing the bare document.
    assert out["analysis"]["hash"] == analyze(oval_document(), "quick")["hash"]


def test_length_outside_tolerance_is_only_geometry_checked(oval_length):
    off = oval_length * (1.0 + 3.0 * LENGTH_TOLERANCE)
    out = analyze_record(record(off), "quick", current_year=2026)
    assert out["state"] == "geometry-checked"
    assert "length.mismatch" in [i["code"] for i in out["issues"]]


def test_approximate_geometry_never_reaches_length_matched(oval_length):
    rec = record(oval_length)
    rec["provenance"] = {"geometry": "approximate", "sources": [], "estimated": []}
    out = analyze_record(rec, "quick", current_year=2026)
    assert out["state"] == "geometry-checked"
    assert "provenance.approximate" in [i["code"] for i in out["issues"]]


@pytest.mark.parametrize(
    "change, code",
    [
        ({"years": {"from": 1975, "to": 1979}}, "years.from"),
        ({"years": {"from": 1990, "to": 1985}}, "years.to"),
        ({"years": {"from": 1990, "to": 2099}}, "years.to"),
        ({"state": "length-matched"}, "record.unknown"),
        ({"catalog": 99}, "record.catalog"),
        ({"reference_length_m": 50.0}, "length.reference"),
        ({"provenance": {"geometry": "published-map", "sources": []}}, "provenance.sources"),
        ({"provenance": {"geometry": "survey", "sources": [{"title": "x"}], "estimated": ["mu"]}}, "provenance.estimated"),
        ({"provenance": {"geometry": "rumour", "sources": [{"title": "x"}]}}, "provenance.geometry"),
        ({"name": ""}, "record.name"),
    ],
)
def test_bad_metadata_is_rejected(oval_length, change, code):
    rec = record(oval_length, **change)
    assert code in [i["code"] for i in validate_record(rec, current_year=2026)]
    assert analyze_record(rec, "quick", current_year=2026)["state"] == "rejected"


def test_invalid_geometry_is_rejected(oval_length):
    doc = oval_document()
    doc["width"] = 3.0
    out = analyze_record(record(oval_length, document=doc), "quick", current_year=2026)
    assert out["state"] == "rejected"
    assert not out["analysis"]["valid"]


def test_cached_analysis_from_another_version_is_refused():
    """A cached analysis is derived data: one produced by another analyser or
    engine version must not be re-registered for racing."""
    full = json.loads(api.analyze_track(json.dumps(oval_document()), "full"))
    assert api.remember_analysis(json.dumps(full)) == full["hash"]
    for key, value in (("analyzer_version", "0.0.1"), ("engine_version", "0.0.1"), ("level", "quick"), ("valid", False)):
        stale = copy.deepcopy(full)
        stale[key] = value
        with pytest.raises(ValueError):
            api.remember_analysis(json.dumps(stale))


def test_analysis_json_is_strict_for_malformed_numbers():
    """Non-numeric width/run-off/exposure are fatal validation errors and the
    JSON returned to the browser never contains NaN/Infinity tokens."""
    for patch in ({"width": "abc"}, {"runoff": {"width": "NaN"}}, {"environment": {"exposure": "Infinity"}}):
        doc = oval_document()
        doc.update(patch)
        text = api.analyze_track(json.dumps(doc), "full")
        assert "NaN" not in text and "Infinity" not in text
        result = json.loads(text)
        assert result["valid"] is False and result["errors"]

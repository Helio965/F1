"""Functions called from the Web Worker through Pyodide.

Infrequent calls exchange JSON strings (analysis, session creation, events,
metrics); the per-frame ``advance`` returns an ``array('d')`` that the worker
copies into a transferable Float64Array. No network access is involved.
"""

import json
from array import array

from physics_engine import ENGINE_VERSION
from physics_engine.contract import describe
from physics_engine.vehicles import VEHICLES, vehicle_summary
from track_analyzer.analyzer import ANALYZER_VERSION, analyze

from .session import Session

_session = None
_analyses = {}


def versions():
    return json.dumps(
        {"engine": ENGINE_VERSION, "analyzer": ANALYZER_VERSION, "contract": describe()},
        separators=(",", ":"),
    )


def vehicles():
    return json.dumps([vehicle_summary(v) for v in VEHICLES.values()], separators=(",", ":"))


def analyze_track(document_json, level="full"):
    document = json.loads(document_json)
    result = analyze(document, level)
    if result.get("valid") and level == "full":
        _analyses[result["hash"]] = result
        while len(_analyses) > 6:
            _analyses.pop(next(iter(_analyses)))
    return json.dumps(result, separators=(",", ":"))


def remember_analysis(analysis_json):
    """Register a cached analysis (e.g. from IndexedDB) after the worker restarts."""
    result = json.loads(analysis_json)
    _analyses[result["hash"]] = result
    return result["hash"]


def create_session(analysis_hash, config_json):
    global _session
    analysis = _analyses.get(analysis_hash)
    if analysis is None:
        raise ValueError("Análise da pista não encontrada; execute a análise novamente.")
    _session = Session(analysis, json.loads(config_json))
    return json.dumps(_session.info(), separators=(",", ":"))


def advance(elapsed, throttle, brake, steer, drs, analog):
    return array("d", _session.advance(elapsed, throttle, brake, steer, drs, analog))


def reset_player():
    _session.reset_player()


def set_brake_bias(value):
    return _session.set_brake_bias(value)


def drain_events():
    return json.dumps(_session.drain_events(), separators=(",", ":"))


def surface_grid():
    version, segments, lanes, grid = _session.surface_grid()
    return version, segments, lanes, array("f", grid)


def ping():
    return 1


def metrics():
    return json.dumps(_session.metrics(), separators=(",", ":"))


def end_session():
    global _session
    _session = None

"""Track analysis pipeline (deterministic, no generative AI).

``analyze(document, level)``:

* ``quick``: validation, geometry, segmentation and trackside layout. Fast
  enough to run after every edit in the editor.
* ``full``: + racing line, reference speed profiles per vehicle (dry and wet),
  braking/acceleration zones, hazards, navigability and recommendations.

The result carries a SHA-256 ``hash`` of the canonical physical inputs plus
the analyser and engine versions. Caches (IndexedDB in the browser, bundled
JSON for historical circuits) are only valid for the same hash; any edit of
geometry, surface, run-off or environment, or a new analyser version,
produces a new hash and invalidates the derived data.
"""

import hashlib
import json
import math
import time

from physics_engine import ENGINE_VERSION
from physics_engine.environment import Atmosphere
from physics_engine.track import SURFACE_NAMES
from physics_engine.vehicles import VEHICLES, DEFAULT_VEHICLE

from . import geometry as geo
from . import segmentation as seg
from .circuit import ASPHALT_TYPES, canonical, normalise
from .hazards import detect as detect_hazards
from .profiles import compute_profile, zones
from .racing_line import line_geometry, racing_line
from .validation import validate_document, validate_geometry

ANALYZER_VERSION = "1.1.0"
CONTRACT = "apex.track-analysis/1"


def analysis_hash(circuit):
    payload = {
        "analyzer": ANALYZER_VERSION,
        "engine": ENGINE_VERSION,
        "track": canonical(circuit),
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _round_list(values, digits=4):
    return [round(v, digits) for v in values]


def analyze(document, level="full", vehicles=None):
    t0 = time.perf_counter()
    timings = {}
    circuit = normalise(document)
    result = {
        "contract": CONTRACT,
        "analyzer_version": ANALYZER_VERSION,
        "engine_version": ENGINE_VERSION,
        "level": level,
        "circuit": {"id": circuit["id"], "version": circuit["version"], "name": circuit["name"]},
        "hash": analysis_hash(circuit),
    }
    errors, warnings, fatal = validate_document(circuit)
    if fatal:
        result.update(valid=False, errors=errors, warnings=warnings, length=0.0)
        return result
    spline = geo.build_spline(circuit)
    table = geo.uniform_table(spline)
    timings["geometry_ms"] = (time.perf_counter() - t0) * 1000.0
    g_errors, g_warnings = validate_geometry(circuit, spline, table)
    errors += g_errors
    warnings += g_warnings
    timings["validation_ms"] = (time.perf_counter() - t0) * 1000.0 - timings["geometry_ms"]
    n = len(table["x"])
    half = 0.5 * circuit["width"]
    local = max(circuit["width"] * 3.0, 40.0)
    # Barriers sit up to half + run-off + 4 m out (segmentation.trackside_layout);
    # two facing sections need to see each other at twice that distance.
    reach = 2.0 * (half + circuit["runoff"]["width"] + 4.0) + 2.0
    clear_l, clear_r = geo.clearances(table, local, max(80.0, reach))
    corners = seg.find_corners(table)
    straights = seg.find_straights(table, corners)
    layout = seg.trackside_layout(circuit, table, corners, clear_l, clear_r)
    layout["half_width"] = [half] * n
    asphalt = ASPHALT_TYPES[circuit["surface"]["asphalt"]]
    exposure = circuit["environment"]["exposure"]
    physics_table = {
        "length": table["length"],
        "spacing": table["spacing"],
        "x": _round_list(table["x"]),
        "y": _round_list(table["y"]),
        "z": _round_list(table["z"]),
        "tx": _round_list(table["tx"], 6),
        "tz": _round_list(table["tz"], 6),
        "curvature": _round_list(table["curvature"], 6),
        "bank": _round_list(table["bank"], 6),
        "grade": _round_list(table["grade"], 6),
        "vertical_curvature": _round_list(table["vertical_curvature"], 7),
        "half_width": layout["half_width"],
        "barrier_left": _round_list(layout["barrier_left"], 3),
        "barrier_right": _round_list(layout["barrier_right"], 3),
        "kerb_left": layout["kerb_left"],
        "kerb_right": layout["kerb_right"],
        "runoff_left": layout["runoff_left"],
        "runoff_right": layout["runoff_right"],
        "grip": [asphalt["grip"]] * n,
        "roughness": [asphalt["texture"]] * n,
        "exposure": [exposure] * n,
    }
    adaptive = geo.adaptive_samples(spline)
    min_radius = 1.0 / max(max(abs(k) for k in table["curvature"]), 1e-9)
    stats = {
        "length": round(table["length"], 2),
        "physics_samples": n,
        "physics_spacing": round(table["spacing"], 4),
        "adaptive_samples": len(adaptive),
        "adaptive_min_spacing": round(min(b["s"] - a["s"] for a, b in zip(adaptive, adaptive[1:])), 3)
        if len(adaptive) > 1
        else 0.0,
        "corners": len(corners),
        "left_corners": sum(1 for c in corners if c["direction"] == "left"),
        "right_corners": sum(1 for c in corners if c["direction"] == "right"),
        "straights": len(straights),
        "longest_straight": max((s["length"] for s in straights), default=0.0),
        "min_radius": round(min_radius, 2),
        "elevation_range": round(max(table["y"]) - min(table["y"]), 2),
        "max_grade_pct": round(100.0 * max(abs(g) for g in table["grade"]), 2),
        "max_bank_deg": round(math.degrees(max(abs(b) for b in table["bank"])), 2),
        "asphalt": circuit["surface"]["asphalt"],
        "runoff": circuit["runoff"]["type"],
        "surface_materials": sorted(
            {SURFACE_NAMES[m] for m in layout["runoff_left"] + layout["runoff_right"]} | {"asphalt", "kerb"}
        ),
    }
    timings["segmentation_ms"] = (time.perf_counter() - t0) * 1000.0 - sum(timings.values())
    result.update(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        length=round(table["length"], 3),
        stats=stats,
        physics_table=physics_table,
        corners=corners,
        straights=straights,
        segments=seg.physical_segments(table, layout),
        adaptive={
            "s": [round(a["s"], 3) for a in adaptive],
            "curvature": [round(a["curvature"], 6) for a in adaptive],
        },
        environment=circuit["environment"],
    )
    if level != "full" or errors:
        timings["total_ms"] = (time.perf_counter() - t0) * 1000.0
        result["timings"] = {k: round(v, 2) for k, v in timings.items()}
        return result

    # ---------------------------------------------------------------- full
    t1 = time.perf_counter()
    line = racing_line(table, layout["half_width"])
    px, pz, line_ds, line_k = line_geometry(table, line["offset"])
    line_data = {"offset": line["offset"], "ds": line_ds, "curvature": line_k}
    timings["racing_line_ms"] = (time.perf_counter() - t1) * 1000.0
    t2 = time.perf_counter()
    vehicle_ids = vehicles or [DEFAULT_VEHICLE]
    profiles = {}
    reference = None
    for vid in vehicle_ids:
        params = VEHICLES.get(vid)
        if params is None:
            continue
        entry = {}
        for condition, preset, compound in (
            ("dry", "sun", params.default_compound),
            ("wet", "rain", "wet" if "wet" in params.compounds else params.default_compound),
        ):
            atm = Atmosphere(preset, altitude=circuit["environment"]["altitude"])
            wet_scale = None
            if condition == "wet":
                # Typical wet-grip ratio of the chosen compound on a drained film.
                from physics_engine.vehicles import COMPOUNDS

                wet_scale = [COMPOUNDS[compound].wet_ratio] * n
            prof = compute_profile(table, line_data, params, compound, atm, mu_scale=wet_scale)
            entry[condition] = {
                "compound": compound,
                "lap_time": round(prof["lap_time"], 3),
                "top_speed_kmh": round(prof["top_speed"] * 3.6, 1),
                "min_speed_kmh": round(prof["min_speed"] * 3.6, 1),
                "nodes": prof["nodes"],
                "speed": _round_list(prof["speed"], 3),
            }
            if vid == vehicle_ids[0] and condition == "dry":
                reference = prof
        profiles[vid] = entry
    timings["profiles_ms"] = (time.perf_counter() - t2) * 1000.0
    zone_data = zones(table, reference, corners) if reference else {"braking": [], "acceleration": []}
    for c in corners:
        if reference:
            c["apex_speed_kmh"] = round(reference["speed"][c["apex_index"]] * 3.6, 1)
    hazards = detect_hazards(table, layout, corners, reference, clear_l, clear_r) if reference else []
    navigable = bool(reference and reference["min_speed"] > 3.0 and math.isfinite(reference["lap_time"]))
    recommendations = _recommendations(stats, hazards, corners, reference)
    if not navigable:
        errors.append({"code": "navigability", "message": "O perfil de velocidade não completa a volta com segurança."})
    result.update(
        valid=not errors,
        errors=errors,
        racing_line={"offset": _round_list(line["offset"], 3)},
        profiles=profiles,
        braking_zones=zone_data["braking"],
        acceleration_zones=zone_data["acceleration"],
        hazards=hazards,
        navigable=navigable,
        recommendations=recommendations,
        reference_vehicle=vehicle_ids[0],
    )
    timings["total_ms"] = (time.perf_counter() - t0) * 1000.0
    result["timings"] = {k: round(v, 2) for k, v in timings.items()}
    return result


def _recommendations(stats, hazards, corners, reference):
    out = []
    kinds = {h["type"] for h in hazards}
    if "runoff.short" in kinds:
        out.append("Amplie a área de escape nas curvas rápidas marcadas ou afaste trechos vizinhos.")
    if "crest.airborne" in kinds or "crest.corner" in kinds:
        out.append("Suavize a elevação perto das cristas para manter os pneus carregados.")
    if "water.puddle" in kinds:
        out.append("Adicione inclinação lateral (1–2°) nos pontos baixos para drenar a água.")
    if "compression" in kinds:
        out.append("Reduza a variação brusca de elevação nas compressões.")
    if stats["longest_straight"] > 600 and stats["corners"] < 4:
        out.append("Traçado de alta velocidade: considere uma chicane para criar zonas de frenagem.")
    if reference and reference["min_speed"] * 3.6 < 45:
        out.append("Há curvas muito lentas: verifique se o raio mínimo é intencional.")
    if not out:
        out.append("Nenhum ajuste crítico identificado. Teste o circuito ao volante antes de homologar.")
    return out


def analysis_json(document_json, level="full"):
    """JSON in / JSON out convenience used by the browser bridge."""
    return json.dumps(analyze(json.loads(document_json), level), separators=(",", ":"))

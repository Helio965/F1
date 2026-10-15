"""Circuit document normalisation (input contract of the analyser).

A circuit document (JSON) is::

    {
      "id": str, "version": int, "name": str,
      "width": float,                       # m, asphalt width
      "points": [{"x", "y", "z", "bank"?}], # m; bank in degrees, + = left side higher
      "surface"?: {"asphalt": "standard" | "smooth" | "abrasive"},
      "runoff"?: {"type": "mixed" | "grass" | "gravel" | "asphalt", "width": float},
      "environment"?: {"altitude": float, "exposure": float}
    }

Unknown asphalt/run-off values fall back to defaults; numeric ranges are
checked by :mod:`track_analyzer.validation`. Nothing in the document is
executed.
"""

ASPHALT_TYPES = {
    # grip multiplier, macrotexture depth (mm): abrasive surfaces grip more,
    # drain better and wear tyres faster; smooth surfaces the opposite.
    "standard": {"grip": 1.0, "texture": 0.8, "label": "Asfalto padrão"},
    "smooth": {"grip": 0.97, "texture": 0.5, "label": "Asfalto liso"},
    "abrasive": {"grip": 1.03, "texture": 1.2, "label": "Asfalto abrasivo"},
}
RUNOFF_TYPES = ("mixed", "grass", "gravel", "asphalt")
DEFAULT_RUNOFF_WIDTH = 6.5  # m beyond the asphalt edge to the barrier


def normalise(doc):
    points = []
    for p in doc.get("points", []):
        points.append(
            {
                "x": float(p.get("x", 0.0)),
                "y": float(p.get("y", 0.0)),
                "z": float(p.get("z", 0.0)),
                "bank": float(p.get("bank", 0.0) or 0.0),
            }
        )
    surface = doc.get("surface") or {}
    asphalt = surface.get("asphalt", "standard")
    if asphalt not in ASPHALT_TYPES:
        asphalt = "standard"
    runoff = doc.get("runoff") or {}
    runoff_type = runoff.get("type", "mixed")
    if runoff_type not in RUNOFF_TYPES:
        runoff_type = "mixed"
    try:
        runoff_width = float(runoff.get("width", DEFAULT_RUNOFF_WIDTH))
    except (TypeError, ValueError):
        runoff_width = DEFAULT_RUNOFF_WIDTH
    env = doc.get("environment") or {}
    try:
        width = float(doc.get("width", 12.0))
    except (TypeError, ValueError):
        width = float("nan")
    return {
        "id": str(doc.get("id", "circuit")),
        "version": int(doc.get("version", 1)),
        "name": str(doc.get("name", "Circuito")),
        "width": width,
        "points": points,
        "surface": {"asphalt": asphalt},
        "runoff": {"type": runoff_type, "width": runoff_width},
        "environment": {
            "altitude": float(env.get("altitude", 0.0) or 0.0),
            "exposure": float(env.get("exposure", 1.0) if env.get("exposure") is not None else 1.0),
        },
    }


def canonical(circuit):
    """Inputs that define the physical track (used for the cache hash)."""
    return {
        "width": round(circuit["width"], 6),
        "points": [
            [round(p["x"], 6), round(p["y"], 6), round(p["z"], 6), round(p["bank"], 6)]
            for p in circuit["points"]
        ],
        "surface": circuit["surface"],
        "runoff": {"type": circuit["runoff"]["type"], "width": round(circuit["runoff"]["width"], 6)},
        "environment": {
            "altitude": round(circuit["environment"]["altitude"], 3),
            "exposure": round(circuit["environment"]["exposure"], 4),
        },
    }

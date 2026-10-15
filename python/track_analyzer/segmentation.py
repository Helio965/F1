"""Corner/straight identification, physical segmentation and trackside layout.

Corners: contiguous regions where |kappa| exceeds 1/400 m^-1 (radius < 400 m)
with hysteresis (exit below 1/600) and a minimum swept angle of 8 degrees.
Corners in the same direction separated by less than 25 m are merged into one
complex. Each corner reports start/apex/end, minimum radius, direction, swept
angle and a class.

Physical segments group consecutive physics samples sharing curvature class,
grade class, banking class and surface material, giving a compact description
for the report and the renderer.

Trackside layout per sample: kerbs (inside around the apex, outside at the
exit), run-off material per side and barrier offsets bounded by the clearance
to neighbouring sections of the track.
"""

import math

from physics_engine.track import (
    SURFACE_GRASS,
    SURFACE_GRAVEL,
    SURFACE_RUNOFF_ASPHALT,
)

ENTER_CURVATURE = 1.0 / 400.0
EXIT_CURVATURE = 1.0 / 600.0
MIN_CORNER_ANGLE = math.radians(8.0)
MERGE_GAP = 25.0
KERB_WIDTH = 1.0


def find_corners(table):
    k = table["curvature"]
    n = len(k)
    ds = table["spacing"]
    # rotate so we start on a straight-ish sample (avoid splitting a corner at s=0)
    start = min(range(n), key=lambda i: abs(k[i]))
    raw = []
    inside = False
    cur = None
    for step in range(n + 1):
        i = (start + step) % n
        a = abs(k[i])
        if not inside and a > ENTER_CURVATURE:
            inside = True
            cur = {"start": i, "sign": 1 if k[i] > 0 else -1, "samples": [i]}
        elif inside:
            same = (k[i] > 0) == (cur["sign"] > 0)
            if a < EXIT_CURVATURE or not same:
                inside = False
                cur["end"] = cur["samples"][-1]
                raw.append(cur)
                if a > ENTER_CURVATURE and not same:
                    inside = True
                    cur = {"start": i, "sign": 1 if k[i] > 0 else -1, "samples": [i]}
            else:
                cur["samples"].append(i)
    if inside and cur:
        cur["end"] = cur["samples"][-1]
        raw.append(cur)
    # merge same-direction corners separated by a short gap
    merged = []
    for c in raw:
        if merged:
            last = merged[-1]
            gap = ((c["start"] - last["end"]) % n) * ds
            if c["sign"] == last["sign"] and gap < MERGE_GAP:
                last["samples"].extend(range(last["end"] + 1, last["end"] + 1 + int(gap / ds)))
                last["samples"].extend(c["samples"])
                last["end"] = c["end"]
                continue
        merged.append(c)
    pieces = []
    for c in merged:
        idx = [i % n for i in c["samples"]]
        for part in _split_complex(idx, k):
            pieces.append({"sign": c["sign"], "idx": part})
    corners = []
    for c in pieces:
        idx = c["idx"]
        angle = sum(k[i] for i in idx) * ds
        if abs(angle) < MIN_CORNER_ANGLE:
            continue
        apex = max(idx, key=lambda i: abs(k[i]))
        min_radius = 1.0 / max(abs(k[apex]), 1e-9)
        deg = abs(math.degrees(angle))
        if deg > 150 and min_radius < 30:
            kind = "hairpin"
        elif deg < 25:
            kind = "kink"
        elif min_radius < 40:
            kind = "slow"
        elif min_radius < 120:
            kind = "medium"
        else:
            kind = "fast"
        corners.append(
            {
                "start_index": idx[0],
                "end_index": idx[-1],
                "apex_index": apex,
                "start": round(idx[0] * ds, 2),
                "end": round(idx[-1] * ds, 2),
                "apex": round(apex * ds, 2),
                "length": round(len(idx) * ds, 2),
                "direction": "left" if c["sign"] > 0 else "right",
                "min_radius": round(min_radius, 2),
                "angle_deg": round(deg, 1),
                "kind": kind,
            }
        )
    corners.sort(key=lambda c: c["start"])
    for number, c in enumerate(corners, 1):
        c["number"] = number
    return corners


def _split_complex(idx, k, ratio=0.4, window=10):
    """Split a same-direction region at deep curvature minima between peaks."""
    m = len(idx)
    if m < 2 * window + 3:
        return [idx]
    vals = [abs(k[i]) for i in idx]
    smooth = [sum(vals[max(0, j - 2):j + 3]) / len(vals[max(0, j - 2):j + 3]) for j in range(m)]
    peaks = [
        j
        for j in range(m)
        if smooth[j] == max(smooth[max(0, j - window):j + window + 1]) and smooth[j] > ENTER_CURVATURE
    ]
    cuts = []
    for a, b in zip(peaks, peaks[1:]):
        if b - a < 3:
            continue
        j = min(range(a + 1, b), key=lambda q: smooth[q])
        if smooth[j] < ratio * min(smooth[a], smooth[b]):
            cuts.append(j)
    if not cuts:
        return [idx]
    parts = []
    start = 0
    for j in cuts:
        parts.append(idx[start:j])
        start = j
    parts.append(idx[start:])
    return [p for p in parts if p]


def find_straights(table, corners, min_length=60.0):
    n = len(table["x"])
    ds = table["spacing"]
    if not corners:
        return [{"start": 0.0, "end": round(n * ds, 2), "length": round(n * ds, 2)}]
    out = []
    for i, c in enumerate(corners):
        nxt = corners[(i + 1) % len(corners)]
        start = c["end_index"]
        end = nxt["start_index"]
        length = ((end - start) % n) * ds
        if length >= min_length:
            out.append(
                {
                    "start": round(start * ds, 2),
                    "end": round(end * ds, 2),
                    "length": round(length, 2),
                    "after_corner": c["number"],
                }
            )
    return out


def _class(value, edges):
    for i, e in enumerate(edges):
        if value < e:
            return i
    return len(edges)


def physical_segments(table, layout):
    """Group samples by (curvature, grade, bank, surface) classes."""
    n = len(table["x"])
    ds = table["spacing"]
    keys = []
    for i in range(n):
        keys.append(
            (
                _class(abs(table["curvature"][i]), (1 / 400, 1 / 120, 1 / 40)),
                _class(table["grade"][i], (-0.06, -0.02, 0.02, 0.06)),
                _class(math.degrees(table["bank"][i]), (-4.0, -1.0, 1.0, 4.0)),
                layout["kerb_left"][i] > 0 or layout["kerb_right"][i] > 0,
            )
        )
    segments = []
    start = 0
    for i in range(1, n + 1):
        if i == n or keys[i] != keys[start]:
            key = keys[start]
            segments.append(
                {
                    "start": round(start * ds, 2),
                    "end": round(i * ds, 2),
                    "curvature_class": ("reta", "rápida", "média", "lenta")[key[0]],
                    "grade_class": ("descida forte", "descida", "plano", "subida", "subida forte")[key[1]],
                    "bank_class": ("inclinada à direita", "leve à direita", "plana", "leve à esquerda", "inclinada à esquerda")[key[2]],
                    "kerbs": key[3],
                }
            )
            start = i
    return segments


def trackside_layout(circuit, table, corners, clearance_left, clearance_right, corner_speed=None):
    """Kerbs, run-off material and barrier offsets per physics sample."""
    n = len(table["x"])
    ds = table["spacing"]
    half = 0.5 * circuit["width"]
    runoff_type = circuit["runoff"]["type"]
    runoff_width = circuit["runoff"]["width"]
    kerb_left = [0.0] * n
    kerb_right = [0.0] * n
    outside_fast = [0] * n  # +1 outside on the left, -1 outside on the right
    for c in corners:
        span = c["length"]
        apex = c["apex_index"]
        inner_left = c["direction"] == "left"
        inside_half = max(6.0, 0.35 * span)
        a0 = int(-inside_half / ds)
        a1 = int(inside_half / ds)
        for d in range(a0, a1 + 1):
            i = (apex + d) % n
            if inner_left:
                kerb_left[i] = KERB_WIDTH
            else:
                kerb_right[i] = KERB_WIDTH
        # outside kerb from apex to 15 m after the corner end
        end = c["end_index"]
        length = ((end - apex) % n) + int(15.0 / ds)
        for d in range(length):
            i = (apex + d) % n
            if inner_left:
                kerb_right[i] = KERB_WIDTH
            else:
                kerb_left[i] = KERB_WIDTH
        fast = c["min_radius"] > 60.0
        if fast:
            for d in range(((c["end_index"] - c["start_index"]) % n) + int(30.0 / ds)):
                i = (c["start_index"] + d) % n
                outside_fast[i] = -1 if inner_left else 1
    runoff_left = [SURFACE_GRASS] * n
    runoff_right = [SURFACE_GRASS] * n
    barrier_left = [0.0] * n
    barrier_right = [0.0] * n
    for i in range(n):
        for side in (1, -1):
            if runoff_type == "grass":
                material = SURFACE_GRASS
            elif runoff_type == "gravel":
                material = SURFACE_GRAVEL
            elif runoff_type == "asphalt":
                material = SURFACE_RUNOFF_ASPHALT
            else:  # mixed: gravel on the outside of fast corners, asphalt on other outsides
                if outside_fast[i] == side:
                    material = SURFACE_GRAVEL
                else:
                    material = SURFACE_GRASS
            extra = 4.0 if outside_fast[i] == side else 0.0
            clearance = clearance_left[i] if side > 0 else clearance_right[i]
            limit = 0.5 * clearance - 0.5
            barrier = half + runoff_width + extra
            if barrier > limit:
                barrier = max(half + 1.0, limit)
            if side > 0:
                runoff_left[i] = material
                barrier_left[i] = barrier
            else:
                runoff_right[i] = material
                barrier_right[i] = barrier
    # smooth barrier offsets (avoid saw-tooth from the clearance clamp)
    for arr in (barrier_left, barrier_right):
        smoothed = list(arr)
        for _ in range(3):
            smoothed = [
                min(arr[i], (smoothed[i - 1] + smoothed[i] + smoothed[(i + 1) % n]) / 3.0) for i in range(n)
            ]
        arr[:] = smoothed
    return {
        "kerb_left": kerb_left,
        "kerb_right": kerb_right,
        "runoff_left": runoff_left,
        "runoff_right": runoff_right,
        "barrier_left": barrier_left,
        "barrier_right": barrier_right,
    }

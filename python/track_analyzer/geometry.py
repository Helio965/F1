"""Derived geometry: uniform physics table, adaptive samples, clearances.

* Curvature is analytic from the spline derivatives (signed, ISO: positive
  turns left). With heading psi = atan2(x', z'):
      kappa = (z' x'' - x' z'') / (x'^2 + z'^2)^(3/2)
* Grade = dy/dh (rise over horizontal run); vertical curvature is its
  derivative along the lap.
* Adaptive sampling chooses the spacing from the chord sagitta tolerance
  ds = sqrt(8 tol / |kappa|) (bounded), so tight corners get dense samples and
  straights sparse ones.
* Clearance to other (non-local) parts of the track uses a spatial hash grid,
  O(N) on average instead of O(N^2).
"""

import math

from .spline import ClosedCatmullRom

PHYSICS_SPACING = 2.0  # m, target spacing of the uniform physics table
VALIDATION_DIVISIONS = 768  # same sampling as the legacy editor validation


def build_spline(circuit):
    points = [(p["x"], p["y"], p["z"]) for p in circuit["points"]]
    banks = [math.radians(float(p.get("bank", 0.0))) for p in circuit["points"]]
    return ClosedCatmullRom(points, extra=[banks])


def frame_at(spline, t):
    """Point, unit planar tangent, signed curvature, grade and bank at parameter t."""
    x, y, z = spline.point(t)
    dx, dy, dz, ddx, ddy, ddz = spline.derivatives(t)
    h2 = dx * dx + dz * dz
    h = math.sqrt(h2) if h2 > 1e-18 else 1e-9
    curvature = (dz * ddx - dx * ddz) / (h2 * h) if h2 > 1e-18 else 0.0
    grade = dy / h
    bank = spline.channel(t, 0)
    return x, y, z, dx / h, dz / h, curvature, grade, bank


def uniform_table(spline, spacing=PHYSICS_SPACING):
    """Uniform arc-length table used by the runtime physics."""
    length = spline.length
    n = max(150, int(round(length / spacing)))
    ds = length / n
    table = {
        "length": length,
        "spacing": ds,
        "x": [],
        "y": [],
        "z": [],
        "tx": [],
        "tz": [],
        "curvature": [],
        "grade": [],
        "bank": [],
        "t": [],
    }
    for i in range(n):
        t = spline.u_to_t(i / n)
        x, y, z, tx, tz, k, g, b = frame_at(spline, t)
        table["x"].append(x)
        table["y"].append(y)
        table["z"].append(z)
        table["tx"].append(tx)
        table["tz"].append(tz)
        table["curvature"].append(k)
        table["grade"].append(g)
        table["bank"].append(b)
        table["t"].append(t)
    grade = table["grade"]
    table["vertical_curvature"] = [
        (grade[(i + 1) % n] - grade[i - 1]) / (2.0 * ds) for i in range(n)
    ]
    return table


def adaptive_samples(spline, tolerance=0.02, min_spacing=0.75, max_spacing=15.0):
    """Arc-length samples whose spacing follows local curvature.

    Returns a list of dicts with s, x, y, z, curvature, grade, bank.
    """
    length = spline.length
    samples = []
    s = 0.0
    while s < length - 1e-6:
        t = spline.u_to_t(s / length)
        x, y, z, tx, tz, k, g, b = frame_at(spline, t)
        samples.append({"s": s, "x": x, "y": y, "z": z, "curvature": k, "grade": g, "bank": b})
        ak = abs(k)
        step = math.sqrt(8.0 * tolerance / ak) if ak > 1e-9 else max_spacing
        if step < min_spacing:
            step = min_spacing
        elif step > max_spacing:
            step = max_spacing
        # look ahead: do not step over a sharper region
        t2 = spline.u_to_t(min(length, s + step) / length)
        k2 = abs(frame_at(spline, t2)[5])
        if k2 > ak * 1.5 and k2 > 1e-9:
            step = max(min_spacing, min(step, math.sqrt(8.0 * tolerance / k2)))
        s += step
    return samples


def validation_line(spline):
    """Spaced points matching the legacy 768-sample editor validation."""
    pts = spline.spaced_points(VALIDATION_DIVISIONS)
    out = []
    dist = 0.0
    for i, p in enumerate(pts):
        if i:
            q = pts[i - 1]
            dist += math.sqrt((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2)
        out.append((p[0], p[1], p[2], dist))
    return out


def clearances(table, local_window, radius=80.0):
    """Distance from each sample to the nearest non-local sample, per side.

    Only samples within about ``radius`` metres are searched (spatial hash);
    returns (left, right) lists with ``inf`` when nothing is that close.
    Callers that cap barrier offsets with the clearance must pass a radius of
    at least twice the largest offset they may place.
    """
    xs, zs = table["x"], table["z"]
    txs, tzs = table["tx"], table["tz"]
    n = len(xs)
    ds = table["spacing"]
    length = table["length"]
    cell = 40.0
    grid = {}
    for i in range(n):
        key = (int(math.floor(xs[i] / cell)), int(math.floor(zs[i] / cell)))
        grid.setdefault(key, []).append(i)
    left = [float("inf")] * n
    right = [float("inf")] * n
    reach = max(2, int(math.ceil(radius / cell)))
    for i in range(n):
        cx = int(math.floor(xs[i] / cell))
        cz = int(math.floor(zs[i] / cell))
        lx, lz = tzs[i], -txs[i]
        best_l = best_r = float("inf")
        for gx in range(cx - reach, cx + reach + 1):
            for gz in range(cz - reach, cz + reach + 1):
                bucket = grid.get((gx, gz))
                if not bucket:
                    continue
                for j in bucket:
                    along = abs(j - i) * ds
                    along = min(along, length - along)
                    if along < local_window:
                        continue
                    dx = xs[j] - xs[i]
                    dz = zs[j] - zs[i]
                    d = math.sqrt(dx * dx + dz * dz)
                    if dx * lx + dz * lz >= 0.0:
                        if d < best_l:
                            best_l = d
                    elif d < best_r:
                        best_r = d
        left[i] = best_l
        right[i] = best_r
    return left, right


def segments_cross(a, b, c, d):
    def cross(p, q, r):
        return (q[0] - p[0]) * (r[2] - p[2]) - (q[2] - p[2]) * (r[0] - p[0])

    ab_c = cross(a, b, c)
    ab_d = cross(a, b, d)
    cd_a = cross(c, d, a)
    cd_b = cross(c, d, b)
    return ab_c * ab_d < -1e-8 and cd_a * cd_b < -1e-8

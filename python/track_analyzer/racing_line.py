"""Initial racing line: minimum-curvature path inside the track limits.

Path nodes P_i = C_i + n_i L_i (centre line C, left normal L, offset n).
Objective: minimise sum |P_{i-1} - 2 P_i + P_{i+1}|^2 (discrete curvature for
uniform spacing). Coordinate descent: the unconstrained minimiser for node i
with its neighbours fixed is the biharmonic stencil

    P_i* = (4 (P_{i-1} + P_{i+1}) - (P_{i-2} + P_{i+2})) / 6,

projected on the node's normal line and clamped to the limits
[-half + margin, half - margin]. Multigrid (coarse to fine) speeds up the
slow convergence of the biharmonic problem; the result is deterministic.
This is a classical starting line (not a lap-time optimum).
"""

import math

CAR_MARGIN = 1.35  # m from the asphalt edge (half car width + safety)


def _resample(table, spacing):
    n_table = len(table["x"])
    length = table["length"]
    n = max(24, int(round(length / spacing)))
    step = n_table / n
    idx = [int(round(k * step)) % n_table for k in range(n)]
    return idx


def _solve(cx, cz, lx, lz, lo, hi, offsets, iterations, tol=1e-4):
    n = len(cx)
    for _ in range(iterations):
        change = 0.0
        for i in range(n):
            im2 = (i - 2) % n
            im1 = (i - 1) % n
            ip1 = (i + 1) % n
            ip2 = (i + 2) % n
            px = (
                4.0 * (cx[im1] + offsets[im1] * lx[im1] + cx[ip1] + offsets[ip1] * lx[ip1])
                - (cx[im2] + offsets[im2] * lx[im2] + cx[ip2] + offsets[ip2] * lx[ip2])
            ) / 6.0
            pz = (
                4.0 * (cz[im1] + offsets[im1] * lz[im1] + cz[ip1] + offsets[ip1] * lz[ip1])
                - (cz[im2] + offsets[im2] * lz[im2] + cz[ip2] + offsets[ip2] * lz[ip2])
            ) / 6.0
            target = (px - cx[i]) * lx[i] + (pz - cz[i]) * lz[i]
            new = offsets[i] + 1.6 * (target - offsets[i])  # over-relaxation
            if new < lo[i]:
                new = lo[i]
            elif new > hi[i]:
                new = hi[i]
            d = abs(new - offsets[i])
            if d > change:
                change = d
            offsets[i] = new
        if change < tol:
            break
    return offsets


def racing_line(table, half_width, levels=(32.0, 16.0, 8.0, 4.0), iterations=(400, 300, 200, 150)):
    """Return dict with node centre indices, offsets and per-table-sample offsets."""
    n_table = len(table["x"])
    txs, tzs = table["tx"], table["tz"]
    xs = table["x"]
    zs = table["z"]
    offsets_prev = None
    idx_prev = None
    for spacing, iters in zip(levels, iterations):
        idx = _resample(table, spacing)
        cx = [xs[i] for i in idx]
        cz = [zs[i] for i in idx]
        lx = [tzs[i] for i in idx]
        lz = [-txs[i] for i in idx]
        lo = [-half_width[i] + CAR_MARGIN for i in idx]
        hi = [half_width[i] - CAR_MARGIN for i in idx]
        if offsets_prev is None:
            offsets = [0.0] * len(idx)
        else:
            offsets = [_interp_offsets(idx_prev, offsets_prev, i, n_table) for i in idx]
        offsets = _solve(cx, cz, lx, lz, lo, hi, offsets, iters)
        offsets_prev, idx_prev = offsets, idx
    per_sample = [_interp_offsets(idx_prev, offsets_prev, i, n_table) for i in range(n_table)]
    # light smoothing (removes node-scale ripples before differentiation)
    for _ in range(2):
        per_sample = [
            (per_sample[i - 1] + 2.0 * per_sample[i] + per_sample[(i + 1) % n_table]) / 4.0
            for i in range(n_table)
        ]
    return {"node_index": idx_prev, "node_offset": offsets_prev, "offset": per_sample}


def _interp_offsets(idx, offsets, i, n_table):
    """Periodic linear interpolation of node offsets at table index i."""
    m = len(idx)
    # nodes are increasing modulo n_table
    lo = 0
    hi = m - 1
    if i < idx[0] or i >= idx[-1]:
        a, b = m - 1, 0
        span = (idx[0] - idx[-1]) % n_table
        t = ((i - idx[-1]) % n_table) / span if span else 0.0
        return offsets[a] + (offsets[b] - offsets[a]) * t
    while hi - lo > 1:
        mid = (lo + hi) >> 1
        if idx[mid] <= i:
            lo = mid
        else:
            hi = mid
    span = idx[hi] - idx[lo]
    t = (i - idx[lo]) / span if span else 0.0
    return offsets[lo] + (offsets[hi] - offsets[lo]) * t


def line_geometry(table, offsets):
    """World points, arc length and signed curvature of the offset path."""
    n = len(table["x"])
    px = [table["x"][i] + offsets[i] * table["tz"][i] for i in range(n)]
    pz = [table["z"][i] - offsets[i] * table["tx"][i] for i in range(n)]
    ds = []
    for i in range(n):
        j = (i + 1) % n
        ds.append(math.hypot(px[j] - px[i], pz[j] - pz[i]))
    curvature = []
    for i in range(n):
        a = (px[i - 1], pz[i - 1])
        b = (px[i], pz[i])
        c = (px[(i + 1) % n], pz[(i + 1) % n])
        curvature.append(_three_point_curvature(a, b, c))
    # smooth curvature over +/- 2 samples
    smooth = [
        (curvature[i - 2] + 2 * curvature[i - 1] + 3 * curvature[i] + 2 * curvature[(i + 1) % n] + curvature[(i + 2) % n])
        / 9.0
        for i in range(n)
    ]
    return px, pz, ds, smooth


def _three_point_curvature(a, b, c):
    """Signed curvature (ISO, + left) of the circle through three points."""
    abx, abz = b[0] - a[0], b[1] - a[1]
    bcx, bcz = c[0] - b[0], c[1] - b[1]
    acx, acz = c[0] - a[0], c[1] - a[1]
    cross = abz * bcx - abx * bcz
    denom = math.hypot(abx, abz) * math.hypot(bcx, bcz) * math.hypot(acx, acz)
    if denom < 1e-12:
        return 0.0
    return 2.0 * cross / denom

"""Reference speed profiles per vehicle and condition (QSS, forward/backward).

Nodes are placed adaptively along the racing line: spacing
ds = clamp(sqrt(8 tol / |kappa|), 2 m, 12 m), dense in corners and sparse on
straights. The profile uses :class:`physics_engine.limits.PerformanceEnvelope`
(the same tyre/aero/engine models as the dynamic simulation) and is then
interpolated back to every physics sample. Braking and acceleration zones
are contiguous regions limited by the backward and forward passes.
"""

import math

from physics_engine.limits import PerformanceEnvelope, speed_profile


def adaptive_nodes(line_ds, line_curvature, tolerance=0.02, min_step=2.0, max_step=12.0):
    n = len(line_ds)
    nodes = [0]
    i = 0
    while True:
        k = abs(line_curvature[i])
        step = math.sqrt(8.0 * tolerance / k) if k > 1e-9 else max_step
        step = min(max(step, min_step), max_step)
        travelled = 0.0
        j = i
        while travelled < step and j < n:
            travelled += line_ds[j % n]
            j += 1
            if abs(line_curvature[j % n]) > 1.5 * k + 1e-4:
                break
        if j >= n:
            break
        nodes.append(j)
        i = j
    return nodes


def compute_profile(table, line, params, compound, atmosphere, mu_scale=None, grip_scale=1.0, use_ers=True):
    """Speed profile on the racing line mapped to every physics sample."""
    n = len(table["x"])
    ds_line = line["ds"]
    curv = line["curvature"]
    nodes = adaptive_nodes(ds_line, curv)
    m = len(nodes)
    node_ds = []
    for a in range(m):
        i0 = nodes[a]
        i1 = nodes[(a + 1) % m] if a + 1 < m else nodes[0] + n
        node_ds.append(sum(ds_line[k % n] for k in range(i0, i1)))
    k_n = [curv[i] for i in nodes]
    b_n = [table["bank"][i] for i in nodes]
    g_n = [table["grade"][i] for i in nodes]
    v_n = [table["vertical_curvature"][i] for i in nodes]
    mu_n = [1.0 if mu_scale is None else mu_scale[i] for i in nodes]
    env = PerformanceEnvelope(params, compound, atmosphere, grip_scale=grip_scale, use_ers=use_ers)
    v, v_corner, acc_lim, brk_lim = speed_profile(env, node_ds, k_n, b_n, g_n, v_n, mu_n)
    # interpolate back to samples
    speed = [0.0] * n
    braking = [False] * n
    accelerating = [False] * n
    for a in range(m):
        i0 = nodes[a]
        i1 = nodes[(a + 1) % m] if a + 1 < m else nodes[0] + n
        span = i1 - i0
        for k in range(i0, i1):
            t = (k - i0) / span if span else 0.0
            speed[k % n] = v[a] + (v[(a + 1) % m] - v[a]) * t
            braking[k % n] = brk_lim[a]
            accelerating[k % n] = acc_lim[a] and not brk_lim[a]
    lap_time = 0.0
    for i in range(n):
        vm = 0.5 * (speed[i] + speed[(i + 1) % n])
        lap_time += ds_line[i] / max(vm, 0.5)
    return {
        "speed": speed,
        "braking": braking,
        "accelerating": accelerating,
        "lap_time": lap_time,
        "top_speed": max(speed),
        "min_speed": min(speed),
        "nodes": len(nodes),
        "envelope_top_speed": env.top_speed,
        "accel_fn": env,
    }


def zones(table, profile, corners):
    """Contiguous braking / acceleration zones with entry and exit speeds."""
    n = len(table["x"])
    ds = table["spacing"]
    out = {"braking": [], "acceleration": []}
    for key, flags in (("braking", profile["braking"]), ("acceleration", profile["accelerating"])):
        start = None
        first_false = next((i for i in range(n) if not flags[i]), None)
        if first_false is None:
            continue
        for step in range(1, n + 1):
            i = (first_false + step) % n
            if flags[i] and start is None:
                start = i
            elif not flags[i] and start is not None:
                end = (i - 1) % n
                length = ((end - start) % n + 1) * ds
                if length >= 8.0:
                    v0 = profile["speed"][start]
                    v1 = profile["speed"][end]
                    zone = {
                        "start": round(start * ds, 1),
                        "end": round(end * ds, 1),
                        "length": round(length, 1),
                        "entry_speed_kmh": round(v0 * 3.6, 1),
                        "exit_speed_kmh": round(v1 * 3.6, 1),
                    }
                    if key == "braking":
                        zone["mean_decel_g"] = round((v0 * v0 - v1 * v1) / (2.0 * length) / 9.80665, 2)
                        zone["corner"] = _corner_after(corners, end * ds, n * ds)
                    out[key].append(zone)
                start = None
    return out


def _corner_after(corners, s, length):
    """Number of the first corner whose apex lies at or after ``s`` (with 30 m tolerance)."""
    best = None
    best_d = float("inf")
    for c in corners:
        d = (c["apex"] - s + 30.0) % length
        if d < best_d:
            best_d = d
            best = c["number"]
    return best

"""Path and speed planning shared by the AI drivers of a session.

* Path: the analyser's minimum-curvature racing line (lateral offset per
  physics sample), recomputed here if the analysis did not include it.
* Speed: QSS forward/backward profiles (same physics models as the cars) for
  a ladder of grip levels. At run time each sample's grip level is estimated
  from the *observed* water film on the racing line and the car's compound;
  the target speed is interpolated between the two neighbouring profiles.
  Pre-computing the ladder at session start avoids recomputation stalls when
  the weather changes, while still reacting to rain, drying and puddles.
"""

import math

from physics_engine.limits import PerformanceEnvelope, speed_profile
from physics_engine.tyre import TyreModel, aquaplaning_speed
from physics_engine.vehicles import COMPOUNDS
from track_analyzer.profiles import adaptive_nodes
from track_analyzer.racing_line import line_geometry, racing_line

GRIP_LADDER = (0.35, 0.5, 0.65, 0.8, 0.9, 1.0, 1.06)


class SpeedPlan:
    def __init__(self, track, params, compound, atmosphere, racing_offsets=None, ladder=GRIP_LADDER):
        self.track = track
        self.params = params
        self.compound = compound
        n = track.n
        table = {
            "x": track.xs,
            "z": track.zs,
            "tx": track.txs,
            "tz": track.tzs,
            "length": track.length,
            "spacing": track.ds,
        }
        if racing_offsets is None or len(racing_offsets) != n:
            racing_offsets = racing_line(table, track.half_width)["offset"]
        self.offsets = list(racing_offsets)
        _, _, line_ds, line_k = line_geometry(table, self.offsets)
        self.line_ds = line_ds
        self.line_k = line_k
        nodes = adaptive_nodes(line_ds, line_k)
        m = len(nodes)
        node_ds = []
        for a in range(m):
            i0 = nodes[a]
            i1 = nodes[a + 1] if a + 1 < m else nodes[0] + n
            node_ds.append(sum(line_ds[k % n] for k in range(i0, i1)))
        k_n = [line_k[i] for i in nodes]
        b_n = [track.bank[i] for i in nodes]
        g_n = [track.grade[i] for i in nodes]
        v_n = [track.vcurv[i] for i in nodes]
        self.ladder = ladder
        self.profiles = []
        self.envelopes = []
        for level in ladder:
            env = PerformanceEnvelope(params, compound, atmosphere, grip_scale=level)
            v, _, _, _ = speed_profile(env, node_ds, k_n, b_n, g_n, v_n, [1.0] * m)
            self.profiles.append(_to_samples(nodes, v, n))
            self.envelopes.append(env)
        self.grip_estimate = [1.0] * n
        self.tyre = TyreModel(COMPOUNDS[compound])

    def offset(self, s):
        tr = self.track
        f = (s % tr.length) / tr.ds
        i = int(f) % tr.n
        t = f - int(f)
        j = (i + 1) % tr.n
        return self.offsets[i] + (self.offsets[j] - self.offsets[i]) * t

    def target_speed(self, index, grip):
        """Interpolate the profile ladder at a grip level (fraction of dry grip)."""
        ladder = self.ladder
        if grip <= ladder[0]:
            return self.profiles[0][index] * math.sqrt(max(grip, 0.05) / ladder[0])
        for k in range(1, len(ladder)):
            if grip <= ladder[k]:
                t = (grip - ladder[k - 1]) / (ladder[k] - ladder[k - 1])
                return self.profiles[k - 1][index] + (self.profiles[k][index] - self.profiles[k - 1][index]) * t
        return self.profiles[-1][index]

    def brake_capability(self, speed, grip):
        """Approximate straight-line deceleration at a grip level (m/s^2)."""
        k = min(range(len(self.ladder)), key=lambda q: abs(self.ladder[q] - grip))
        return self.envelopes[k].deceleration(speed, 0.0, 0.0, 0.0, 0.0)

    def refresh_grip(self, surface, pressure_kpa):
        """Observed wet-grip ratio along the racing line (called a few times per second)."""
        tr = self.track
        tyre = self.tyre
        width = self.params.tyre_width_rear
        onset = aquaplaning_speed(pressure_kpa)
        n = tr.n
        estimate = self.grip_estimate
        lanes = surface.lanes
        for k in range(surface.segments):
            i = surface.seg_index[k]
            half = tr.half_width[i]
            lane = int((self.offsets[i] + half) / (2.0 * half) * lanes)
            lane = min(lanes - 1, max(0, lane))
            film = surface.water[k * lanes + lane]
            speed = self.profiles[-2][i]
            factor, _ = tyre.water_factor(film, 0.7 * tr.roughness[i], min(speed, onset * 1.2), width, pressure_kpa)
            per = n // surface.segments + 1
            for d in range(-per // 2, per // 2 + 1):
                estimate[(i + d) % n] = factor


def _to_samples(nodes, v, n):
    out = [0.0] * n
    m = len(nodes)
    for a in range(m):
        i0 = nodes[a]
        i1 = nodes[a + 1] if a + 1 < m else nodes[0] + n
        span = i1 - i0
        va = v[a]
        vb = v[(a + 1) % m]
        for k in range(i0, i1):
            t = (k - i0) / span if span else 0.0
            out[k % n] = va + (vb - va) * t
    return out

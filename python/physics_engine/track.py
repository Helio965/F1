"""Runtime track model consumed by the physics.

The track analyser (``track_analyzer``) produces uniformly re-sampled
centre-line data; this module turns it into fast queries:

* projection of a world point onto the centre line (s, n) with a local
  hill-climb from the previous index (O(1) per car per step) and a global
  fallback;
* surface geometry at (s, n): height, cross slope (banking), grade and
  vertical curvature;
* surface material at (s, n): asphalt, kerb, grass, gravel or asphalt
  run-off, with grip and rolling-resistance multipliers;
* a spatial grid (segments x lanes) with water-film depth, rubber and track
  temperature (see :class:`SurfaceState`).

Lateral coordinate n > 0 is to the left of the travel direction.
"""

import math

from .constants import GRAVITY
from .environment import saturation_vapour_pressure

SURFACE_ASPHALT = 0
SURFACE_KERB = 1
SURFACE_GRASS = 2
SURFACE_GRAVEL = 3
SURFACE_RUNOFF_ASPHALT = 4
SURFACE_NAMES = ("asphalt", "kerb", "grass", "gravel", "runoff-asphalt")

# (grip multiplier dry, extra multiplier when wet, rolling resistance factor,
#  speed-proportional drag coefficient N per (m/s) per N of load)
SURFACE_PROPERTIES = (
    (1.00, 1.00, 1.0, 0.0),
    (0.92, 0.78, 1.3, 0.0),
    (0.55, 0.62, 6.0, 0.0016),
    (0.50, 0.85, 22.0, 0.0045),
    (0.95, 1.00, 1.1, 0.0),
)

MANNING_ASPHALT = 0.015  # s/m^(1/3), typical range 0.013-0.016 (FHWA HEC-22)
WATER_UPDATE_INTERVAL = 0.25  # s between updates of the same cell


class TrackModel:
    """Immutable geometric description plus mutable :class:`SurfaceState`."""

    def __init__(self, data):
        self.length = float(data["length"])
        self.ds = float(data["spacing"])
        self.xs = list(data["x"])
        self.ys = list(data["y"])
        self.zs = list(data["z"])
        self.txs = list(data["tx"])
        self.tzs = list(data["tz"])
        self.curvature = list(data["curvature"])
        self.bank = list(data["bank"])
        self.grade = list(data["grade"])
        self.vcurv = list(data["vertical_curvature"])
        self.half_width = list(data["half_width"])
        self.barrier_left = list(data["barrier_left"])
        self.barrier_right = list(data["barrier_right"])
        self.kerb_left = list(data["kerb_left"])
        self.kerb_right = list(data["kerb_right"])
        self.runoff_left = list(data["runoff_left"])
        self.runoff_right = list(data["runoff_right"])
        self.grip = list(data["grip"])
        self.roughness = list(data["roughness"])
        self.exposure = list(data["exposure"])
        self.n = len(self.xs)
        self.headings = [math.atan2(tx, tz) for tx, tz in zip(self.txs, self.tzs)]
        self.max_barrier = max(max(self.barrier_left), max(self.barrier_right))
        self.surface = SurfaceState(self, data)

    # ------------------------------------------------------------------ queries
    def wrap_s(self, s):
        return s % self.length

    def index_at(self, s):
        return int((s % self.length) / self.ds) % self.n

    def project(self, x, z, hint):
        """Closest centre-line location. Returns (index, t, s, n, dist2)."""
        xs, zs, n = self.xs, self.zs, self.n
        i = hint % n
        dx = x - xs[i]
        dz = z - zs[i]
        best = dx * dx + dz * dz
        # Hill-climb in both directions.
        while True:
            j = i + 1 if i + 1 < n else 0
            dx = x - xs[j]
            dz = z - zs[j]
            d = dx * dx + dz * dz
            if d < best:
                i, best = j, d
                continue
            j = i - 1 if i > 0 else n - 1
            dx = x - xs[j]
            dz = z - zs[j]
            d = dx * dx + dz * dz
            if d < best:
                i, best = j, d
                continue
            break
        limit = self.max_barrier + 12.0
        if best > limit * limit:
            i = self._global_nearest(x, z)
        return self._refine(x, z, i)

    def _global_nearest(self, x, z):
        xs, zs = self.xs, self.zs
        best_i = 0
        best = float("inf")
        for i in range(self.n):
            dx = x - xs[i]
            dz = z - zs[i]
            d = dx * dx + dz * dz
            if d < best:
                best, best_i = d, i
        return best_i

    def _refine(self, x, z, i):
        xs, zs, n = self.xs, self.zs, self.n
        best = None
        for a in (i - 1 if i > 0 else n - 1, i):
            b = a + 1 if a + 1 < n else 0
            ax, az = xs[a], zs[a]
            sx = xs[b] - ax
            sz = zs[b] - az
            seg2 = sx * sx + sz * sz
            if seg2 < 1e-12:
                continue
            t = ((x - ax) * sx + (z - az) * sz) / seg2
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            px = ax + sx * t
            pz = az + sz * t
            dx = x - px
            dz = z - pz
            d2 = dx * dx + dz * dz
            if best is None or d2 < best[4]:
                inv = 1.0 / math.sqrt(seg2)
                lateral = dx * (sz * inv) - dz * (sx * inv)
                s = (a * self.ds + t * self.ds) % self.length
                best = (a, t, s, lateral, d2)
        return best

    def sample(self, index, t, values):
        b = index + 1 if index + 1 < self.n else 0
        return values[index] + (values[b] - values[index]) * t

    def heading_at(self, index, t):
        b = index + 1 if index + 1 < self.n else 0
        h0 = self.headings[index]
        dh = math.atan2(math.sin(self.headings[b] - h0), math.cos(self.headings[b] - h0))
        return h0 + dh * t

    def height(self, index, t, lateral):
        y = self.sample(index, t, self.ys)
        return y + lateral * math.sin(self.sample(index, t, self.bank))

    def surface_type(self, index, lateral):
        half = self.half_width[index]
        if lateral >= 0.0:
            edge = lateral - half
            kerb = self.kerb_left[index]
            runoff = self.runoff_left[index]
        else:
            edge = -lateral - half
            kerb = self.kerb_right[index]
            runoff = self.runoff_right[index]
        if edge <= 0.0:
            return SURFACE_ASPHALT
        if edge <= kerb:
            return SURFACE_KERB
        return runoff

    def world_point(self, s, lateral):
        """World (x, y, z) of a track coordinate (for spawning and AI targets)."""
        s = s % self.length
        f = s / self.ds
        i = int(f) % self.n
        t = f - int(f)
        b = i + 1 if i + 1 < self.n else 0
        x = self.xs[i] + (self.xs[b] - self.xs[i]) * t
        z = self.zs[i] + (self.zs[b] - self.zs[i]) * t
        tx = self.txs[i] + (self.txs[b] - self.txs[i]) * t
        tz = self.tzs[i] + (self.tzs[b] - self.tzs[i]) * t
        norm = math.hypot(tx, tz) or 1.0
        tx /= norm
        tz /= norm
        y = self.height(i, t, lateral)
        return x + tz * lateral, y, z - tx * lateral, math.atan2(tx, tz)


class SurfaceState:
    """Spatial grid of water film (mm), rubber (0..1) and track temperature.

    Cells: ``segments`` along the lap (about 10 m each) x ``lanes`` across the
    asphalt width. Per cell and per update:

        dh/dt = I e - E - q_out + q_in - traffic

    * rain I (mm/s) times exposure e;
    * evaporation E (Dalton-type): E = c_e (1 + 0.5 w)(e_s(T_track) - RH e_s(T_air));
    * sheet flow (Manning, kinematic wave): v = h^(2/3) S^(1/2) / n_M, moving
      water toward the lower lane (cross slope) and the downhill segment
      (grade); the low-side edge lane drains off the asphalt. Local
      elevation minima therefore collect puddles;
    * traffic: each tyre passage expels a share of the film it sweeps,
      dh = -eta h w V dt / A_cell, so the racing line dries first.
    Rubber is laid by sliding energy and washed by rain; it raises dry grip
    slightly and lowers wet grip.
    """

    LANES = 6

    def __init__(self, track, data):
        self.track = track
        length = track.length
        segments = max(8, int(round(length / 10.0)))
        self.segments = segments
        self.seg_length = length / segments
        lanes = self.LANES
        self.lanes = lanes
        count = segments * lanes
        self.water = [0.0] * count
        self.rubber = [0.0] * count
        n = track.n
        per = n / segments
        self.seg_index = [min(n - 1, int((k + 0.5) * per)) for k in range(segments)]
        idx = self.seg_index
        self.seg_half = [track.half_width[i] for i in idx]
        self.seg_bank = [math.sin(track.bank[i]) for i in idx]
        self.seg_grade = [track.grade[i] for i in idx]
        self.seg_exposure = [track.exposure[i] for i in idx]
        self.seg_texture = [track.roughness[i] for i in idx]
        self.seg_height = [track.ys[i] for i in idx]
        self.track_temperature = [0.0] * segments
        self.cursor = 0
        self.timer = 0.0
        self.rain_rate = 0.0
        self.version = 0

    # --------------------------------------------------------------- lookups
    def cell(self, s, lateral, index):
        seg = int(s / self.seg_length)
        if seg >= self.segments:
            seg = self.segments - 1
        half = self.track.half_width[index]
        lane = int((lateral + half) / (2.0 * half) * self.lanes)
        if lane < 0:
            lane = 0
        elif lane >= self.lanes:
            lane = self.lanes - 1
        return seg * self.lanes + lane, seg

    def film(self, cell_index, lateral, half):
        """Water depth at a contact point; off-asphalt reuses the edge lane."""
        h = self.water[cell_index]
        if lateral > half or lateral < -half:
            return h * 0.6
        return h

    def traffic(self, cell_index, sweep_fraction, slide_energy):
        """Tyre passage: expel water and lay rubber in one cell."""
        if sweep_fraction > 0.0:
            h = self.water[cell_index]
            if h > 0.0:
                frac = 0.6 * sweep_fraction
                if frac > 0.5:
                    frac = 0.5
                self.water[cell_index] = h * (1.0 - frac)
        if slide_energy > 0.0:
            r = self.rubber[cell_index] + slide_energy * 2.0e-9
            self.rubber[cell_index] = r if r < 1.0 else 1.0

    # ---------------------------------------------------------- initial state
    def initialise(self, atmosphere, wetness):
        temp = atmosphere.track_temperature
        self.track_temperature = [temp] * self.segments
        if wetness <= 0.0:
            return
        # Equilibrium of a heavy shower (15 mm/h) then scaled: local sheet-flow
        # balance followed by a short spin-up so puddles form at sag points.
        rain = 15.0 / 3600.0
        lanes = self.lanes
        for k in range(self.segments):
            half = self.seg_half[k]
            lane_w = 2.0 * half / lanes
            slope = math.hypot(self.seg_bank[k], self.seg_grade[k])
            slope = slope if slope > 0.004 else 0.004
            for j in range(lanes):
                bank = self.seg_bank[k]
                upstream = (lanes - j) if bank >= 0.0 else (j + 1)
                flow_len = upstream * lane_w
                q = rain * self.seg_exposure[k] * flow_len / 1000.0  # m^2/s
                h_m = (q * MANNING_ASPHALT / math.sqrt(slope)) ** 0.6
                self.water[k * lanes + j] = 1000.0 * h_m
        saved = atmosphere.rain_rate_mm_h
        atmosphere.rain_rate_mm_h = 15.0
        for _ in range(40):
            self._update_range(0, self.segments, 2.0, atmosphere)
        atmosphere.rain_rate_mm_h = saved
        if wetness < 1.0:
            self.water = [h * wetness for h in self.water]
        self.version += 1

    # ------------------------------------------------------------ evolution
    def update(self, dt, atmosphere):
        """Incremental update: a slice of segments each physics step."""
        self.timer += dt
        per_step = max(1, int(math.ceil(self.segments * dt / WATER_UPDATE_INTERVAL)))
        start = self.cursor
        end = min(self.segments, start + per_step)
        self._update_range(start, end, WATER_UPDATE_INTERVAL, atmosphere)
        self.cursor = 0 if end >= self.segments else end
        if self.cursor == 0:
            self.version += 1

    def _update_range(self, start, end, dt, atmosphere):
        water = self.water
        rubber = self.rubber
        lanes = self.lanes
        segs = self.segments
        rain = atmosphere.rain_rate_mm_h / 3600.0
        e_air = atmosphere.relative_humidity * saturation_vapour_pressure(atmosphere.air_temperature)
        wind = 1.0 + 0.5 * atmosphere.wind_speed
        sun = (1.0 - atmosphere.cloud_cover) * 18.0
        target_base = atmosphere.air_temperature + sun
        inv_n = 1.0 / MANNING_ASPHALT
        seg_len = self.seg_length
        for k in range(start, end):
            half = self.seg_half[k]
            lane_w = 2.0 * half / lanes
            bank = self.seg_bank[k]
            grade = self.seg_grade[k]
            texture = self.seg_texture[k]
            exposure = self.seg_exposure[k]
            t_track = self.track_temperature[k]
            # Track temperature relaxes toward a weather-dependent target.
            wet_avg = 0.0
            base = k * lanes
            for j in range(lanes):
                wet_avg += water[base + j]
            wet_avg /= lanes
            target = target_base - 6.0 * (wet_avg / (wet_avg + 0.3)) - (4.0 if rain > 0.0 else 0.0)
            t_track += (target - t_track) * min(1.0, dt / 600.0)
            self.track_temperature[k] = t_track
            evap = 0.0
            dvp = saturation_vapour_pressure(t_track) - e_air
            if dvp > 0.0:
                evap = 0.6 * wind * (dvp / 1000.0) / 3600.0
            down_seg = (k + 1) % segs if grade < 0.0 else (k - 1) % segs
            s_lat = abs(bank)
            s_long = abs(grade)
            storage = 0.3 * texture
            for j in range(lanes):
                c = base + j
                h = water[c]
                h += (rain * exposure - (evap if h > 0.0 else 0.0)) * dt
                if h < 0.0:
                    h = 0.0
                flow = h - storage
                if flow > 0.0:
                    hm = flow / 1000.0
                    v_coef = inv_n * hm ** 0.6666667
                    if s_lat > 1e-4:
                        moved = min(flow * 0.45, flow * v_coef * math.sqrt(s_lat) * dt / lane_w)
                        h -= moved
                        low = j - 1 if bank > 0.0 else j + 1
                        if 0 <= low < lanes:
                            water[base + low] += moved
                    if s_long > 1e-4:
                        moved = min(flow * 0.45, flow * v_coef * math.sqrt(s_long) * dt / seg_len)
                        h -= moved
                        water[down_seg * lanes + j] += moved
                if rain > 0.0:
                    rubber[c] *= 1.0 - 0.002 * dt
                water[c] = h if h < 25.0 else 25.0

    def lane_grid(self):
        """Flat copy of water depths for rendering (segments x lanes)."""
        return list(self.water)

    def average_wetness(self, start_s=0.0, end_s=None):
        """Mean film depth (mm) across the asphalt over a lap range."""
        if end_s is None:
            return sum(self.water) / len(self.water)
        k0 = int((start_s % self.track.length) / self.seg_length)
        k1 = int((end_s % self.track.length) / self.seg_length)
        total = 0.0
        count = 0
        k = k0
        while True:
            base = k * self.lanes
            for j in range(self.lanes):
                total += self.water[base + j]
                count += 1
            if k == k1:
                break
            k = (k + 1) % self.segments
        return total / max(1, count)


def gravity_components(grade, bank, vertical_curvature, speed):
    """Track-frame gravity: along-track, lateral (left +) and effective normal g."""
    cos_g = 1.0 / math.sqrt(1.0 + grade * grade)
    sin_g = grade * cos_g
    sin_b = math.sin(bank)
    cos_b = math.cos(bank)
    g_t = -GRAVITY * sin_g
    g_l = -GRAVITY * sin_b * cos_g
    g_n = GRAVITY * cos_g * cos_b + speed * speed * vertical_curvature
    return g_t, g_l, g_n

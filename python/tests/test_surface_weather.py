"""Spatial water film, weather, rubber and track-temperature tests.

Covers :class:`physics_engine.track.SurfaceState` (segments x lanes grid of
water film, rubber and track temperature), its coupling to the weather presets
(:mod:`physics_engine.environment`), the tyre water model
(:meth:`physics_engine.tyre.TyreModel.water_factor`) as seen by a complete
:class:`physics_engine.vehicle.Vehicle`, and end-to-end behaviour in a
:class:`physics_engine.simulation.Simulation`.

Conventions used below (read from the code, not assumed):

* lane index ``j = int((n + half) / (2 half) * lanes)``: lane 0 is the right
  edge (n = -half), lane ``lanes - 1`` the left edge;
* bank > 0 raises the left side (``TrackModel.height = y + n sin(bank)``),
  so lane 0 is the low side and water flows toward ``j - 1``;
* evaporation is switched off in the "still air" atmosphere by RH = 1 and a
  track colder than the air (Dalton term e_s(T_track) - RH e_s(T_air) < 0).

``verification`` tests check the documented update equations exactly or a
self-consistency property; ``validation`` tests compare emergent behaviour
with public reference envelopes (plausibility ranges, not calibration).
"""

import math
from functools import lru_cache

import pytest

from conftest import circle_document
from physics_engine.assists import PlayerController
from physics_engine.environment import Atmosphere
from physics_engine.simulation import Environment, Simulation
from physics_engine.track import MANNING_ASPHALT, WATER_UPDATE_INTERVAL, TrackModel
from physics_engine.tyre import TyreModel, aquaplaning_speed
from physics_engine.vehicle import Vehicle
from physics_engine.vehicles import COMPOUNDS, VEHICLES
from track_analyzer import analyze

GE = VEHICLES["ground-effect-2022"]
DT = 1.0 / 120.0
RIGHT_LANE_TARGET = -1.67  # m: centre of lane 2 on a 20 m wide track (lanes are 3.33 m)


def _sag(angle):
    """Single smooth dip: 4 m high at angle 0, lowest (0 m) at angle pi; max grade 2 %."""
    return 2.0 * (1.0 + math.cos(angle))


@lru_cache(maxsize=None)
def _analysis(radius=100.0, bank_deg=0.0, exposure=1.0, sag=False, level="quick"):
    doc = circle_document(radius, bank_deg=bank_deg, elevation=_sag if sag else None)
    doc["environment"] = {"exposure": exposure}
    result = analyze(doc, level)
    assert result["valid"], result["errors"]
    return result


def _table(**kwargs):
    return _analysis(**kwargs)["physics_table"]


def _still_atmosphere(rain_mm_h):
    """Rain at a chosen rate with evaporation switched off (RH = 1, track colder than air)."""
    atm = Atmosphere(
        "rain",
        overrides={
            "rain_rate_mm_h": rain_mm_h,
            "relative_humidity": 1.0,
            "cloud_cover": 1.0,
            "track_temperature": 10.0,
        },
    )
    assert atm.track_temperature < atm.air_temperature
    return atm


def _mean(values):
    return sum(values) / len(values)


def _lane_means(surface):
    lanes = surface.lanes
    return [_mean(surface.water[j::lanes]) for j in range(lanes)]


def _segment_means(surface):
    lanes = surface.lanes
    return [_mean(surface.water[k * lanes : (k + 1) * lanes]) for k in range(surface.segments)]


def _clamp01(x):
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _lane_keeping_inputs(sim, index, n_target, v_target):
    """Deterministic lane-keeping driver: kinematic feed-forward + P on offset/heading.

    Driver input +1 steers right (road-wheel angle -max_steer), so the
    kinematic road-wheel angle L*kappa maps to the input -L*kappa/max_steer.
    """
    car = sim.cars[index]
    p = car.p
    kappa = sim.track.curvature[car.track_index]
    dpsi = math.atan2(math.sin(car.dpsi), math.cos(car.dpsi))
    steer = -p.wheelbase * kappa / p.max_steer + 0.08 * (car.n - n_target) + 1.0 * dpsi
    throttle = _clamp01(0.25 + 0.3 * (v_target - car.u))
    brake = _clamp01(0.3 * (car.u - v_target - 1.0))
    return (throttle, brake, steer, False)


def _drive_lane(sim, n_target, v_target, seconds=None, distance=None):
    car = sim.cars[0]
    car.place(0.0, n_target, v_target)
    offsets = []
    steps = 0
    limit = int(round(seconds / DT)) if seconds is not None else 10**9
    while steps < limit and (distance is None or car.distance < distance):
        sim.step([_lane_keeping_inputs(sim, 0, n_target, v_target)])
        offsets.append(car.n)
        steps += 1
    return offsets


# --------------------------------------------------------------------------
# Weather presets: dry, rain, light rain, drying
# --------------------------------------------------------------------------


@pytest.mark.verification
@pytest.mark.parametrize("weather", ["sun", "night"])
def test_dry_sessions_have_no_water(circle100, weather):
    """Dry presets: initial wetness 0 and no rain, so the film stays exactly 0
    everywhere, including under the tyres of driven cars."""
    sim = Simulation(circle100["physics_table"], cars=2, laps=2, weather=weather)
    surface = sim.track.surface
    assert set(surface.water) == {0.0}
    for _ in range(240):
        sim.step([(0.6, 0.0, 0.1, False), (0.6, 0.0, 0.1, False)])
    assert set(surface.water) == {0.0}
    assert surface.average_wetness() == 0.0
    for car in sim.cars:
        assert car.film == [0.0, 0.0, 0.0, 0.0]


@pytest.mark.verification
def test_rain_starts_wet_and_accumulates_from_a_dry_surface(circle100):
    """'rain' sessions start wet (initial wetness 1); a dry surface under the
    same rain gains water every update cycle (flat track: no drainage path)."""
    sim = Simulation(circle100["physics_table"], cars=1, laps=2, weather="rain")
    assert sim.track.surface.average_wetness() > 0.3
    assert min(sim.track.surface.water) > 0.0

    track = TrackModel(circle100["physics_table"])
    surface = track.surface
    atm = Atmosphere("rain")
    surface.initialise(atm, 0.0)
    assert set(surface.water) == {0.0}
    means = []
    for _ in range(6):
        for _ in range(int(round(10.0 / DT))):
            surface.update(DT, atm)
        means.append(_mean(surface.water))
    assert means[0] > 0.0
    assert all(b > a for a, b in zip(means, means[1:]))


@pytest.mark.verification
def test_heavy_rain_holds_more_water_than_light_rain():
    """Same circuit (3 deg banking, so the film can reach a sheet-flow balance)
    and the same preset initial state: 15 mm/h keeps more water than 2 mm/h
    at every minute, and also fills a dry surface faster."""
    table = _table(bank_deg=3.0)
    surfaces = {}
    history = {}
    for weather in ("rain", "light-rain"):
        surface = TrackModel(table).surface
        atm = Atmosphere(weather)
        surface.initialise(atm, atm.initial_wetness())
        history[weather] = [_mean(surface.water)]
        for _ in range(10):
            for _ in range(240):
                surface.update(0.25, atm)
            history[weather].append(_mean(surface.water))
        surfaces[weather] = surface
    # Both presets start from the same documented heavy-shower state ...
    assert history["rain"][0] == pytest.approx(history["light-rain"][0], rel=0.05)
    # ... then the light rain drains toward its lower balance.
    for heavy, light in zip(history["rain"][1:], history["light-rain"][1:]):
        assert heavy > light
    assert history["rain"][-1] > 1.3 * history["light-rain"][-1]

    # From a dry surface (flat circle, 2 simulated minutes).
    added = {}
    for weather in ("rain", "light-rain"):
        surface = TrackModel(_table()).surface
        atm = Atmosphere(weather)
        surface.initialise(atm, 0.0)
        for _ in range(480):
            surface.update(0.25, atm)
        added[weather] = _mean(surface.water)
    assert added["rain"] > 3.0 * added["light-rain"] > 0.0


@pytest.mark.verification
def test_drying_starts_wet_and_dries_over_minutes(circle100):
    """'drying': initial wetness 0.6 of the documented heavy-shower state (exact
    scaling), no rain, positive evaporation -> the mean film falls every minute."""
    table = circle100["physics_table"]
    atm = Atmosphere("drying")
    full = TrackModel(table).surface
    full.initialise(atm, 1.0)
    partial = TrackModel(table).surface
    partial.initialise(atm, atm.initial_wetness())
    assert atm.initial_wetness() == 0.6
    for a, b in zip(partial.water, full.water):
        assert a == pytest.approx(0.6 * b, rel=1e-12)

    sim = Simulation(table, cars=1, laps=2, weather="drying")
    surface = sim.track.surface
    means = [_mean(surface.water)]
    assert means[0] > 0.2
    for _ in range(5):
        for _ in range(240):
            surface.update(0.25, sim.atmosphere)
        means.append(_mean(surface.water))
    assert all(b < a for a, b in zip(means, means[1:]))
    assert means[-1] < 0.9 * means[0]


# --------------------------------------------------------------------------
# Time base of the incremental update
# --------------------------------------------------------------------------


@pytest.mark.verification
@pytest.mark.parametrize(
    "radius, dt, exposure",
    [
        (50.0, 1.0 / 120.0, 1.0),
        (60.0, 1.0 / 120.0, 0.4),
        (100.0, 1.0 / 120.0, 1.0),
        (77.0, 1.0 / 60.0, 0.4),
        (251.0, 1.0 / 120.0, 0.4),
    ],
)
def test_update_integrates_real_time(radius, dt, exposure):
    """Regression for the former 1.07-1.88x clock error: SurfaceState.update
    visits a segment once every ceil(segments / per_step) steps and must
    integrate exactly that much time. On a flat track (no drainage) with
    evaporation off, every cell gains exactly I * e * T after T seconds.

    Each case is chosen so that the old fixed WATER_UPDATE_INTERVAL per visit
    differs from the real revisit time by >= 10 %. Tolerance: float round-off
    of a few hundred additions (rel 1e-9).
    """
    table = _table(radius=radius, exposure=exposure)
    assert set(table["bank"]) == {0.0} and set(table["grade"]) == {0.0}
    surface = TrackModel(table).surface
    atm = _still_atmosphere(12.0)
    surface.initialise(atm, 0.0)
    per_step = max(1, math.ceil(surface.segments * dt / WATER_UPDATE_INTERVAL))
    cycle_steps = math.ceil(surface.segments / per_step)
    assert abs(WATER_UPDATE_INTERVAL / (cycle_steps * dt) - 1.0) >= 0.1
    cycles = math.ceil(60.0 / (cycle_steps * dt))
    steps = cycles * cycle_steps
    for _ in range(steps):
        surface.update(dt, atm)
    expected = 12.0 / 3600.0 * exposure * steps * dt
    for h in surface.water:
        assert h == pytest.approx(expected, rel=1e-9)
    assert surface.timer == pytest.approx(steps * dt, rel=1e-9)


# --------------------------------------------------------------------------
# Sheet flow: Manning velocity, conservation, puddles, banking, drainage
# --------------------------------------------------------------------------


@pytest.mark.verification
def test_sheet_flow_step_follows_manning_kinematic_wave():
    """One update with dt = WATER_UPDATE_INTERVAL (one visit of every segment):
    a cell of depth h above the texture storage h_s = 0.3 * texture moves
    dh = min(0.45 f, f * (f/1000)^(2/3) / n_M * sqrt(S) * dt / L) with f = h - h_s,
    across the lane (S = |sin bank|, L = lane width) toward the low lane, and
    along the lap (S = |grade|, L = segment length) toward the downhill segment.
    Tolerance rel 1e-6: the code uses the exponent 0.6666667 for 2/3."""
    atm = _still_atmosphere(0.0)
    h0 = 3.0

    # Lateral: banked flat circle, water only in lane 5 (high side) of segment 10.
    surface = TrackModel(_table(bank_deg=5.0)).surface
    surface.initialise(atm, 0.0)
    lanes = surface.lanes
    k, j = 10, lanes - 1
    surface.water[k * lanes + j] = h0
    surface.update(WATER_UPDATE_INTERVAL, atm)
    flow = h0 - 0.3 * surface.seg_texture[k]
    lane_w = 2.0 * surface.seg_half[k] / lanes
    velocity = (flow / 1000.0) ** (2.0 / 3.0) / MANNING_ASPHALT
    moved = min(0.45 * flow, flow * velocity * math.sqrt(abs(surface.seg_bank[k])) * WATER_UPDATE_INTERVAL / lane_w)
    assert 0.0 < moved < 0.45 * flow  # the Manning branch, not the stability cap
    assert surface.water[k * lanes + j - 1] == pytest.approx(moved, rel=1e-6)
    assert surface.water[k * lanes + j] == pytest.approx(h0 - moved, rel=1e-6)
    assert sum(surface.water) == pytest.approx(h0, rel=1e-12)

    # Longitudinal: unbanked sag circle, one cell on a downhill (+s) and one on
    # an uphill grade, far apart.
    surface = TrackModel(_table(sag=True)).surface
    surface.initialise(atm, 0.0)
    segs = surface.segments
    downhill = min(range(segs), key=lambda q: surface.seg_grade[q])  # most negative grade
    uphill = max(range(segs), key=lambda q: surface.seg_grade[q])
    assert surface.seg_grade[downhill] < -0.01 and surface.seg_grade[uphill] > 0.01
    j = 2
    for q in (downhill, uphill):
        surface.water[q * lanes + j] = h0
    surface.update(WATER_UPDATE_INTERVAL, atm)
    for q, target in ((downhill, (downhill + 1) % segs), (uphill, (uphill - 1) % segs)):
        flow = h0 - 0.3 * surface.seg_texture[q]
        velocity = (flow / 1000.0) ** (2.0 / 3.0) / MANNING_ASPHALT
        moved = min(
            0.45 * flow,
            flow * velocity * math.sqrt(abs(surface.seg_grade[q])) * WATER_UPDATE_INTERVAL / surface.seg_length,
        )
        assert 0.0 < moved < 0.45 * flow
        assert surface.water[target * lanes + j] == pytest.approx(moved, rel=1e-6)
        assert surface.water[q * lanes + j] == pytest.approx(h0 - moved, rel=1e-6)


@pytest.mark.verification
def test_longitudinal_sheet_flow_conserves_water():
    """Without rain, evaporation or banking, water only moves between segments
    of the closed lap: the total is conserved (rel 1e-12, float round-off) while
    it collects at the sag."""
    surface = TrackModel(_table(sag=True)).surface
    atm = _still_atmosphere(0.0)
    surface.initialise(atm, 0.0)
    surface.water = [1.0] * len(surface.water)
    total = sum(surface.water)
    for _ in range(240):
        surface.update(WATER_UPDATE_INTERVAL, atm)
    assert max(surface.water) < 25.0  # the 25 mm clamp (which removes water) never acts
    assert sum(surface.water) == pytest.approx(total, rel=1e-12)
    sag = min(range(surface.segments), key=lambda q: surface.seg_height[q])
    seg_means = _segment_means(surface)
    assert max(range(surface.segments), key=lambda q: seg_means[q]) == sag
    assert seg_means[sag] > 1.1


@pytest.mark.verification
def test_sag_collects_a_puddle_after_rain_spin_up():
    """Unbanked circuit with one elevation minimum: after initialise (heavy-shower
    balance + spin-up) and one simulated minute of rain the wettest segment is
    the sag, at least 1.5x the lap mean. (No claim about the crest: in a
    kinematic wave the near-flat crest drains slowly and holds about the lap
    mean.) The track analyser flags a 'water.puddle' hazard at the same place."""
    surface = TrackModel(_table(sag=True)).surface
    atm = Atmosphere("rain")
    surface.initialise(atm, 1.0)
    sag = min(range(surface.segments), key=lambda q: surface.seg_height[q])
    assert _segment_means(surface)[sag] > _mean(surface.water)
    for _ in range(int(round(60.0 / DT))):
        surface.update(DT, atm)
    seg_means = _segment_means(surface)
    lap_mean = _mean(surface.water)
    wettest = max(range(surface.segments), key=lambda q: seg_means[q])
    assert wettest == sag
    assert seg_means[sag] > 1.5 * lap_mean

    full = _analysis(sag=True, level="full")
    sag_s = (sag + 0.5) * surface.seg_length
    puddles = [h for h in full["hazards"] if h["type"] == "water.puddle"]
    assert puddles
    assert min(abs(h["s"] - sag_s) for h in puddles) <= surface.seg_length


@pytest.mark.verification
@pytest.mark.parametrize("bank_deg", [5.0, -5.0])
def test_banking_drains_water_toward_the_low_side_lane(bank_deg):
    """The low side is found geometrically (TrackModel.height at +-half width) and
    mapped to its lane with SurfaceState.cell; the film must increase
    monotonically toward that lane, which is the wettest."""
    track = TrackModel(_table(bank_deg=bank_deg))
    surface = track.surface
    idx = 0
    half = track.half_width[idx]
    low_n = -half if track.height(idx, 0.0, -half) < track.height(idx, 0.0, half) else half
    low_lane = surface.cell(1.0, 0.99 * low_n, idx)[0] % surface.lanes
    assert low_lane == (0 if bank_deg > 0.0 else surface.lanes - 1)

    atm = Atmosphere("rain")
    surface.initialise(atm, 1.0)
    for _ in range(int(round(60.0 / DT))):
        surface.update(DT, atm)
    lanes = _lane_means(surface)
    toward_low = lanes if low_lane == 0 else lanes[::-1]
    assert all(a > b for a, b in zip(toward_low, toward_low[1:]))
    assert toward_low[0] > 1.3 * toward_low[-1]


@pytest.mark.verification
@pytest.mark.parametrize(
    "bank_deg",
    [
        4.0,
        1.0,
        pytest.param(
            0.0,
            marks=pytest.mark.xfail(
                strict=True,
                reason="BUG: unbanked asphalt has no drainage path; the film grows at the full rain rate",
            ),
        ),
    ],
)
def test_steady_rain_reaches_a_sheet_flow_balance(bank_deg):
    """Under constant 15 mm/h rain the film must settle (rain in = sheet flow
    off the edge): over minutes 6-12 the mean film may grow by at most 10 % of
    the rain that fell in that window. initialise() itself assumes this balance
    with a 0.4 % minimum drainage slope; update() must not contradict it on a
    track whose editor default banking is 0."""
    surface = TrackModel(_table(bank_deg=bank_deg)).surface
    atm = Atmosphere("rain")
    surface.initialise(atm, 1.0)
    for _ in range(int(6 * 60 / WATER_UPDATE_INTERVAL)):
        surface.update(WATER_UPDATE_INTERVAL, atm)
    at_6 = _mean(surface.water)
    for _ in range(int(6 * 60 / WATER_UPDATE_INTERVAL)):
        surface.update(WATER_UPDATE_INTERVAL, atm)
    at_12 = _mean(surface.water)
    rain_input = atm.rain_rate_mm_h * 0.1  # mm in 6 minutes
    assert at_12 - at_6 < 0.1 * rain_input


@pytest.mark.verification
def test_water_film_is_clamped_and_off_asphalt_film_is_reduced():
    """Documented limits: the cell depth never exceeds 25 mm; a contact point
    outside the asphalt reads 0.6 x the edge-lane film."""
    surface = TrackModel(_table(radius=50.0)).surface
    atm = _still_atmosphere(600.0)
    surface.initialise(atm, 0.0)
    for _ in range(200):
        surface.update(1.0, atm)  # 600 mm/h for 200 s = 33 mm of rain
    assert max(surface.water) == 25.0
    half = surface.seg_half[0]
    cell, _ = surface.cell(5.0, half + 1.5, 0)
    assert cell % surface.lanes == surface.lanes - 1
    assert surface.film(cell, half + 1.5, half) == pytest.approx(0.6 * surface.water[cell], rel=1e-15)
    assert surface.film(cell, half - 0.5, half) == surface.water[cell]


# --------------------------------------------------------------------------
# Traffic: tyres expel water and lay rubber
# --------------------------------------------------------------------------


@pytest.mark.verification
def test_traffic_expels_documented_share_and_lays_rubber():
    """traffic(): h *= 1 - min(0.5, 0.6 sweep); rubber += 2e-9 E, capped at 1."""
    surface = TrackModel(_table()).surface
    surface.water = [2.0] * len(surface.water)
    surface.traffic(7, 0.1, 0.0)
    assert surface.water[7] == pytest.approx(2.0 * (1.0 - 0.06), rel=1e-15)
    surface.traffic(8, 2.0, 0.0)
    assert surface.water[8] == pytest.approx(1.0, rel=1e-15)
    surface.traffic(9, 0.0, 1.0e8)
    assert surface.water[9] == 2.0
    assert surface.rubber[9] == pytest.approx(0.2, rel=1e-12)
    surface.traffic(9, 0.0, 1.0e9)
    assert surface.rubber[9] == 1.0
    surface.water[10] = 0.0
    surface.traffic(10, 0.3, 0.0)
    assert surface.water[10] == 0.0


@pytest.mark.verification
def test_repeated_traffic_on_one_lane_dries_it_in_light_rain():
    """Direct SurfaceState check: one simulated car (4 tyres in lane 2, total
    sweep sum(w_i)/lane_w per pass) every 25 s in light rain leaves lane 2
    drier than every other lane and than the same lane without traffic. On a
    flat circle lanes do not exchange water; they only couple through the
    segment's track temperature, hence the 1e-3 tolerance on untouched lanes."""
    table = _table()
    atm_a = Atmosphere("light-rain")
    atm_b = Atmosphere("light-rain")
    driven = TrackModel(table).surface
    control = TrackModel(table).surface
    driven.initialise(atm_a, 1.0)
    control.initialise(atm_b, 1.0)
    lanes = driven.lanes
    lane_w = 2.0 * driven.seg_half[0] / lanes
    sweep = 2.0 * (GE.tyre_width_front + GE.tyre_width_rear) / lane_w
    for _ in range(6):
        for k in range(driven.segments):
            # one sweep call per tyre, as the vehicle does
            for width in (GE.tyre_width_front, GE.tyre_width_front, GE.tyre_width_rear, GE.tyre_width_rear):
                driven.traffic(k * lanes + 2, width / lane_w, 0.0)
        for _ in range(100):
            driven.update(0.25, atm_a)
            control.update(0.25, atm_b)
    assert sweep < 1.0
    d = _lane_means(driven)
    c = _lane_means(control)
    assert d[2] < 0.5 * c[2]
    for j in (0, 1, 3, 4, 5):
        assert d[j] == pytest.approx(c[j], rel=1e-3)
        for k in range(driven.segments):
            assert driven.water[k * lanes + 2] < driven.water[k * lanes + j]


@pytest.mark.verification
def test_car_pass_expels_water_as_documented(circle100):
    """End-to-end, one lap of a car in lane 2 with rain and evaporation off:
    each tyre sweeps dh = -0.6 h w V dt / A_cell, so one pass leaves
    exp(-0.6 sum_i(w_i l_i) / lane_w) of the film, with l_i = 1 - kappa n the
    wheel path length per unit centre-line length. Tolerances: lap mean 0.3 %
    (slip-angle cosine, product vs exponential); single segments 1 % (a 0.2 m
    step straddles 10 m segment boundaries). Other lanes stay untouched."""
    table = circle100["physics_table"]
    sim = Simulation(table, cars=1, laps=3, weather="light-rain")
    atm = sim.atmosphere
    atm.rain_rate_mm_h = 0.0
    atm.relative_humidity = 1.0
    atm.cloud_cover = 1.0
    surface = sim.track.surface
    surface.track_temperature = [atm.air_temperature - 3.0] * surface.segments
    before = list(surface.water)
    offsets = _drive_lane(sim, RIGHT_LANE_TARGET, 25.0, distance=640.0)
    lanes = surface.lanes
    lane_w = 2.0 * surface.seg_half[0] / lanes
    n_mean = _mean(offsets)
    kappa = sim.track.curvature[0]
    assert max(abs(n - RIGHT_LANE_TARGET) for n in offsets) < 0.3  # all wheels stay in lane 2
    widths = 2.0 * (GE.tyre_width_front + GE.tyre_width_rear)
    expected = math.exp(-0.6 * widths * (1.0 - kappa * n_mean) / lane_w)
    ratios = []
    for k in range(surface.segments):
        if 40.0 < (k + 0.5) * surface.seg_length < 600.0:  # passed exactly once by every wheel
            ratios.append(surface.water[k * lanes + 2] / before[k * lanes + 2])
        for j in (0, 1, 3, 4, 5):
            assert surface.water[k * lanes + j] == before[k * lanes + j]
    assert _mean(ratios) == pytest.approx(expected, rel=3e-3)
    for r in ratios:
        assert r == pytest.approx(expected, rel=1e-2)


@pytest.mark.verification
def test_driving_one_lane_in_light_rain_leaves_it_drier(circle100):
    """Three laps of a car holding lane 2 in light rain versus the same session
    with the car parked: the driven lane holds well under the control's water,
    the far lanes are unchanged (rel 1e-4: coupling only via track temperature)."""
    table = circle100["physics_table"]
    driven = Simulation(table, cars=1, laps=5, weather="light-rain")
    _drive_lane(driven, RIGHT_LANE_TARGET, 25.0, seconds=75.0)
    assert driven.trackers[0].lap >= 3
    parked = Simulation(table, cars=1, laps=5, weather="light-rain")
    parked.cars[0].place(0.0, RIGHT_LANE_TARGET, 0.0)
    for _ in range(int(round(75.0 / DT))):
        parked.step([(0.0, 1.0, 0.0, False)])
    d = _lane_means(driven.track.surface)
    c = _lane_means(parked.track.surface)
    assert d[2] < 0.7 * c[2]
    for j in (0, 5):
        assert d[j] == pytest.approx(c[j], rel=1e-4)
    assert d[2] < min(d[j] for j in (0, 1, 3, 4, 5))


@pytest.mark.verification
def test_rubber_is_laid_where_tyres_slide_in_the_dry(circle100):
    """Dry laps in lane 2: rubber appears only in that lane, in every segment,
    and a faster lap (more lateral slip power) lays more rubber per lap."""
    table = circle100["physics_table"]
    per_lap = {}
    for speed in (20.0, 35.0):
        sim = Simulation(table, cars=1, laps=3, weather="sun")
        _drive_lane(sim, RIGHT_LANE_TARGET, speed, distance=sim.track.length + 20.0)
        surface = sim.track.surface
        lanes = surface.lanes
        lane2 = surface.rubber[2::lanes]
        assert min(lane2) > 0.0
        assert max(lane2) < 1.0
        for j in (0, 1, 3, 4, 5):
            assert set(surface.rubber[j::lanes]) == {0.0}
        per_lap[speed] = sum(lane2)
    assert per_lap[35.0] > 2.0 * per_lap[20.0]


@pytest.mark.verification
def test_rain_washes_rubber_exponentially():
    """While raining every visit multiplies rubber by (1 - 0.002 dt); in the dry
    it is untouched."""
    table = _table()
    for weather, raining in (("rain", True), ("sun", False)):
        surface = TrackModel(table).surface
        atm = Atmosphere(weather)
        surface.initialise(atm, 0.0)
        surface.rubber = [0.5] * len(surface.rubber)
        visits = 400
        for _ in range(visits):
            surface.update(WATER_UPDATE_INTERVAL, atm)
        expected = 0.5 * (1.0 - 0.002 * WATER_UPDATE_INTERVAL) ** visits if raining else 0.5
        for r in surface.rubber:
            assert r == pytest.approx(expected, rel=1e-12)


@pytest.mark.verification
@pytest.mark.parametrize("film", [0.0, 0.3, 2.0])
def test_rubber_raises_dry_grip_and_lowers_wet_grip(oval_quick, film):
    """Vehicle-level: surf_grip *= 1 + 0.04 rub (1 - wet) - 0.08 rub wet with
    wet = h / (h + 0.15). Two identical cars on identical films, one track fully
    rubbered: the grip ratio is exactly that factor (rel 1e-12)."""
    table = oval_quick["physics_table"]
    env = Environment(Atmosphere("sun"))
    cars = []
    for rubber in (0.0, 1.0):
        track = TrackModel(table)
        track.surface.water = [film] * len(track.surface.water)
        track.surface.rubber = [rubber] * len(track.surface.rubber)
        car = Vehicle(GE, track, compound="intermediate")
        car.place(300.0, 0.0, 30.0)
        car.step(DT, 0.0, 0.0, 0.0, False, env)
        cars.append(car)
    wet = film / (film + 0.15) if film > 0.0 else 0.0
    expected = 1.0 + 0.04 * (1.0 - wet) - 0.08 * wet
    for i in range(4):
        assert cars[1].grip[i] / cars[0].grip[i] == pytest.approx(expected, rel=1e-12)
    if film == 0.0:
        assert expected > 1.0
    if film == 2.0:
        assert expected < 1.0


# --------------------------------------------------------------------------
# Track temperature
# --------------------------------------------------------------------------


@pytest.mark.verification
def test_track_temperature_relaxes_toward_weather_target():
    """Per visit: T += (T* - T) min(1, dt/600) with
    T* = T_air + 18 (1 - cloud) - 6 w/(w + 0.3) - 4 [raining], w = lane-mean film.
    Dry: exact geometric approach; wet: one visit checked against the formula."""
    table = _table()
    # Dry 'sun' through the real-time update path (DT steps, whole cycles).
    surface = TrackModel(table).surface
    atm = Atmosphere("sun")
    surface.initialise(atm, 0.0)
    t0 = atm.track_temperature
    target = atm.air_temperature + 18.0 * (1.0 - atm.cloud_cover)
    per_step = max(1, math.ceil(surface.segments * DT / WATER_UPDATE_INTERVAL))
    cycle_steps = math.ceil(surface.segments / per_step)
    cycle = cycle_steps * DT
    cycles = 300
    for _ in range(cycles * cycle_steps):
        surface.update(DT, atm)
    expected = target + (t0 - target) * (1.0 - cycle / 600.0) ** cycles
    for t in surface.track_temperature:
        assert t == pytest.approx(expected, rel=1e-12)
    assert t0 < expected < target
    for _ in range(3 * 3600):
        surface.update(1.0, atm)
    assert surface.track_temperature[0] == pytest.approx(target, abs=0.01)

    # Wet: one visit of a uniform 1 mm film in the rain.
    surface = TrackModel(table).surface
    atm = Atmosphere("rain")
    surface.initialise(atm, 0.0)
    surface.water = [1.0] * len(surface.water)
    t0 = surface.track_temperature[0]
    surface.update(WATER_UPDATE_INTERVAL, atm)
    target = atm.air_temperature + 18.0 * (1.0 - atm.cloud_cover) - 6.0 * 1.0 / 1.3 - 4.0
    expected = t0 + (target - t0) * WATER_UPDATE_INTERVAL / 600.0
    for t in surface.track_temperature:
        assert t == pytest.approx(expected, rel=1e-12)


def _equilibrium_track_temperature(weather):
    """Mean track temperature after 2 simulated hours (12 relaxation times) on a
    3 deg banked circle, so the film also reaches its balance."""
    surface = TrackModel(_table(radius=50.0, bank_deg=3.0)).surface
    atm = Atmosphere(weather)
    surface.initialise(atm, atm.initial_wetness())
    for _ in range(3600):
        surface.update(2.0, atm)
    return _mean(surface.track_temperature), atm


def _wet_bulb_stull(t_c, rh):
    """Stull (2011), J. Appl. Meteor. Climatol. 50, 2267: wet-bulb temperature (degC)."""
    p = 100.0 * rh
    return (
        t_c * math.atan(0.151977 * math.sqrt(p + 8.313659))
        + math.atan(t_c + p)
        - math.atan(p - 1.676331)
        + 0.00391838 * p**1.5 * math.atan(0.023101 * p)
        - 4.686035
    )


@pytest.mark.validation
@pytest.mark.parametrize(
    "weather, low, high",
    [
        ("sun", 5.0, 25.0),
        pytest.param(
            "night",
            -4.0,
            8.0,
            marks=pytest.mark.xfail(strict=True, reason="BUG: solar heating term applied in the night preset"),
        ),
    ],
)
def test_dry_equilibrium_track_temperature_is_plausible(weather, low, high):
    """Track minus air temperature at equilibrium. Sunny day: asphalt runs
    roughly 5-25 K above the air (pavement surface-temperature studies, e.g.
    FHWA LTPP seasonal monitoring; F1 timing weather feeds of summer races).
    Night: no solar input, the track only keeps stored heat; night races
    (Bahrain, Singapore, Qatar, Las Vegas timing weather feeds) show the track
    a few K above the air, and the preset itself starts at air + 3 K. Bands are
    plausibility ranges, not calibration."""
    track_t, atm = _equilibrium_track_temperature(weather)
    assert low <= track_t - atm.air_temperature <= high


@pytest.mark.validation
@pytest.mark.parametrize(
    "weather",
    [
        pytest.param(
            "rain",
            marks=pytest.mark.xfail(strict=True, reason="BUG: wet-track temperature target far below the wet-bulb"),
        ),
        pytest.param(
            "light-rain",
            marks=pytest.mark.xfail(strict=True, reason="BUG: wet-track temperature target far below the wet-bulb"),
        ),
    ],
)
def test_wet_equilibrium_track_temperature_near_wet_bulb(weather):
    """Under overcast rain (cloud cover >= 0.95, negligible net radiation) a
    wetted surface tends to the psychrometric wet-bulb temperature (Monteith &
    Unsworth, Principles of Environmental Physics, 4th ed., ch. 13); rain itself
    falls at about that temperature. F1 timing weather feeds for wet sessions
    show the track within a few K of the air. Plausibility band:
    T_wb - 3 K <= T_track <= T_air + 5 K (not calibration)."""
    track_t, atm = _equilibrium_track_temperature(weather)
    wet_bulb = _wet_bulb_stull(atm.air_temperature, atm.relative_humidity)
    assert wet_bulb - 3.0 <= track_t <= atm.air_temperature + 5.0


# --------------------------------------------------------------------------
# Wet grip: aquaplaning and braking
# --------------------------------------------------------------------------


def _grip_ratio_on_film(table, compound, speed, film):
    """grip(film) / grip(dry) per wheel for two identical cars after one step."""
    env = Environment(Atmosphere("sun"))
    cars = []
    for depth in (0.0, film):
        track = TrackModel(table)
        track.surface.water = [depth] * len(track.surface.water)
        car = Vehicle(GE, track, compound=compound)
        car.place(300.0, 0.0, speed)
        car.step(DT, 0.0, 0.0, 0.0, False, env)
        cars.append(car)
    return [cars[1].grip[i] / cars[0].grip[i] for i in range(4)], cars[1]


@pytest.mark.verification
@pytest.mark.parametrize("compound", ["medium", "intermediate", "wet"])
@pytest.mark.parametrize("speed", [10.0, 20.0, 35.0, 80.0])
def test_vehicle_grip_applies_water_factor_of_contact_film(oval_quick, compound, speed):
    """Vehicle wiring: on a 2 mm film the per-wheel grip ratio wet/dry equals
    TyreModel.water_factor(h, 0.7 texture, |V_cx|, w_i, p_i) for that wheel
    (same loads, temperatures and pressures on both cars; rel 1e-12)."""
    table = oval_quick["physics_table"]
    ratios, car = _grip_ratio_on_film(table, compound, speed, 2.0)
    for i in range(4):
        expected = car.tyre.water_factor(
            2.0, 0.7 * table["roughness"][0], speed, car.tyre_width[i], car.pressure[i]
        )[0]
        assert ratios[i] == pytest.approx(expected, rel=1e-12)


@pytest.mark.verification
def test_slick_on_deep_film_at_speed_keeps_far_less_grip_than_wet_tyre(oval_quick):
    """3 mm film, 80 m/s. Hand calculation (texture drain 0.7 * 0.8 mm):
    slick (no grooves): h_res = 3 - 0.56 = 2.44 mm, r = 2.44/3.44, above 1.05 V_p
    -> 0.62 (1 - 0.95 r) = 0.2022; wet tyre (front, 305 mm): grooves evacuate
    1000 * 0.075 / (0.305 * 80) = 3.07 mm > 2.44 mm -> no residual, factor 0.90."""
    slick = TyreModel(COMPOUNDS["medium"])
    wet = TyreModel(COMPOUNDS["wet"])
    residual = 3.0 - 0.56
    r = residual / (residual + 1.0)
    f_slick, res_slick = slick.water_factor(3.0, 0.56, 80.0, 0.305, 165.0)
    f_wet, res_wet = wet.water_factor(3.0, 0.56, 80.0, 0.305, 155.0)
    assert res_slick == pytest.approx(residual, rel=1e-12)
    assert f_slick == pytest.approx(0.62 * (1.0 - 0.95 * r), rel=1e-12)
    assert res_wet == 0.0
    assert f_wet == pytest.approx(0.90, rel=1e-12)
    assert f_slick < 0.25 * f_wet

    table = oval_quick["physics_table"]
    slick_ratios, _ = _grip_ratio_on_film(table, "medium", 80.0, 3.0)
    wet_ratios, _ = _grip_ratio_on_film(table, "wet", 80.0, 3.0)
    for i in range(4):
        assert slick_ratios[i] < 0.3 * wet_ratios[i]


@pytest.mark.verification
@pytest.mark.parametrize("pressure_kpa", [100.0, 150.0, 165.0, 200.0, 250.0])
def test_grip_collapse_is_centred_on_horne_dreher_speed(pressure_kpa):
    """Slick on a deep film (residual independent of speed): the grip is flat
    below 0.6 V_p, collapses through a smoothstep and is flat again above
    1.05 V_p, with V_p = 9 sqrt(p[psi]) kt (Horne & Dreher, NASA TN D-2056).
    The half-collapse speed is 0.825 V_p and 96.6 % of the collapse has
    happened at V_p (smoothstep at (1 - 0.6)/0.45). Bisection to 1e-10 m/s."""
    tyre = TyreModel(COMPOUNDS["medium"])
    v_p = aquaplaning_speed(pressure_kpa)
    assert v_p == pytest.approx(9.0 * math.sqrt(pressure_kpa / 6.894757) * 0.514444, rel=1e-12)

    def factor(v):
        return tyre.water_factor(3.0, 0.5, v, 0.305, pressure_kpa)[0]

    high = factor(0.3 * v_p)
    low = factor(2.0 * v_p)
    assert factor(0.6 * v_p * (1.0 - 1e-9)) == high
    assert factor(1.05 * v_p * (1.0 + 1e-9)) == low
    assert low < 0.5 * high
    mid = 0.5 * (high + low)
    a, b = 0.6 * v_p, 1.05 * v_p
    while b - a > 1e-10:
        c = 0.5 * (a + b)
        if factor(c) > mid:
            a = c
        else:
            b = c
    assert 0.5 * (a + b) / v_p == pytest.approx(0.825, abs=1e-9)
    done = (high - factor(v_p)) / (high - low)
    assert done == pytest.approx(0.9657, abs=1e-4)


@pytest.mark.verification
def test_vehicle_aquaplaning_onset_tracks_tyre_pressure(oval_quick):
    """A slick-shod car on a 3 mm film: the speed at which its front-left grip
    has lost half of the speed-dependent part is 0.825 x the Horne & Dreher
    speed of that tyre's current pressure (cold 150 kPa at 80 degC -> ~21.6 m/s).
    Raising the cold pressure raises the onset as sqrt(p). Bisection on whole
    vehicle steps; tolerance 1e-3 relative (bisection to 1e-4 m/s)."""
    table = oval_quick["physics_table"]
    from dataclasses import replace

    onsets = {}
    for cold in (150.0, 240.0):
        params = replace(GE, cold_pressure=cold)

        def ratio(v):
            env = Environment(Atmosphere("sun"))
            out = []
            for depth in (0.0, 3.0):
                track = TrackModel(table)
                track.surface.water = [depth] * len(track.surface.water)
                car = Vehicle(params, track, compound="medium")
                car.place(300.0, 0.0, v)
                car.step(DT, 0.0, 0.0, 0.0, False, env)
                out.append(car)
            return out[1].grip[0] / out[0].grip[0], out[1].pressure[0]

        high, pressure = ratio(5.0)
        low, _ = ratio(80.0)
        v_p = aquaplaning_speed(pressure)
        assert ratio(0.59 * v_p)[0] == pytest.approx(high, rel=1e-12)
        assert ratio(1.06 * v_p)[0] == pytest.approx(low, rel=1e-12)
        mid = 0.5 * (high + low)
        a, b = 0.6 * v_p, 1.05 * v_p
        while b - a > 1e-4:
            c = 0.5 * (a + b)
            if ratio(c)[0] > mid:
                a = c
            else:
                b = c
        onsets[cold] = 0.5 * (a + b)
        assert onsets[cold] == pytest.approx(0.825 * v_p, rel=1e-3)
    assert onsets[150.0] == pytest.approx(0.825 * aquaplaning_speed(150.0), rel=1e-3)
    assert onsets[240.0] / onsets[150.0] == pytest.approx(math.sqrt(240.0 / 150.0), rel=2e-3)


def _stopping_distance(table, weather, v0, compound=None):
    """Straight-line ABS stop ('full' assists, analog brake 1) from v0 to 0.5 m/s.

    Returns (path length, compound id, final lateral offset)."""
    sim = Simulation(table, cars=1, laps=3, weather=weather, compound=compound)
    car = sim.cars[0]
    car.place(150.0, 0.0, v0)
    controller = PlayerController("full")
    start = car.distance
    for _ in range(int(20.0 / DT)):
        if car.u <= 0.5:
            break
        dpsi = math.atan2(math.sin(car.dpsi), math.cos(car.dpsi))
        steer = 0.05 * car.n + 1.0 * dpsi
        sim.step([controller.process(car, DT, 0.0, 1.0, steer, False, True)])
    assert car.u <= 0.5
    return car.distance - start, car.compound_id, car.n


@pytest.mark.validation
@pytest.mark.parametrize("v0", [30.0, 50.0, 70.0])
def test_wet_braking_distance_is_longer_than_dry(oval_quick, v0):
    """Same car, same speed, straight-line ABS stop on the oval straight.
    Reference envelope: peak tyre-road friction on asphalt 0.8-0.9 dry and
    0.5-0.7 wet (Wong, Theory of Ground Vehicles, 4th ed., Table 1.1), i.e.
    wet/dry stopping-distance ratio ~1.1-1.8; aero load scales both equally.
    Band [1.1, 2.0] is plausibility, not calibration. Slicks in heavy rain
    must stop later than the wet tyre the preset fits."""
    table = oval_quick["physics_table"]
    dry, dry_compound, dry_n = _stopping_distance(table, "sun", v0)
    rain, rain_compound, rain_n = _stopping_distance(table, "rain", v0)
    light, light_compound, light_n = _stopping_distance(table, "light-rain", v0)
    slick_in_rain, _, _ = _stopping_distance(table, "rain", v0, compound="medium")
    assert (dry_compound, rain_compound, light_compound) == ("medium", "wet", "intermediate")
    # Straight stops (the slick in heavy rain may slide off line: no such check).
    assert max(abs(dry_n), abs(rain_n), abs(light_n)) < 1.0
    assert 1.1 <= rain / dry <= 2.0
    assert 1.1 <= light / dry <= 2.0
    assert slick_in_rain > rain


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


@pytest.mark.verification
def test_identical_wet_sessions_give_identical_surface_state(circle100):
    """Two runs with identical inputs give bit-identical water, rubber and track
    temperature arrays (and the state does evolve, so the check is not vacuous)."""
    table = circle100["physics_table"]
    runs = []
    for _ in range(2):
        sim = Simulation(table, cars=1, laps=3, weather="rain")
        initial = list(sim.track.surface.water)
        _drive_lane(sim, RIGHT_LANE_TARGET, 22.0, seconds=8.0)
        surface = sim.track.surface
        runs.append(
            (list(surface.water), list(surface.rubber), list(surface.track_temperature), initial, sim.cars[0].x)
        )
    assert runs[0][0] == runs[1][0]
    assert runs[0][1] == runs[1][1]
    assert runs[0][2] == runs[1][2]
    assert runs[0][4] == runs[1][4]
    assert runs[0][0] != runs[0][3]

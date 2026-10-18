"""Whole-vehicle dynamics scenarios (verification and plausibility validation).

Every scenario drives a complete :class:`physics_engine.vehicle.Vehicle`
(tyres, aero, suspension, powertrain, brakes and thermal states together) on
a track produced by the analyser. Two kinds of checks are made:

* ``verification``: the emergent behaviour must agree with an analytic result
  (point-mass banked curve, rolling-down-a-slope, load-transfer moment
  balance, kinematics of circular motion), with the quasi-steady-state
  envelope of :mod:`physics_engine.limits` built from the same parameters, or
  with a self-consistency property (time-step convergence, bounded states).
* ``validation``: the emergent behaviour is compared with public, widely
  quoted figures for modern single-seaters. These bands are plausibility
  ranges, not calibration targets; the parameter set is a generic estimate
  (see ``physics_engine.vehicles``), not any real car.

Conventions: world x, z horizontal, y up; body x forward, y left; steer
input +1 turns right; track lateral n > 0 is left of the travel direction;
``circle_document`` is a right-hand circle (negative curvature), so its
outside is on the left and positive bank is helpful. Wheel order FL, FR,
RL, RR.

All scenarios are deterministic (fixed inputs, fixed seeds). Tracks are
rebuilt for every scenario because a :class:`TrackModel` carries mutable
surface state (rubber, water); the surface is initialised like
``Simulation`` does, so the road temperature is the weather preset's.
"""

import math
import random
import statistics
from dataclasses import replace

import pytest

from apex_bridge.session import Session
from conftest import circle_document
from physics_engine.assists import PlayerController
from physics_engine.constants import GRAVITY
from physics_engine.environment import Atmosphere
from physics_engine.limits import PerformanceEnvelope
from physics_engine.simulation import Environment, Simulation
from physics_engine.track import TrackModel
from physics_engine.tyre import TyreModel
from physics_engine.vehicle import Vehicle
from physics_engine.vehicles import COMPOUNDS, VEHICLES
from track_analyzer import analyze

GE = VEHICLES["ground-effect-2022"]
COMPOUND = "medium"
FUEL = 50.0  # kg; envelope and cars use the same mass
DT = 1.0 / 120.0
FL, FR, RL, RR = 0, 1, 2, 3
KMH = 1.0 / 3.6
SKIDPAD_RADIUS = 100.0


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def calm_atmosphere(preset="sun"):
    """Weather preset without wind, so airspeed equals ground speed."""
    return Atmosphere(preset, overrides={"wind_speed": 0.0})


def calm_env():
    return Environment(calm_atmosphere())


def envelope(params=GE):
    return PerformanceEnvelope(params, COMPOUND, calm_atmosphere(), fuel=FUEL)


def fresh_track(table, atmosphere=None):
    track = TrackModel(table)
    track.surface.initialise(atmosphere or calm_atmosphere(), 0.0)
    return track


def new_car(track, **kwargs):
    return Vehicle(GE, track, compound=COMPOUND, fuel=FUEL, **kwargs)


def pursuit_steer(car, n_target=0.0):
    """Pure pursuit toward a centre-line offset, with yaw-rate damping.

    Same law as the AI driver (lookahead grows with speed); returns the
    driver steer input (+1 = right).
    """
    speed = max(car.u, 1.0)
    look = min(max(6.0 + 0.35 * speed, 8.0), 40.0)
    tx, _, tz, _ = car.track.world_point(car.s + look, n_target)
    dx = tx - car.x
    dz = tz - car.z
    sp, cp = math.sin(car.psi), math.cos(car.psi)
    xb = dx * sp + dz * cp
    yb = dx * cp - dz * sp
    k = 2.0 * yb / (xb * xb + yb * yb)
    wheelbase = car.p.wheelbase
    delta = math.atan(wheelbase * k) + 0.35 * (k * speed - car.r) * wheelbase / speed
    return max(-1.0, min(1.0, -delta / car.p.max_steer))


class SpeedHold:
    """PI throttle controller holding a target speed (no brake)."""

    def __init__(self, kp=0.5, ki=1.0, start=0.2):
        self.kp = kp
        self.ki = ki
        self.integral = start

    def __call__(self, car, target):
        err = target - car.u
        self.integral = min(1.0, max(0.0, self.integral + self.ki * err * DT))
        return min(1.0, max(0.0, self.integral + self.kp * err))


def longest_straight(analysis):
    return max(analysis["straights"], key=lambda s: s["length"])


def all_finite(values):
    return all(math.isfinite(x) for x in values)


def hill_oval_document(straight=1200.0, radius=150.0, width=20.0, rise=96.0):
    """Stadium oval whose first straight climbs ``rise`` metres and whose far
    straight descends back; both half circles are level. With the defaults
    the grade is 96 / 1200 = 8 % (the analyser's limit is 12 %)."""
    half = straight / 2.0
    pts = []
    for k in range(9):
        pts.append({"x": -half + straight * k / 8.0, "y": rise * k / 8.0, "z": -radius})
    for k in range(1, 8):
        a = -math.pi / 2 + math.pi * k / 8.0
        pts.append({"x": half + radius * math.cos(a), "y": rise, "z": radius * math.sin(a)})
    for k in range(9):
        pts.append({"x": half - straight * k / 8.0, "y": rise * (1.0 - k / 8.0), "z": radius})
    for k in range(1, 8):
        a = math.pi / 2 + math.pi * k / 8.0
        pts.append({"x": -half + radius * math.cos(a), "y": 0.0, "z": radius * math.sin(a)})
    return {
        "id": "hill-oval",
        "version": 1,
        "name": "Hill oval",
        "width": width,
        "points": pts,
        "runoff": {"type": "asphalt", "width": 30.0},
    }


# --------------------------------------------------------------------------
# straight-line acceleration and top speed
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def standing_start(oval_quick):
    """Full throttle from rest along the oval's longest straight.

    The player controller with assists 'full' supplies traction control (an
    analog full-throttle request); pure pursuit keeps the car on the centre
    line. The run stops 10 m before the straight ends.
    """
    straight = longest_straight(oval_quick)
    track = fresh_track(oval_quick["physics_table"])
    env = calm_env()
    car = new_car(track)
    car.place(straight["start"], 0.0, 0.0)
    pc = PlayerController("full")
    t = 0.0
    t100 = None
    samples = []
    max_n = 0.0
    run_length = straight["length"] - 10.0
    while car.distance < run_length and t < 40.0:
        thr, brk, steer, drs = pc.process(car, DT, 1.0, 0.0, pursuit_steer(car), False, True)
        car.step(DT, thr, brk, steer, drs, env)
        t += DT
        if t100 is None and car.u >= 100.0 * KMH:
            t100 = t
        if len(samples) * 0.5 <= t:
            samples.append(car.u)
        max_n = max(max_n, abs(car.n))
    return {
        "t100": t100,
        "time": t,
        "distance": car.distance,
        "v_end": car.u,
        "samples": samples,
        "max_n": max_n,
        "car": car,
    }


@pytest.mark.validation
def test_standing_start_0_to_100_kmh(standing_start):
    """0-100 km/h of a modern F1-type car in the published 2.0-4.0 s band.

    Reference: widely published figures for current Formula 1 cars quote
    about 2.5-3 s for 0-100 km/h (team and broadcaster statements). The band
    2.0-4.0 s is a plausibility range, not a calibration target.
    """
    t100 = standing_start["t100"]
    assert t100 is not None, "the car never reached 100 km/h"
    assert 2.0 <= t100 <= 4.0, f"0-100 km/h took {t100:.3f} s"
    # The run must have been a straight-line one (pure pursuit on the centre line).
    assert standing_start["max_n"] < 1.0


@pytest.mark.verification
def test_top_speed_on_longest_straight(standing_start):
    """At the end of the ~1.2 km straight the car approaches the QSS top speed.

    ``PerformanceEnvelope.top_speed`` is the balance of maximum wheel force
    (engine + ERS through the best gear) against drag and rolling
    resistance, with the same parameters as the car. A standing start over
    the straight reaches it within 6 %; and it must agree within 3 % with
    the QSS acceleration integrated over the same distance (v dv/ds = a),
    the residual being gear-shift torque interruptions, launch-clutch slip
    and the ride-height dependence of downforce (rolling resistance) that
    the QSS model ignores.
    """
    env_qss = envelope()
    v_end = standing_start["v_end"]
    top = env_qss.top_speed
    assert v_end <= top * 1.001, f"dynamic {v_end:.2f} m/s above QSS top speed {top:.2f} m/s"
    assert (top - v_end) / top < 0.06, f"v_end {v_end:.2f} m/s vs top speed {top:.2f} m/s"

    # QSS forward integration over the same distance (flat, straight road).
    v = 0.0
    s = 0.0
    ds = 0.25
    while s < standing_start["distance"]:
        a = env_qss.acceleration(v, 0.0, 0.0, 0.0, 0.0)
        v = math.sqrt(max(0.0, v * v + 2.0 * a * ds))
        s += ds
    assert v_end == pytest.approx(v, rel=0.03), f"dynamic {v_end:.2f} m/s, QSS {v:.2f} m/s"

    # Monotonic acceleration (0.5 s samples; gear shifts only dip for 10 ms).
    samples = standing_start["samples"]
    assert all(b > a for a, b in zip(samples, samples[1:]))
    assert all(math.isfinite(x) for x in samples)


# --------------------------------------------------------------------------
# braking from 300 km/h
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def braking_run(oval_quick):
    """Full braking from ~300 km/h with assists 'full' (ABS) on the straight."""
    straight = longest_straight(oval_quick)
    track = fresh_track(oval_quick["physics_table"])
    env = calm_env()
    car = new_car(track)
    target = 300.0 * KMH
    car.place(straight["start"] + 300.0, 0.0, target)
    hold = SpeedHold(kp=1.0, ki=2.0, start=0.6)
    for _ in range(int(1.5 / DT)):
        car.step(DT, hold(car, target), 0.0, pursuit_steer(car), False, env)
    pc = PlayerController("full")
    v_start = car.u
    d0 = car.distance
    t = 0.0
    history = []
    locked_steps = 0
    steps = 0
    while car.u > 0.5 and t < 10.0:
        thr, brk, steer, drs = pc.process(car, DT, 0.0, 1.0, pursuit_steer(car), False, True)
        car.step(DT, thr, brk, steer, drs, env)
        t += DT
        steps += 1
        locked_steps += any(car.locked)
        history.append((t, car.u, car.ax_world))
    return {
        "v_start": v_start,
        "distance": car.distance - d0,
        "time": t,
        "history": history,
        "locked_fraction": locked_steps / max(steps, 1),
        "max_n": abs(car.n),
    }


@pytest.mark.verification
def test_braking_distance_from_300_kmh_matches_qss(braking_run):
    """Stopping distance agrees with the QSS envelope within +-20 %.

    Reference: d = integral of v dv / D(v) from 0.5 m/s to the initial speed,
    where D(v) = PerformanceEnvelope.deceleration (tyre capacity with load
    transfer, aero load, drag, brake torque limit). The QSS assumes every
    tyre sits at its friction peak from the first instant; the dynamic car
    loses distance to the brake-pressure build-up, pitch and tyre-relaxation
    transients and ABS cycling around the peak slip, and the fixed brake bias
    cannot match the changing axle-load split exactly. +-20 % covers those
    transient losses (the dynamic distance is expected to be longer).
    """
    env_qss = envelope()
    v_start = braking_run["v_start"]
    assert v_start == pytest.approx(300.0 * KMH, rel=0.02)
    dv = 0.01
    v = 0.5
    d_qss = 0.0
    while v < v_start:
        vm = min(v + 0.5 * dv, v_start)
        d_qss += vm * min(dv, v_start - v) / env_qss.deceleration(vm, 0.0, 0.0, 0.0, 0.0)
        v += dv
    d = braking_run["distance"]
    assert d == pytest.approx(d_qss, rel=0.20), f"dynamic {d:.1f} m, QSS {d_qss:.1f} m"
    # The anti-lock assist keeps the wheels rolling (it cycles around the peak).
    assert braking_run["locked_fraction"] < 0.05
    # Straight-line stop on the centre line.
    assert braking_run["max_n"] < 0.5


@pytest.mark.validation
def test_peak_braking_deceleration_plausible(braking_run):
    """Peak deceleration at high speed lies in a 4-6.5 g plausibility band.

    Reference: public statements on Formula 1 braking quote peak
    decelerations of about 5-6 g from high speed (aero load adds grip). The
    peak is taken over 0.1 s windows while the speed is above 200 km/h. This
    is a plausibility range, not a calibration target.
    """
    hist = [h for h in braking_run["history"] if h[1] > 200.0 * KMH]
    window = 12  # 0.1 s
    assert len(hist) > window
    peak = max(
        -statistics.fmean(h[2] for h in hist[i : i + window]) for i in range(len(hist) - window + 1)
    )
    peak_g = peak / GRAVITY
    assert 4.0 <= peak_g <= 6.5, f"peak deceleration {peak_g:.2f} g"


# --------------------------------------------------------------------------
# brake bias and lock-up
# --------------------------------------------------------------------------


def ideal_brake_bias(env_qss, speed):
    """Front share of the QSS braking capacity at full deceleration.

    Axle loads: static + aero (QSS ride heights) + longitudinal transfer
    m D h / L; capacity = sum of mu(Fz) Fz per axle (load-sensitive tyres,
    front and rear tread widths differ).
    """
    decel = env_qss.deceleration(speed, 0.0, 0.0, 0.0, 0.0)
    front, rear = env_qss.loads(speed, GRAVITY)
    shift = env_qss.m * decel * env_qss.h / env_qss.L
    front += shift
    rear -= shift
    tyre = env_qss.tyre
    cap_f = tyre.peak_mu(0.5 * front, GE.tyre_width_front) * front
    cap_r = tyre.peak_mu(0.5 * rear, GE.tyre_width_rear) * rear
    return cap_f / (cap_f + cap_r)


def first_lockup(car, step, steer_fn, ramp_time=1.5):
    """Ramp the pedal 0 -> 1 over ``ramp_time``; return (axle, pedal) of the
    first axle whose slip ratio passes twice the peak (runaway to lock)."""
    kp = car.tyre.kappa_peak
    t = 0.0
    while t < ramp_time + 0.5:
        t += DT
        pedal = min(1.0, t / ramp_time)
        step(0.0, pedal, steer_fn(car))
        k_front = min(car.kappa[FL], car.kappa[FR])
        k_rear = min(car.kappa[RL], car.kappa[RR])
        if k_front < -2.0 * kp or k_rear < -2.0 * kp:
            return ("front" if k_front < k_rear else "rear"), pedal
    return None, None


@pytest.mark.verification
def test_brake_bias_moves_lockup_between_axles(oval_quick):
    """Vehicle.brake_bias moves the lock-up tendency between the axles.

    A slow pedal ramp from 70 m/s (no ABS) finds which axle passes its slip
    peak first. Reference: the QSS 'ideal' bias (front share of the axle
    friction capacities at full deceleration) lies between 0.50 and 0.60;
    biases below it must lock the rear first, biases above it the front, and
    moving the bias further from the ideal must lock that axle at a lower
    pedal (higher lock-up tendency).
    """
    ideal = ideal_brake_bias(envelope(), 50.0)
    assert 0.50 < ideal < 0.60, f"QSS ideal bias {ideal:.3f}"
    straight = longest_straight(oval_quick)
    env = calm_env()
    results = {}
    for bias in (0.45, 0.50, 0.60, 0.65, 0.70):
        car = new_car(fresh_track(oval_quick["physics_table"]), brake_bias=bias)
        car.place(straight["start"] + 300.0, 0.0, 70.0)
        for _ in range(60):
            car.step(DT, 0.3, 0.0, pursuit_steer(car), False, env)

        def step(thr, brk, steer, car=car):
            car.step(DT, thr, brk, steer, False, env)

        results[bias] = first_lockup(car, step, pursuit_steer)
    assert results[0.45][0] == "rear" and results[0.50][0] == "rear", results
    assert all(results[b][0] == "front" for b in (0.60, 0.65, 0.70)), results
    # Further from the ideal bias -> that axle locks at a lighter pedal.
    assert results[0.45][1] < results[0.50][1], results
    assert results[0.70][1] < results[0.65][1] < results[0.60][1], results


@pytest.mark.verification
def test_session_brake_bias_setting_moves_lockup(oval_quick):
    """Session brakeBias / set_brake_bias reach car 0 and shift the lock-up.

    The setting is clamped to [0.45, 0.75]; 0.45 (rear-heavy, below the QSS
    ideal of ~0.57) locks the rear first and 0.75 (front-heavy) the front
    first, under the same pedal ramp with assists 'off' (no ABS).
    """
    straight = longest_straight(oval_quick)
    outcomes = {}
    for label in ("config", "setter"):
        config = {"cars": 1, "laps": 3, "weather": "sun", "seed": 1, "assists": "off"}
        if label == "config":
            config["brakeBias"] = 0.45
        session = Session(oval_quick, config)
        car = session.sim.cars[0]
        if label == "config":
            assert car.brake_bias == 0.45
            assert session.info()["brake_bias"] == 0.45
        else:
            assert session.set_brake_bias(0.30) == 0.45
            assert session.set_brake_bias(0.80) == 0.75
            assert car.brake_bias == 0.75
        car.place(straight["start"] + 300.0, 0.0, 70.0)
        session.sim.prev_s[0] = car.s
        for _ in range(60):
            session.step_once((0.3, 0.0, pursuit_steer(car), False, True))

        def step(thr, brk, steer, session=session):
            session.step_once((thr, brk, steer, False, True))

        outcomes[car.brake_bias] = first_lockup(car, step, pursuit_steer)[0]
    assert outcomes == {0.45: "rear", 0.75: "front"}


# --------------------------------------------------------------------------
# skidpad (constant radius) and lateral limit
# --------------------------------------------------------------------------


def skidpad_run(table, speed, seconds=10.0, window=3.0):
    """Hold ``speed`` on the circle with pure pursuit; statistics of the last
    ``window`` seconds. The car starts on the centre line at speed, tyres at
    the blanket temperature (default state)."""
    track = fresh_track(table)
    env = calm_env()
    car = new_car(track)
    car.place(0.0, 0.0, speed)
    hold = SpeedHold(kp=0.3, ki=0.5, start=0.3)
    log = []
    steps = int(round(seconds / DT))
    first = steps - int(round(window / DT))
    for k in range(steps):
        car.step(DT, hold(car, speed), 0.0, pursuit_steer(car), False, env)
        if k >= first:
            log.append((car.u, car.v, car.n, car.ay_spec, tuple(car.fz), car.downforce))
    return {
        "u": statistics.fmean(x[0] for x in log),
        "v_total": statistics.fmean(math.hypot(x[0], x[1]) for x in log),
        "n": statistics.fmean(x[2] for x in log),
        "n_max": max(abs(x[2]) for x in log),
        "ay": statistics.fmean(x[3] for x in log),
        "fz": [statistics.fmean(x[4][i] for x in log) for i in range(4)],
        "downforce": statistics.fmean(x[5] for x in log),
        "half_width": track.half_width[0],
        "car": car,
    }


def holds_circle(run):
    """The car holds the circle when it stays on the asphalt (its centre at
    least half a car width inside the edge) through the evaluation window.
    Above the limit it settles on a larger radius out on the run-off."""
    return run["n_max"] + 0.5 * GE.width <= run["half_width"]


_LIMIT_CACHE = {}


def dynamic_limit(bank_deg):
    """Highest steady speed (1 % steps of the QSS limit) the car holds.

    Steady-speed runs from 95 % of the QSS speed upward; the limit is the
    last percentage before the first run that does not hold the circle.
    Returns (limit_speed, limit_fraction, qss_speed).
    """
    if bank_deg in _LIMIT_CACHE:
        return _LIMIT_CACHE[bank_deg]
    table = analyze(circle_document(SKIDPAD_RADIUS, bank_deg=bank_deg), "quick")["physics_table"]
    v_qss = envelope().max_corner_speed(-1.0 / SKIDPAD_RADIUS, math.radians(bank_deg))
    last = None
    for pct in range(95, 116):
        if not holds_circle(skidpad_run(table, v_qss * pct / 100.0)):
            break
        last = pct
    else:
        pytest.fail(f"no lateral limit found up to 115 % of QSS (bank {bank_deg} deg)")
    assert last is not None, f"car cannot hold 95 % of the QSS speed (bank {bank_deg} deg)"
    result = (v_qss * last / 100.0, last / 100.0, v_qss)
    _LIMIT_CACHE[bank_deg] = result
    return result


@pytest.fixture(scope="module")
def skidpad85(circle100):
    v_qss = envelope().max_corner_speed(-1.0 / SKIDPAD_RADIUS)
    return skidpad_run(circle100["physics_table"], 0.85 * v_qss)


@pytest.mark.verification
def test_skidpad_holds_circle_at_85_percent_of_qss(skidpad85):
    """At 85 % of the QSS limit the car holds the R = 100 m circle.

    * |n| stays below 1.5 m: pure pursuit tracks a circle with no geometric
      offset, the residual is the understeer offset of the steady loop
      (15 % of the 10 m half width is a generous bound).
    * The measured lateral specific force equals the kinematic centripetal
      acceleration V^2 / R_path (R_path = R + n, n > 0 is outward on this
      right-hand circle) within 5 %: the spline circle's radius ripples by
      about +-2.5 % between its 32 control points and the body slip angle
      tilts the force by cos(beta).
    """
    run = skidpad85
    assert run["n_max"] < 1.5, f"max |n| {run['n_max']:.2f} m"
    r_path = SKIDPAD_RADIUS + run["n"]
    a_kin = run["v_total"] ** 2 / r_path
    # Right-hand turn: the lateral force points to the right (negative y).
    assert run["ay"] < 0.0
    assert -run["ay"] == pytest.approx(a_kin, rel=0.05), f"ay {-run['ay']:.2f} vs V^2/R {a_kin:.2f}"


@pytest.mark.verification
def test_skidpad_dynamic_limit_close_to_qss():
    """The dynamic lateral limit on the flat R = 100 m skidpad is 90-105 % of
    the QSS limit (``PerformanceEnvelope.max_corner_speed(-1/100)``).

    Both models share the tyre, aero and mass parameters. The QSS model
    assumes peak tyre grip and the static ride heights; the dynamic car runs
    on tyres near their blanket temperature (below the optimum) but sits
    lower at speed (more ground-effect downforce), and its limit includes
    transient load transfer and the driver loop. Each steady-speed run
    lasts 10 s (about one lap) and is judged on its last 3 s.
    """
    v_limit, fraction, v_qss = dynamic_limit(0.0)
    assert 0.90 <= fraction <= 1.05, f"dynamic {v_limit:.2f} m/s = {fraction:.2f} x QSS {v_qss:.2f} m/s"


# --------------------------------------------------------------------------
# load transfer and aero load
# --------------------------------------------------------------------------


@pytest.mark.verification
def test_braking_load_transfer(oval_quick):
    """Braking moves load to the front: at brake onset front loads rise and
    rear loads fall; throughout the stop the loads relative to static weight
    plus aero load are positive at the front and negative at the rear; and
    the front-axle transfer equals m (-a_x) h / L.

    In quasi-steady pitch the spring, link (anti-dive) and unsprung paths
    together transfer exactly m a_x h / L (h = CG height of the whole car);
    the suspension uses the previous step's specific force, which is what
    the reference uses too (so the loads only move from the second braking
    step on). Aero load at the current ride heights and the
    static weight split are removed first. Tolerance 2 % after the 0.3 s
    pitch transient (the deceleration itself changes slowly with drag).
    """
    straight = longest_straight(oval_quick)
    env = calm_env()
    rho = env.atmosphere.density
    car = new_car(fresh_track(oval_quick["physics_table"]))
    car.place(straight["start"] + 300.0, 0.0, 70.0)
    for _ in range(120):
        car.step(DT, 0.35, 0.0, pursuit_steer(car), False, env)
    coast_front = car.fz[FL] + car.fz[FR]
    coast_rear = car.fz[RL] + car.fz[RR]
    m = car.mass
    f = GE.front_weight_fraction
    checked = 0
    for k in range(int(0.6 / DT)):
        sus = car.suspension
        cla_f, cla_r, _ = car.aero.coefficients(sus.ride_front, sus.ride_rear, False, 0.0, 1.0, 1.0)
        q = 0.5 * rho * car.u * car.u
        ax_prev = car.ax_spec
        car.step(DT, 0.0, 0.5, pursuit_steer(car), False, env)
        front = car.fz[FL] + car.fz[FR]
        rear = car.fz[RL] + car.fz[RR]
        front_transfer = front - q * cla_f - m * GRAVITY * f
        rear_transfer = rear - q * cla_r - m * GRAVITY * (1.0 - f)
        if k >= 1:  # the first braking step still sees the coasting a_x (one-step lag)
            assert front_transfer > 0.0 > rear_transfer
        if 1 <= k <= 5:
            # At brake onset (speed, hence aero load, still unchanged) the raw
            # loads move: front up, rear down. Later the falling aero load
            # lowers both, so only the transfer is compared.
            assert front > coast_front
            assert rear < coast_rear
        if k * DT >= 0.3:
            expected = m * (-ax_prev) * GE.cg_height / GE.wheelbase
            assert front_transfer == pytest.approx(expected, rel=0.02)
            checked += 1
    assert checked > 30
    assert car.ax_spec < -2.0 * GRAVITY  # it really was hard braking


@pytest.mark.verification
def test_cornering_load_transfer(skidpad85):
    """Steady cornering: outer (left, on this right-hand circle) loads exceed
    inner loads on both axles; the total lateral transfer satisfies the roll
    moment balance sum_j dFz_j t_j = m a_y h within 3 %; and the four loads
    carry the weight plus downforce (vertical equilibrium) within 1 %.

    The 3 % covers the destabilising roll-angle term m_s g (h_s - h_ra) phi
    and the small mismatch between the sprung-mass split and the roll-axis
    interpolation of the model.
    """
    run = skidpad85
    fz = run["fz"]
    car = run["car"]
    assert fz[FL] > fz[FR] and fz[RL] > fz[RR]
    d_front = 0.5 * (fz[FL] - fz[FR])
    d_rear = 0.5 * (fz[RL] - fz[RR])
    moment = d_front * GE.track_front + d_rear * GE.track_rear
    expected = car.mass * abs(run["ay"]) * GE.cg_height
    assert moment == pytest.approx(expected, rel=0.03)
    assert sum(fz) == pytest.approx(car.mass * GRAVITY + run["downforce"], rel=0.01)


@pytest.mark.verification
def test_downforce_grows_quadratically_with_speed(oval_quick):
    """Steady downforce at 30 and 60 m/s scales as (v2/v1)^2 within 12 %.

    F = 0.5 rho ClA(h) V^2: the only departure from the square law is the
    ground effect (ClA rises as the car sits lower at speed). The excess
    must therefore be positive and equal the ClA ratio at the measured ride
    heights (1e-4: averages of slowly varying factors over 1 s).
    """
    straight = longest_straight(oval_quick)
    env = calm_env()
    stats = {}
    for target in (30.0, 60.0):
        car = new_car(fresh_track(oval_quick["physics_table"]))
        car.place(straight["start"] + 200.0, 0.0, target)
        hold = SpeedHold()
        log = []
        for k in range(int(3.0 / DT)):
            sus = car.suspension
            cla_f, cla_r, _ = car.aero.coefficients(sus.ride_front, sus.ride_rear, False, 0.0, 1.0, 1.0)
            u_before = car.u
            ride = 0.5 * (sus.ride_front + sus.ride_rear)
            car.step(DT, hold(car, target), 0.0, pursuit_steer(car), False, env)
            if k >= int(2.0 / DT):
                log.append((u_before, car.downforce, cla_f + cla_r, ride))
        stats[target] = [statistics.fmean(x[i] for x in log) for i in range(4)]
    (u1, d1, c1, h1), (u2, d2, c2, h2) = stats[30.0], stats[60.0]
    excess = (d2 / d1) / (u2 / u1) ** 2 - 1.0
    assert abs(excess) < 0.12, f"downforce ratio departs {excess:+.3f} from the square law"
    assert h2 < h1, "the car must sit lower at higher speed"
    assert excess > 0.0
    assert 1.0 + excess == pytest.approx(c2 / c1, rel=1e-4)


# --------------------------------------------------------------------------
# hills
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def hill_analysis():
    analysis = analyze(hill_oval_document(), "quick")
    assert analysis["valid"], analysis["errors"]
    return analysis


def _mid_of(grades, predicate, spacing):
    idx = [i for i, g in enumerate(grades) if predicate(g)]
    assert idx, "grade section not found"
    return 0.5 * (idx[0] + idx[-1]) * spacing


@pytest.mark.verification
def test_hill_climb_slower_than_descent(hill_analysis):
    """Same throttle (0.5) and entry speed (40 m/s): the car accelerates less
    uphill than downhill, and the difference of world-frame accelerations
    minus the difference of specific forces is the gravity term
    2 g sin(theta), tan(theta) = 0.08 (the document's 96 m over 1200 m),
    within 1 % (the car follows the centre line, so cos(dpsi) ~ 1)."""
    table = hill_analysis["physics_table"]
    grades = table["grade"]
    max_grade = max(abs(g) for g in grades)
    assert max_grade < 0.12
    s_up = _mid_of(grades, lambda g: g > 0.075, table["spacing"])
    s_down = _mid_of(grades, lambda g: g < -0.075, table["spacing"])
    env = calm_env()
    out = {}
    for label, s0 in (("up", s_up), ("down", s_down)):
        car = new_car(fresh_track(table))
        car.place(s0 - 20.0, 0.0, 40.0)
        for _ in range(30):
            car.step(DT, 0.5, 0.0, pursuit_steer(car), False, env)
        world, spec, grade = [], [], []
        for _ in range(60):
            car.step(DT, 0.5, 0.0, pursuit_steer(car), False, env)
            world.append(car.ax_world)
            spec.append(car.ax_spec)
            grade.append(car.track.sample(car.track_index, car.track_t, car.track.grade))
        out[label] = (statistics.fmean(world), statistics.fmean(spec), statistics.fmean(grade))
    up, down = out["up"], out["down"]
    assert up[2] == pytest.approx(0.08, rel=0.01) and down[2] == pytest.approx(-0.08, rel=0.01)
    assert up[0] < down[0], f"uphill {up[0]:.3f} m/s^2, downhill {down[0]:.3f} m/s^2"
    sin_theta = 0.08 / math.sqrt(1.0 + 0.08 ** 2)
    gravity_gap = (down[0] - up[0]) - (down[1] - up[1])
    assert gravity_gap == pytest.approx(2.0 * GRAVITY * sin_theta, rel=0.01)


@pytest.mark.verification
def test_brake_holds_car_on_slope(hill_analysis):
    """Held with the brake on the 8 % slope, the car creeps < 0.2 m in 5 s.

    Control experiment without brake: the car rolls back with
    a = -g (sin theta - C_rr cos theta) m / (m + sum I_w / r^2) (rotating
    wheels add effective mass; the launch clutch is open, so no engine
    inertia) within 2 % (aero drag at < 3.5 m/s and the low-speed carcass
    damping account for < 1 %). This shows the slope is strong enough to
    move a free car, so the hold is the brake's doing (Coulomb brake torque
    at standstill)."""
    table = hill_analysis["physics_table"]
    s_up = _mid_of(table["grade"], lambda g: g > 0.075, table["spacing"])
    env = calm_env()

    car = new_car(fresh_track(table))
    car.place(s_up, 0.0, 0.0)
    x0, z0 = car.x, car.z
    for _ in range(int(5.0 / DT)):
        car.step(DT, 0.0, 1.0, 0.0, False, env)
    creep = math.hypot(car.x - x0, car.z - z0)
    assert creep < 0.2, f"car crept {creep:.3f} m"

    car = new_car(fresh_track(table))
    car.place(s_up, 0.0, 0.0)
    speeds = []
    for _ in range(int(5.0 / DT)):
        car.step(DT, 0.0, 0.0, 0.0, False, env)
        speeds.append(car.u)
    assert not car.powertrain.clutch_locked
    grade = car.track.sample(car.track_index, car.track_t, car.track.grade)
    cos_t = 1.0 / math.sqrt(1.0 + grade * grade)
    sin_t = grade * cos_t
    rotating = 2.0 * GE.wheel_inertia_front / GE.tyre_radius_front ** 2
    rotating += 2.0 * GE.wheel_inertia_rear / GE.tyre_radius_rear ** 2
    crr = COMPOUNDS[COMPOUND].rolling_resistance
    m = car.mass
    a_expected = -GRAVITY * (sin_t - crr * cos_t) * m / (m + rotating)
    a_measured = (speeds[-1] - speeds[int(1.0 / DT) - 1]) / 4.0
    assert a_measured == pytest.approx(a_expected, rel=0.02)
    assert car.u < -2.0  # it really rolled back


# --------------------------------------------------------------------------
# numerical robustness
# --------------------------------------------------------------------------


@pytest.mark.verification
@pytest.mark.parametrize("weather,seed", [("sun", 11), ("rain", 12)])
def test_random_inputs_keep_states_bounded(oval_quick, weather, seed):
    """60 s of seeded, piecewise-constant random inputs (held 0.2-1.5 s).

    Every 10 s the car is re-placed at a random position and a random speed
    of 0-90 m/s so that low-speed spins, high-speed spins and barrier
    impacts are all exercised. Bounds: every state finite; speed and wheel
    surface speed below 130 m/s (above the top speed); normal loads >= 0
    (one-sided contact); wear within [0, 1]; pitch/roll below 0.1 rad (the
    bump stops limit them to a few hundredths); the car stays inside the
    barriers.
    """
    sim = Simulation(oval_quick["physics_table"], cars=1, laps=50, weather=weather)
    car = sim.cars[0]
    rng = random.Random(seed)
    length = sim.track.length
    max_barrier = sim.track.max_barrier
    hold = 0.0
    inputs = (0.0, 0.0, 0.0, False)
    episode = int(round(10.0 / DT))
    max_speed = 0.0
    for k in range(int(round(60.0 / DT))):
        if k % episode == 0:
            car.place(rng.uniform(0.0, length), rng.uniform(-5.0, 5.0), rng.uniform(0.0, 90.0))
            sim.prev_s[0] = car.s
        hold -= DT
        if hold <= 0.0:
            hold = rng.uniform(0.2, 1.5)
            brake = rng.random() if rng.random() < 0.3 else 0.0
            steer = rng.uniform(-1.0, 1.0) * rng.choice((0.02, 0.1, 0.4, 1.0))
            inputs = (rng.random(), brake, steer, rng.random() < 0.5)
        sim.step([inputs])
        sus = car.suspension
        state = (
            [car.x, car.y, car.z, car.psi, car.u, car.v, car.r, car.s, car.n, car.steer]
            + car.omega + car.fz + car.fx + car.fy + car.kappa + car.alpha
            + car.surface_t + car.core_t + car.brake_t + car.pressure + car.wear
            + [sus.z, sus.theta, sus.phi, sus.vz, sus.vtheta, sus.vphi]
            + [car.powertrain.rpm, car.powertrain.fuel, car.powertrain.ers_store, car.drag, car.downforce]
        )
        assert all_finite(state), f"non-finite state at t = {k * DT:.3f} s"
        speed = math.hypot(car.u, car.v)
        max_speed = max(max_speed, speed)
        assert speed < 130.0
        assert max(abs(w) * r for w, r in zip(car.omega, car.radius)) < 130.0
        assert min(car.fz) >= 0.0
        assert all(0.0 <= w <= 1.0 for w in car.wear)
        assert abs(sus.theta) < 0.1 and abs(sus.phi) < 0.1
        assert abs(car.n) <= max_barrier
    assert max_speed > 60.0  # high-speed episodes really happened


@pytest.mark.verification
def test_time_step_convergence(oval_quick):
    """The same 5 s open-loop manoeuvre (throttle 0.7, then brake 0.35, then
    throttle 0.3, with a 0.04 steer sine) stepped with dt = 1/120 and 1/240:
    final speed and distance agree within 2 % and the final lateral position
    within 0.5 m (0.25 % of the path). The integrator is first order in the
    body states, so halving dt changes results by O(dt)."""
    straight = longest_straight(oval_quick)

    def run(dt):
        env = calm_env()
        car = new_car(fresh_track(oval_quick["physics_table"]))
        car.place(straight["start"] + 150.0, 0.0, 40.0)
        for k in range(int(round(5.0 / dt))):
            t = k * dt
            if t < 2.5:
                thr, brk = 0.7, 0.0
            elif t < 4.0:
                thr, brk = 0.0, 0.35
            else:
                thr, brk = 0.3, 0.0
            car.step(dt, thr, brk, 0.04 * math.sin(math.pi * t), False, env)
        return car.u, car.distance, car.n

    u1, d1, n1 = run(1.0 / 120.0)
    u2, d2, n2 = run(1.0 / 240.0)
    assert u1 == pytest.approx(u2, rel=0.02)
    assert d1 == pytest.approx(d2, rel=0.02)
    assert abs(n1 - n2) < 0.5
    assert d1 > 150.0 and u1 > 20.0  # the manoeuvre really moved the car


# --------------------------------------------------------------------------
# tyre wear and temperature
# --------------------------------------------------------------------------


@pytest.mark.verification
def test_tyre_wear_follows_sliding_energy(oval_quick):
    """Wear grows with sliding: a burnout (front brakes hold the car, rear
    wheels spin under full throttle) wears the rear tyres > 100x faster than
    clean rolling at 30 m/s; and the burnout's rear wear equals
    wear_rate x 1e-6 x abrasion x sliding energy, the energy being summed
    from the step telemetry: |Fx (Vcx - R omega)| + |Fy Vcy| dt.

    The tread stays below temp_optimal + temp_window (no thermal wear
    multiplier) and the surface is dry; tolerance 1e-6 (round-off of the
    summation order).
    """
    table = oval_quick["physics_table"]
    straight = longest_straight(oval_quick)
    roughness = set(table["roughness"])
    assert len(roughness) == 1
    abrasion = 0.75 + 0.35 * roughness.pop()
    compound = COMPOUNDS[COMPOUND]
    env = calm_env()
    duration = 2.0
    steps = int(round(duration / DT))
    assert steps % 4 == 0  # wear is flushed every 4 steps (30 Hz slow loop)

    car = new_car(fresh_track(table), brake_bias=1.0)
    car.place(straight["start"] + 300.0, 0.0, 0.0)
    energy = [0.0, 0.0]
    max_surface = 0.0
    for _ in range(steps):
        u, v, r = car.u, car.v, car.r
        car.step(DT, 1.0, 1.0, 0.0, False, env)
        for j, i in enumerate((RL, RR)):
            xb = car.wx[i]
            yb = car.wy[i]
            vcx = u - r * yb
            vcy = v + r * xb
            slide = abs(car.fx[i] * (vcx - car.radius[i] * car.omega[i])) + abs(car.fy[i] * vcy)
            energy[j] += slide * DT
        max_surface = max(max_surface, car.surface_t[RL], car.surface_t[RR])
    assert max_surface < compound.temp_optimal + compound.temp_window
    assert car.omega[RL] * car.radius[RL] > car.u + 10.0  # sustained wheelspin
    for j, i in enumerate((RL, RR)):
        expected = compound.wear_rate * 1e-6 * abrasion * compound.dry_wear_multiplier * energy[j]
        assert car.wear[i] == pytest.approx(expected, rel=1e-6)
    burnout_rate = 0.5 * (car.wear[RL] + car.wear[RR]) / duration

    rolling = new_car(fresh_track(table))
    rolling.place(straight["start"] + 300.0, 0.0, 30.0)
    hold = SpeedHold()
    for _ in range(steps):
        rolling.step(DT, hold(rolling, 30.0), 0.0, pursuit_steer(rolling), False, env)
    rolling_rate = 0.5 * (rolling.wear[RL] + rolling.wear[RR]) / duration
    assert rolling_rate > 0.0
    assert burnout_rate > 100.0 * rolling_rate, f"burnout {burnout_rate:.3e}/s, rolling {rolling_rate:.3e}/s"


@pytest.mark.verification
def test_tyre_core_temperature_bounded_over_ai_laps(oval_quick):
    """Regression for the former carcass-heat runaway: over 120 s of fast AI
    laps on the oval the core temperature stays below
    temp_optimal + 2 temp_window, and its rise slows down (the windowed
    maximum rises less in 80-120 s than in 40-80 s), as a first-order
    thermal system approaching equilibrium must; the flex heat fades above
    the compound window."""
    session = Session(
        oval_quick,
        {"cars": 1, "laps": 20, "difficulty": 4, "weather": "sun", "seed": 7, "autopilot": True},
    )
    car = session.sim.cars[0]
    compound = car.tyre.compound
    limit = compound.temp_optimal + 2.0 * compound.temp_window
    window_max = [0.0, 0.0, 0.0]
    laps = 0
    steps = int(round(120.0 / DT))
    for k in range(steps):
        session.step_once((0.0, 0.0, 0.0, False, True))
        hottest = max(car.core_t)
        assert hottest < limit, f"core {hottest:.1f} degC at t = {k * DT:.1f} s"
        w = min(2, k * 3 // steps)
        window_max[w] = max(window_max[w], hottest)
        laps += sum(1 for e in session.drain_events() if e["type"] == "lap")
    assert laps >= 2, "the AI must have completed fast laps"
    assert window_max[2] - window_max[1] < window_max[1] - window_max[0], window_max


# --------------------------------------------------------------------------
# banking
# --------------------------------------------------------------------------


@pytest.mark.verification
def test_qss_corner_speed_increases_with_helpful_banking():
    """On the right-hand R = 100 m curve, raising the outside (positive bank)
    raises the QSS corner speed monotonically, adverse bank lowers it, and the
    model is mirror-symmetric: v(k, b) == v(-k, -b)."""
    env_qss = envelope()
    banks = (-8.0, -4.0, 0.0, 4.0, 8.0, 12.0)
    speeds = [env_qss.max_corner_speed(-1.0 / SKIDPAD_RADIUS, math.radians(b)) for b in banks]
    assert all(b > a for a, b in zip(speeds, speeds[1:])), speeds
    for b, v in zip(banks, speeds):
        mirrored = env_qss.max_corner_speed(1.0 / SKIDPAD_RADIUS, math.radians(-b))
        assert mirrored == pytest.approx(v, rel=1e-9)


@pytest.mark.verification
@pytest.mark.parametrize(
    "bank_deg",
    [
        0.0,
        pytest.param(
            8.0,
            marks=pytest.mark.xfail(
                strict=True, reason="BUG: in-plane lateral demand on a bank omits cos(bank) (limits.lateral_ok)"
            ),
        ),
        pytest.param(
            12.0,
            marks=pytest.mark.xfail(
                strict=True, reason="BUG: in-plane lateral demand on a bank omits cos(bank) (limits.lateral_ok)"
            ),
        ),
    ],
)
def test_qss_banked_corner_matches_point_mass_formula(bank_deg):
    """With load sensitivity and downforce removed, the QSS corner speed on a
    horizontal circle of radius R with bank b is the textbook point-mass
    result v^2 = g R (sin b + mu cos b) / (cos b - mu sin b).

    Decomposing the horizontal centripetal acceleration v^2/R and gravity on
    the road plane: normal g cos b + (v^2/R) sin b, in-plane
    (v^2/R) cos b - g sin b; friction limit: in-plane <= mu normal.
    Tolerance 1e-6 (the bisection resolves ~1e-10).
    """
    params = replace(GE, cla_front=1e-9, cla_rear=1e-9)  # ~zero downforce, valid aero split
    env_qss = PerformanceEnvelope(params, COMPOUND, calm_atmosphere(), fuel=FUEL)
    env_qss.tyre = TyreModel(replace(COMPOUNDS[COMPOUND], load_sensitivity=0.0))
    mu = COMPOUNDS[COMPOUND].mu_peak
    b = math.radians(bank_deg)
    expected = math.sqrt(
        GRAVITY * SKIDPAD_RADIUS * (math.sin(b) + mu * math.cos(b)) / (math.cos(b) - mu * math.sin(b))
    )
    v = env_qss.max_corner_speed(-1.0 / SKIDPAD_RADIUS, b)
    assert v == pytest.approx(expected, rel=1e-6)


@pytest.mark.verification
def test_dynamic_skidpad_limit_increases_with_banking():
    """The dynamic skidpad limit (same procedure as the flat test) rises with
    8 deg of helpful banking, as the QSS limit does."""
    v_flat, frac_flat, qss_flat = dynamic_limit(0.0)
    v_bank, frac_bank, qss_bank = dynamic_limit(8.0)
    assert qss_bank > qss_flat
    assert v_bank > v_flat, (
        f"flat {v_flat:.2f} m/s ({frac_flat:.2f} x QSS), 8 deg {v_bank:.2f} m/s ({frac_bank:.2f} x QSS)"
    )


@pytest.fixture(scope="module")
def banked8_table():
    analysis = analyze(circle_document(SKIDPAD_RADIUS, bank_deg=8.0), "quick")
    assert analysis["valid"], analysis["errors"]
    return analysis["physics_table"]


@pytest.mark.verification
@pytest.mark.parametrize("yaw", [0.0, 0.3, -0.7, math.pi / 2, 2.5])
def test_road_attitude_for_yawed_car_on_bank(banked8_table, yaw):
    """Vehicle.pitch_road / roll_road for a car yawed by ``yaw`` relative to
    the track on an 8 deg bank (positive pitch = nose down, positive roll =
    right side down).

    Exact attitude of a body lying on the road plane h = G t + tan(b) l with
    horizontal heading dpsi (yaw -> pitch -> roll): the forward axis rises by
    s_f = G cos dpsi + tan(b) sin dpsi, so pitch = -atan(s_f) exactly; the
    roll making the body up axis the plane normal is
    atan(s_l cos(theta)), s_l = tan(b) cos dpsi - G sin dpsi. The engine's
    atan(s_l) is exact whenever s_f or s_l vanishes and otherwise differs by
    at most |s_l| (1 - cos theta) (mean value theorem, atan' <= 1), i.e.
    second order (< 5e-4 rad here).
    """
    track = fresh_track(banked8_table)
    car = new_car(track)
    car.place(100.0, 0.0, 0.0)
    car.psi += yaw
    car.step(DT, 0.0, 1.0, 0.0, False, calm_env())
    bank = track.sample(car.track_index, car.track_t, track.bank)
    grade = track.sample(car.track_index, car.track_t, track.grade)
    assert bank == pytest.approx(math.radians(8.0), rel=1e-5)
    d = car.dpsi
    assert math.cos(d) == pytest.approx(math.cos(yaw), abs=1e-6)
    tan_b = math.tan(bank)
    s_f = grade * math.cos(d) + tan_b * math.sin(d)
    s_l = tan_b * math.cos(d) - grade * math.sin(d)
    theta = math.atan(s_f)
    roll_exact = math.atan(s_l * math.cos(theta))
    assert car.pitch_road == pytest.approx(-theta, abs=1e-12)
    bound = abs(s_l) * (1.0 - math.cos(theta))
    assert abs(car.roll_road - roll_exact) <= bound + 1e-12
    assert abs(car.roll_road - roll_exact) < 5e-4
    if yaw in (0.0, math.pi / 2):
        assert car.roll_road == pytest.approx(roll_exact, abs=1e-9)
    if yaw == 0.0:
        assert car.roll_road == pytest.approx(bank, abs=1e-12)
    if yaw == math.pi / 2:
        # Car sideways on the bank: all of the bank shows up as pitch.
        assert car.pitch_road == pytest.approx(-bank, abs=1e-9)


# --------------------------------------------------------------------------
# placement at speed
# --------------------------------------------------------------------------


@pytest.mark.verification
@pytest.mark.xfail(strict=True, reason="BUG: Vehicle.place(speed>0) leaves 1st gear engaged (rpm far above rev limit)")
def test_place_at_speed_engages_a_matching_gear(oval_quick):
    """Vehicle.place(s, n, speed) sets the wheel speeds to speed / radius; the
    gearbox must be consistent with them, i.e. the engine speed after the
    first step must not exceed the rev limit."""
    straight = longest_straight(oval_quick)
    car = new_car(fresh_track(oval_quick["physics_table"]))
    car.place(straight["start"] + 300.0, 0.0, 300.0 * KMH)
    car.step(DT, 0.3, 0.0, 0.0, False, calm_env())
    assert car.powertrain.rpm <= GE.rev_limit, f"rpm {car.powertrain.rpm:.0f} in gear {car.powertrain.gear}"

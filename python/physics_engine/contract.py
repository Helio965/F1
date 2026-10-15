"""Versioned data contract between the Python physics and the renderer.

The worker returns one flat float64 array per ``advance`` call:

    [HEADER..., PREVIOUS STATE (n_cars x CAR_FIELDS), CURRENT STATE (n_cars x CAR_FIELDS)]

``previous`` is the state one fixed step before ``current``; the renderer
interpolates with ``alpha`` (header) and never runs physics itself. The field
order is the contract: ``src/physics/contract.ts`` mirrors these lists and a
test (python/tests/test_contract.py) fails if they diverge. Any change must
bump CONTRACT_VERSION.

Units: metres, radians, seconds, m/s, degC, 0..1 fractions.
"""

CONTRACT_VERSION = 1

HEADER_FIELDS = (
    "contract_version",
    "time",
    "step",
    "n_cars",
    "car_fields",
    "finished",
    "player_position",
    "alpha",
    "surface_version",
    "events_pending",
    "player_reverse",
    "physics_ms",
)

CAR_FIELDS = (
    "id",
    "x",
    "y",
    "z",
    "heading",
    "pitch",
    "roll",
    "speed",
    "u",
    "v",
    "yaw_rate",
    "steer_angle",
    "gear",
    "rpm",
    "throttle",
    "brake",
    "lap",
    "lap_time",
    "best_lap",
    "last_lap",
    "progress",
    "lateral",
    "finished",
    "position",
    "fuel_pct",
    "tyre_wear",
    "damage",
    "drs_open",
    "drs_allowed",
    "ers_pct",
    "water_film",
    "downforce",
    "spin_fl",
    "spin_fr",
    "spin_rl",
    "spin_rr",
    "susp_fl",
    "susp_fr",
    "susp_rl",
    "susp_rr",
    "tyre_t_fl",
    "tyre_t_fr",
    "tyre_t_rl",
    "tyre_t_rr",
    "wear_fl",
    "wear_fr",
    "wear_rl",
    "wear_rr",
    "slip_fl",
    "slip_fr",
    "slip_rl",
    "slip_rr",
    "surface_fl",
    "surface_fr",
    "surface_rl",
    "surface_rr",
    "brake_t_front",
    "brake_t_rear",
)

HEADER_SIZE = len(HEADER_FIELDS)
CAR_SIZE = len(CAR_FIELDS)


def describe():
    return {
        "version": CONTRACT_VERSION,
        "header": list(HEADER_FIELDS),
        "car": list(CAR_FIELDS),
    }


def car_state(car, tracker, position):
    """Flat list (CAR_FIELDS order) for one car."""
    sus = car.suspension
    pt = car.powertrain
    p = car.p
    ers = pt.ers_store / p.ers_battery if p.ers_battery > 0.0 else 0.0
    return [
        float(car.id),
        car.x,
        car.y,
        car.z,
        car.psi,
        sus.theta + car.pitch_road,
        sus.phi + car.roll_road,
        car.speed,
        car.u,
        car.v,
        car.r,
        car.steer,
        float(pt.gear),
        pt.rpm,
        car.throttle,
        car.brake,
        float(tracker.lap),
        tracker.lap_time,
        -1.0 if tracker.best_lap is None else tracker.best_lap,
        -1.0 if tracker.last_lap is None else tracker.last_lap,
        tracker.progress,
        car.n,
        1.0 if tracker.finished else 0.0,
        float(position),
        100.0 * pt.fuel / p.fuel_capacity,
        25.0 * (car.wear[0] + car.wear[1] + car.wear[2] + car.wear[3]),
        100.0 * car.damage,
        1.0 if car.drs_open else 0.0,
        1.0 if car.drs_allowed else 0.0,
        100.0 * ers,
        max(car.film),
        car.downforce,
        car.spin[0],
        car.spin[1],
        car.spin[2],
        car.spin[3],
        sus.deflection[0],
        sus.deflection[1],
        sus.deflection[2],
        sus.deflection[3],
        car.surface_t[0],
        car.surface_t[1],
        car.surface_t[2],
        car.surface_t[3],
        car.wear[0],
        car.wear[1],
        car.wear[2],
        car.wear[3],
        car.slip[0],
        car.slip[1],
        car.slip[2],
        car.slip[3],
        float(car.surface[0]),
        float(car.surface[1]),
        float(car.surface[2]),
        float(car.surface[3]),
        0.5 * (car.brake_t[0] + car.brake_t[1]),
        0.5 * (car.brake_t[2] + car.brake_t[3]),
    ]

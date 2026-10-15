"""Multi-car simulation: fixed-step integration, contacts, race control,
slipstream, DRS eligibility and the evolving wet surface.

All cars - player and AI - are :class:`Vehicle` instances with identical
physics. Controllers (player input filters, AI drivers) live outside this
module and only produce (throttle, brake, steer, drs) tuples.
"""

import math

from .aero import wake_factors
from .collisions import car_barrier, car_car
from .environment import Atmosphere
from .race import RaceTracker, standings
from .track import TrackModel
from .vehicle import Vehicle
from .vehicles import DEFAULT_VEHICLE, VEHICLES

FIXED_DT = 1.0 / 120.0
GRID_SPACING = 8.0
DRS_MIN_STRAIGHT = 250.0
DRS_GAP_SECONDS = 1.0
DRS_FROM_LAP = 3


class Environment:
    __slots__ = ("atmosphere", "wind")

    def __init__(self, atmosphere):
        self.atmosphere = atmosphere
        self.wind = atmosphere.wind_vector()


def default_compound(params, weather):
    if weather == "rain" and "wet" in params.compounds:
        return "wet"
    if weather in ("light-rain", "drying") and "intermediate" in params.compounds:
        return "intermediate"
    return params.default_compound


class Simulation:
    def __init__(
        self,
        track_data,
        cars=2,
        laps=3,
        weather="sun",
        vehicle_id=DEFAULT_VEHICLE,
        compound=None,
        altitude=0.0,
        straights=None,
        fuel=None,
    ):
        self.track = TrackModel(track_data)
        self.atmosphere = Atmosphere(weather, altitude=altitude)
        self.track.surface.initialise(self.atmosphere, self.atmosphere.initial_wetness())
        self.env = Environment(self.atmosphere)
        self.params = VEHICLES.get(vehicle_id, VEHICLES[DEFAULT_VEHICLE])
        self.laps = max(1, min(99, int(laps)))
        count = max(1, min(10, int(cars)))
        length = self.track.length
        if fuel is None:
            fuel = min(self.params.fuel_capacity, 3.0 + 0.45 * length / 1000.0 * (self.laps + 1))
        compound = compound or default_compound(self.params, weather)
        self.cars = []
        self.trackers = []
        half = self.track.half_width[0]
        lane = min(2.4, half * 0.35)
        for i in range(count):
            behind = GRID_SPACING + (i // 2) * GRID_SPACING
            lateral = lane if i % 2 else -lane
            car = Vehicle(self.params, self.track, car_id=i, compound=compound, fuel=fuel)
            car.place(length - behind, lateral)
            self.cars.append(car)
            self.trackers.append(RaceTracker(i, -behind, self.laps))
        self.time = 0.0
        self.steps = 0
        self.events = []
        self.finished = False
        self.drs_zones = []
        for s in straights or []:
            if s["length"] >= DRS_MIN_STRAIGHT:
                self.drs_zones.append((float(s["start"]) + 40.0, float(s["end"]) - 60.0))
        self.drs_granted = [False] * count
        self.collision_impulse = [0.0] * count
        self.prev_s = [car.s for car in self.cars]

    # ------------------------------------------------------------------ step
    def step(self, inputs, dt=FIXED_DT):
        cars = self.cars
        env = self.env
        for i, car in enumerate(cars):
            throttle, brake, steer, drs = inputs[i]
            car.step(dt, throttle, brake, steer, drs, env)
        self._contacts()
        track = self.track
        self.time += dt
        self.steps += 1
        for i, car in enumerate(cars):
            idx, t, s, n, _ = track.project(car.x, car.z, car.track_index)
            car.track_index, car.track_t, car.s, car.n = idx, t, s, n
            car.proj_fresh = True
            tracker = self.trackers[i]
            if not tracker.finished:
                crossed = tracker.update(
                    self.prev_s[i],
                    s,
                    track.length,
                    car.u,
                    n,
                    track.half_width[idx],
                    car.p.width,
                    dt,
                    self.time,
                )
                if crossed:
                    car.powertrain.new_lap()
                    if tracker.lap_events:
                        lap, lap_time, clean = tracker.lap_events.pop()
                        self.events.append(
                            {"type": "lap", "car": i, "lap": lap, "time": round(lap_time, 4), "clean": clean}
                        )
                    if tracker.finished:
                        self.events.append({"type": "finish", "car": i, "time": round(self.time, 4)})
            self.prev_s[i] = s
        self._aero_interaction()
        self._drs()
        track.surface.update(dt, self.atmosphere)
        self.finished = self.trackers[0].finished

    def _contacts(self):
        cars = self.cars
        for i, car in enumerate(cars):
            j = car_barrier(car)
            if j > 0.0:
                self.collision_impulse[i] += j
                if j / car.mass > 1.0:
                    self.events.append(
                        {"type": "barrier", "car": i, "dv": round(j / car.mass, 2), "x": round(car.x, 2), "z": round(car.z, 2)}
                    )
        n = len(cars)
        length = self.track.length
        for a in range(n):
            ca = cars[a]
            for b in range(a + 1, n):
                cb = cars[b]
                ds = abs(ca.s - cb.s)
                if ds > 0.5 * length:
                    ds = length - ds
                if ds > 9.0:
                    continue
                j = car_car(ca, cb)
                if j > 0.0:
                    self.collision_impulse[a] += j
                    self.collision_impulse[b] += j
                    dv = j / min(ca.mass, cb.mass)
                    if dv > 0.5:
                        self.events.append(
                            {
                                "type": "contact",
                                "cars": [a, b],
                                "dv": round(dv, 2),
                                "x": round(0.5 * (ca.x + cb.x), 2),
                                "z": round(0.5 * (ca.z + cb.z), 2),
                            }
                        )

    def _aero_interaction(self):
        cars = self.cars
        length = self.track.length
        for follower in cars:
            drag = 1.0
            down = 1.0
            for leader in cars:
                if leader is follower:
                    continue
                gap = leader.s - follower.s
                if gap < 0.0:
                    gap += length
                if gap > 80.0:
                    continue
                fd, fdn = wake_factors(gap, leader.n - follower.n, leader.p.width)
                if fd < drag:
                    drag = fd
                if fdn < down:
                    down = fdn
            follower.wake_drag = drag
            follower.wake_down = down

    def _drs(self):
        if not self.drs_zones or not self.params.drs:
            return
        wet = self.atmosphere.rain_rate_mm_h > 0.0 or self.track.surface.average_wetness() > 0.3
        cars = self.cars
        length = self.track.length
        for i, car in enumerate(cars):
            allowed = False
            if not wet and self.trackers[i].lap >= DRS_FROM_LAP:
                for start, end in self.drs_zones:
                    rel = (car.s - start) % length
                    span = (end - start) % length
                    if rel <= span:
                        if rel < 3.0:  # detection at the zone start
                            self.drs_granted[i] = self._gap_ahead_seconds(i) < DRS_GAP_SECONDS
                        allowed = self.drs_granted[i]
                        break
                else:
                    self.drs_granted[i] = False
            car.drs_allowed = allowed

    def _gap_ahead_seconds(self, index):
        me = self.trackers[index]
        best = float("inf")
        for t in self.trackers:
            if t is me:
                continue
            gap = t.credited - me.credited
            if 0.0 < gap < best:
                best = gap
        speed = max(self.cars[index].u, 5.0)
        return best / speed

    # ------------------------------------------------------------- queries
    def standings(self):
        return [t.car_id for t in standings(self.trackers)]

    def position_of(self, car_id):
        return self.standings().index(car_id) + 1

    def reset_car(self, index):
        """Put a car back on the track at its current progress (player 'R' key)."""
        car = self.cars[index]
        car.place(car.s, 0.0, 0.0)
        self.prev_s[index] = car.s

    def drain_events(self):
        events = self.events
        self.events = []
        return events


def drive_constant(sim, steps, throttle=0.0, brake=0.0, steer=0.0):
    """Helper for tests: same input for every car."""
    inputs = [(throttle, brake, steer, False)] * len(sim.cars)
    for _ in range(steps):
        sim.step(inputs)
    return sim

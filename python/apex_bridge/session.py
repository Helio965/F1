"""A race session: simulation + AI drivers + player controller + snapshots.

Time handling: the caller (Web Worker) passes the *wall-clock* time elapsed
since its previous call; the session accumulates it and executes whole fixed
steps (1/120 s). At most ``MAX_STEPS_PER_CALL`` steps run per call, so a
stalled tab cannot trigger an unbounded catch-up spiral (the excess time is
dropped, the simulation simply runs slower than real time for that frame).
Rendering frame rate never changes the physics step.
"""

import json
import time

from physics_engine.assists import PlayerController
from physics_engine.contract import CAR_SIZE, HEADER_SIZE, car_state, describe
from physics_engine.contract import CONTRACT_VERSION
from physics_engine.simulation import FIXED_DT, Simulation
from physics_engine.vehicles import VEHICLES, DEFAULT_VEHICLE, vehicle_summary
from racing_ai.difficulty import skill
from racing_ai.driver import AIDriver, Perception
from racing_ai.planner import SpeedPlan

MAX_STEPS_PER_CALL = 24  # 0.2 s of simulated time


class Session:
    def __init__(self, analysis, config):
        if not analysis.get("valid", False) or "physics_table" not in analysis:
            raise ValueError("A pista precisa de uma análise válida antes da corrida.")
        self.config = config
        cars = int(max(1, min(10, config.get("cars", 2))))
        laps = int(max(1, min(99, config.get("laps", 3))))
        self.difficulty = int(max(0, min(4, config.get("difficulty", 2))))
        weather = config.get("weather", "sun")
        vehicle_id = config.get("vehicle", DEFAULT_VEHICLE)
        if vehicle_id not in VEHICLES:
            vehicle_id = DEFAULT_VEHICLE
        self.seed = int(config.get("seed", 1)) & 0xFFFFFFFF
        self.training = bool(config.get("training", False))
        t0 = time.perf_counter()
        self.sim = Simulation(
            analysis["physics_table"],
            cars=cars,
            laps=laps,
            weather=weather,
            vehicle_id=vehicle_id,
            compound=config.get("compound"),
            altitude=analysis.get("environment", {}).get("altitude", 0.0),
            straights=analysis.get("straights"),
        )
        offsets = (analysis.get("racing_line") or {}).get("offset")
        sim = self.sim
        player = sim.cars[0]
        self.plan = SpeedPlan(sim.track, sim.params, player.compound_id, sim.atmosphere, offsets)
        self.plan.refresh_grip(sim.track.surface, player.pressure[0])
        self.setup_ms = (time.perf_counter() - t0) * 1000.0
        self.perception = Perception(sim.cars)
        autopilot = bool(config.get("autopilot", False))
        self.drivers = [None] * cars
        for i in range(1 if not autopilot else 0, cars):
            self.drivers[i] = AIDriver(i, sim, self.plan, self.difficulty, self.seed)
        self.player = PlayerController(config.get("assists", "full"))
        brake_bias = config.get("brakeBias")
        if brake_bias is not None:
            self.set_brake_bias(float(brake_bias))
        self.accumulator = 0.0
        self.grip_timer = 0.0
        self.prev = self._state()
        self.curr = list(self.prev)
        self.step_times = []
        self.ai_times = []
        self.last_physics_ms = 0.0

    # ------------------------------------------------------------- info
    def info(self):
        sim = self.sim
        return {
            "contract": describe(),
            "vehicle": vehicle_summary(sim.params),
            "compound": sim.cars[0].compound_id,
            "cars": len(sim.cars),
            "laps": sim.laps,
            "difficulty": skill(self.difficulty).name,
            "weather": sim.atmosphere.preset,
            "air_density": round(sim.atmosphere.density, 4),
            "fixed_dt": FIXED_DT,
            "surface": {
                "segments": sim.track.surface.segments,
                "lanes": sim.track.surface.lanes,
                "segment_length": sim.track.surface.seg_length,
            },
            "setup_ms": round(self.setup_ms, 2),
            "brake_bias": sim.cars[0].brake_bias,
        }

    # ------------------------------------------------------------ state
    def _state(self):
        sim = self.sim
        order = sim.standings()
        positions = [0] * len(sim.cars)
        for rank, cid in enumerate(order, 1):
            positions[cid] = rank
        out = []
        for i, car in enumerate(sim.cars):
            out.extend(car_state(car, sim.trackers[i], positions[i]))
        return out

    def _header(self, alpha):
        sim = self.sim
        order = sim.standings()
        return [
            float(CONTRACT_VERSION),
            sim.time,
            float(sim.steps),
            float(len(sim.cars)),
            float(CAR_SIZE),
            1.0 if sim.finished else 0.0,
            float(order.index(0) + 1),
            alpha,
            float(sim.track.surface.version),
            float(len(sim.events)),
            1.0 if self.player.reverse else 0.0,
            self.last_physics_ms,
        ]

    # ------------------------------------------------------------ step
    def step_once(self, player_input):
        sim = self.sim
        t0 = time.perf_counter()
        inputs = []
        for i, car in enumerate(sim.cars):
            driver = self.drivers[i]
            if driver is None:
                throttle, brake, steer, drs, analog = player_input
                inputs.append(self.player.process(car, FIXED_DT, throttle, brake, steer, drs, analog))
            else:
                inputs.append(driver.control(self.perception, FIXED_DT))
        t1 = time.perf_counter()
        sim.step(inputs, FIXED_DT)
        self.perception.record(sim.cars)
        self.grip_timer += FIXED_DT
        if self.grip_timer >= 0.5:
            self.grip_timer = 0.0
            self.plan.refresh_grip(sim.track.surface, sim.cars[0].pressure[0])
        t2 = time.perf_counter()
        self.ai_times.append((t1 - t0) * 1000.0)
        self.step_times.append((t2 - t0) * 1000.0)
        if len(self.step_times) > 4096:
            del self.step_times[:2048]
            del self.ai_times[:2048]

    def advance(self, elapsed, throttle, brake, steer, drs, analog):
        """Run the fixed steps covered by ``elapsed`` seconds; return a snapshot."""
        if elapsed < 0.0 or elapsed != elapsed:
            elapsed = 0.0
        self.accumulator += min(elapsed, MAX_STEPS_PER_CALL * FIXED_DT)
        t0 = time.perf_counter()
        player_input = (throttle, brake, steer, bool(drs), bool(analog))
        steps = int(self.accumulator / FIXED_DT + 1e-9)
        if steps > MAX_STEPS_PER_CALL:
            steps = MAX_STEPS_PER_CALL
        if self.sim.finished and not self.training:
            steps = 0
            self.accumulator = 0.0
        for k in range(steps):
            if k == steps - 1:
                self.prev = self._state()
            self.step_once(player_input)
            self.accumulator -= FIXED_DT
        if steps:
            self.curr = self._state()
        if self.accumulator >= FIXED_DT:
            self.accumulator = FIXED_DT * 0.999
        self.last_physics_ms = (time.perf_counter() - t0) * 1000.0
        alpha = self.accumulator / FIXED_DT
        return self._header(alpha) + self.prev + self.curr

    def run_steps(self, steps, player_input=(0.0, 0.0, 0.0, False, True)):
        for _ in range(steps):
            self.step_once(player_input)
        self.prev = self.curr = self._state()

    # --------------------------------------------------------- commands
    def reset_player(self):
        self.sim.reset_car(0)
        self.player.reverse = False

    def set_brake_bias(self, bias):
        bias = max(0.45, min(0.75, bias))
        self.sim.cars[0].brake_bias = bias
        return bias

    def drain_events(self):
        return self.sim.drain_events()

    def surface_grid(self):
        surface = self.sim.track.surface
        return surface.version, surface.segments, surface.lanes, surface.lane_grid()

    def metrics(self):
        def pct(values, q):
            if not values:
                return 0.0
            ordered = sorted(values)
            return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))]

        steps = self.step_times
        ai = self.ai_times
        n_cars = len(self.sim.cars)
        mean = sum(steps) / len(steps) if steps else 0.0
        return {
            "samples": len(steps),
            "step_mean_ms": round(mean, 4),
            "step_p95_ms": round(pct(steps, 0.95), 4),
            "step_p99_ms": round(pct(steps, 0.99), 4),
            "step_max_ms": round(max(steps) if steps else 0.0, 4),
            "ai_mean_ms": round(sum(ai) / len(ai) if ai else 0.0, 4),
            "per_car_ms": round(mean / n_cars, 4),
            "realtime_load": round(mean / (FIXED_DT * 1000.0), 4),
            "cars": n_cars,
        }


def snapshot_layout():
    return {"header": HEADER_SIZE, "car": CAR_SIZE}


def to_json(data):
    return json.dumps(data, separators=(",", ":"))

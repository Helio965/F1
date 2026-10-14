import type {
  CarState,
  Circuit,
  CircuitGeometry,
  DriverInput,
  RaceSettings,
  TrackSample,
} from "../core/types";
import { sampleCircuit } from "../circuits/geometry";

const WHEELBASE = 3.6;
const SECTORS = 20;
const clamp = (v: number, min: number, max: number) =>
  Math.max(min, Math.min(max, v));
const angleDelta = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));
const wrap = (v: number, n: number) => ((v % n) + n) % n;
const COLORS = [
  "#ff512f",
  "#00d4e8",
  "#ffc940",
  "#906bff",
  "#47dd94",
  "#f978c1",
  "#d9e2eb",
  "#3d91ff",
  "#e39c45",
  "#b8e251",
];
interface Projection {
  x: number;
  y: number;
  z: number;
  tx: number;
  tz: number;
  progress: number;
  lateral: number;
  distance: number;
  index: number;
}
interface Dynamics {
  steering: number;
  lateralVelocity: number;
  projection: Projection;
  creditedDistance: number;
  sinceLine: number;
  completedLaps: number;
  started: boolean;
  nextSector: number;
  validLap: boolean;
  finishTime: number;
  reaction: number;
  aiInput: DriverInput;
  lane: number;
}

/** Deterministic, accessible bicycle model; not a professional engineering simulator.
 * Metres, m/s, heading zero faces +z and positive steering turns +x.
 * Tyre grip, downforce, fuel, wear and impacts are deliberately simplified.
 */
export class RaceSimulation {
  readonly circuit: Circuit;
  readonly settings: RaceSettings;
  readonly geometry: CircuitGeometry;
  readonly cars: CarState[];
  time = 0;
  finished = false;
  private dynamics: Dynamics[] = [];

  constructor(circuit: Circuit, settings: RaceSettings) {
    this.circuit = circuit;
    this.settings = {
      cars: Math.round(clamp(settings.cars, 2, 10)),
      laps: Math.round(clamp(settings.laps, 1, 99)),
      difficulty: Math.round(clamp(settings.difficulty, 0, 4)),
      weather: settings.weather,
    };
    this.geometry = sampleCircuit(circuit);
    if (this.geometry.length < 30 || this.geometry.samples.length < 3)
      throw new Error(
        "O circuito precisa de pelo menos 30 metros para iniciar uma corrida.",
      );
    this.cars = Array.from({ length: this.settings.cars }, (_, id) => {
      const behindLine = 8 + Math.floor(id / 2) * 8;
      const p = this.at(this.geometry.length - behindLine);
      const lane = (id % 2 ? 1 : -1) * Math.min(2.2, circuit.width * 0.2);
      const x = p.x + p.tz * lane,
        z = p.z - p.tx * lane;
      const projection = this.project(x, z);
      this.dynamics.push({
        steering: 0,
        lateralVelocity: 0,
        projection,
        creditedDistance: -behindLine,
        sinceLine: 0,
        completedLaps: 0,
        started: false,
        nextSector: 1,
        validLap: true,
        finishTime: Infinity,
        reaction: 0,
        aiInput: { throttle: 1, brake: 0, steer: 0, drs: false },
        lane,
      });
      return {
        id,
        name: id === 0 ? "Você" : `Piloto ${id + 1}`,
        color: COLORS[id],
        x,
        y: p.y + 0.35,
        z,
        heading: Math.atan2(p.tx, p.tz),
        speed: 0,
        gear: 1,
        rpm: 4500,
        lap: 1,
        lapTime: 0,
        bestLap: null,
        progress: projection.progress,
        finished: false,
        fuel: 100,
        tyreWear: 0,
        damage: 0,
      };
    });
  }

  get standings(): CarState[] {
    return [...this.cars].sort((a, b) => {
      const da = this.dynamics[a.id],
        db = this.dynamics[b.id];
      if (a.finished || b.finished) {
        if (a.finished !== b.finished) return a.finished ? -1 : 1;
        return da.finishTime - db.finishTime || a.id - b.id;
      }
      return db.creditedDistance - da.creditedDistance || a.id - b.id;
    });
  }
  get position(): number {
    return this.standings.findIndex((car) => car.id === 0) + 1;
  }

  /** The caller normally uses a fixed 1/120 s accumulator. */
  update(dt: number, input: DriverInput): void {
    if (!Number.isFinite(dt) || dt <= 0 || this.finished) return;
    const steps = Math.ceil(Math.min(dt, 0.25) * 120),
      step = Math.min(dt, 0.25) / steps;
    for (let i = 0; i < steps && !this.finished; i++) this.step(step, input);
  }

  resetPlayer(): void {
    const car = this.cars[0],
      state = this.dynamics[0],
      p = this.at(state.projection.progress);
    car.x = p.x;
    car.z = p.z;
    car.y = p.y + 0.35;
    car.heading = Math.atan2(p.tx, p.tz);
    car.speed = 0;
    car.gear = 1;
    car.rpm = 4500;
    state.steering = 0;
    state.lateralVelocity = 0;
    state.projection = this.project(car.x, car.z);
  }

  private step(dt: number, input: DriverInput): void {
    this.time += dt;
    for (const car of this.cars)
      if (!car.finished)
        this.drive(car, car.id === 0 ? input : this.ai(car, dt), dt);
    this.resolveCarContacts();
    for (const car of this.cars) if (!car.finished) this.trackProgress(car, dt);
    this.finished = this.cars[0].finished;
  }
  private maxSteering(speed: number): number {
    return 0.5 / (1 + speed / 38);
  }
  private grip(car: CarState, onGrass: boolean): number {
    return (
      (this.settings.weather === "rain" ? 0.62 : 1) *
      (onGrass ? 0.29 : 1) *
      (1 - car.tyreWear * 0.0035) *
      (1 - car.damage * 0.0015)
    );
  }

  private drive(car: CarState, raw: DriverInput, dt: number): void {
    const state = this.dynamics[car.id],
      p = this.project(car.x, car.z, state.projection.index);
    const onGrass = p.distance > this.circuit.width * 0.5;
    const throttle = clamp(raw.throttle || 0, 0, 1),
      brake = clamp(raw.brake || 0, 0, 1);
    const steering = clamp(raw.steer || 0, -1, 1),
      grip = this.grip(car, onGrass);
    const lateralLimit = (13.8 + car.speed * car.speed * 0.004) * grip;
    state.steering += (steering - state.steering) * Math.min(1, dt * 9);
    const wheelAngle = state.steering * this.maxSteering(car.speed);
    const requestedYaw = (car.speed / WHEELBASE) * Math.tan(wheelAngle);
    const yaw = clamp(
      requestedYaw,
      -lateralLimit / Math.max(car.speed, 1),
      lateralLimit / Math.max(car.speed, 1),
    );
    car.heading = angleDelta(car.heading + yaw * dt);
    const excess = requestedYaw - yaw;
    state.lateralVelocity +=
      (-excess * car.speed * 0.15 -
        state.lateralVelocity * (onGrass ? 2.5 : 7) * grip) *
      dt;
    state.lateralVelocity = clamp(state.lateralVelocity, -7, 7);
    const drsOpen =
      raw.drs && !onGrass && this.settings.weather !== "rain" && car.speed > 22;
    const drag = car.speed * car.speed * (drsOpen ? 0.0011 : 0.0015);
    const engine =
      (car.fuel > 0 ? 17 : 0) *
      Math.max(0.1, 1 - car.speed / 180) *
      (1 - car.damage * 0.005);
    const rolling =
      car.speed > 0.05 ? (onGrass ? 4.8 + car.speed * 0.08 : 0.45) : 0;
    const slope = (this.at(p.progress + 5).y - p.y) / 5;
    car.speed = clamp(
      car.speed +
        (throttle * engine -
          brake * 24 * Math.min(grip, 1.2) -
          drag -
          rolling -
          slope * 9.81) *
          dt,
      0,
      105,
    );
    const sx = Math.sin(car.heading),
      sz = Math.cos(car.heading);
    car.x += (sx * car.speed + sz * state.lateralVelocity) * dt;
    car.z += (sz * car.speed - sx * state.lateralVelocity) * dt;
    car.fuel = Math.max(0, car.fuel - dt * (0.003 + throttle * 0.017));
    car.tyreWear = Math.min(
      100,
      car.tyreWear +
        dt * (0.003 + Math.abs(wheelAngle) * car.speed * 0.002 + brake * 0.008),
    );
    const atNew = this.project(car.x, car.z, p.index),
      barrier = this.circuit.width * 0.5 + 3;
    if (atNew.distance > barrier) {
      // Resolve just wall penetration, preserving forward movement without track teleporting.
      const sign = Math.sign(atNew.lateral) || 1;
      car.x = atNew.x + atNew.tz * barrier * sign;
      car.z = atNew.z - atNew.tx * barrier * sign;
      const normalSpeed = Math.abs(sx * atNew.tz - sz * atNew.tx) * car.speed;
      car.damage = Math.min(100, car.damage + normalSpeed * 0.045);
      car.speed *= Math.max(0.35, 1 - normalSpeed * 0.025);
      state.lateralVelocity = 0;
      const tangentHeading = Math.atan2(atNew.tx, atNew.tz);
      const direction =
        Math.cos(car.heading - tangentHeading) >= 0 ? 0 : Math.PI;
      car.heading = angleDelta(
        tangentHeading +
          direction +
          angleDelta(car.heading - tangentHeading - direction) * 0.2,
      );
    }
    car.y = atNew.y + 0.35;
    const limits = [0, 13, 23, 34, 45, 57, 69, 82, 100];
    let gear = 1;
    while (gear < 8 && car.speed > limits[gear]) gear++;
    car.gear = gear;
    car.rpm = Math.round(
      clamp(
        5000 +
          ((car.speed - limits[gear - 1]) / (limits[gear] - limits[gear - 1])) *
            7200 +
          throttle * 600,
        4500,
        12800,
      ),
    );
  }

  private ai(car: CarState, dt: number): DriverInput {
    const state = this.dynamics[car.id];
    state.reaction -= dt;
    if (state.reaction > 0) return state.aiInput;
    const level = this.settings.difficulty,
      reactionTimes = [0.32, 0.25, 0.18, 0.12, 0.075];
    state.reaction += reactionTimes[level];
    const p = state.projection,
      safety = [1.34, 1.23, 1.14, 1.07, 1.01][level];
    const accuracy = [1.2, 0.85, 0.55, 0.3, 0.12][level],
      roadGrip = this.grip(car, false);
    let desiredSpeed = 86;
    const brakePower = (18 * roadGrip) / safety;
    const horizon = Math.max(
      45,
      (car.speed * car.speed) / (2 * brakePower) + 35,
    );
    for (let d = 0; d <= horizon; d += 6) {
      const target = this.at(p.progress + d),
        curvature = Math.abs(target.curvature) * safety;
      const cornerSpeed = Math.min(
        86,
        Math.sqrt(
          (13.8 * roadGrip) / Math.max(0.001, curvature - 0.004 * roadGrip),
        ),
      );
      desiredSpeed = Math.min(
        desiredSpeed,
        Math.sqrt(
          cornerSpeed * cornerSpeed + 2 * brakePower * Math.max(0, d - 10),
        ),
      );
    }
    let desiredLane = Math.sin(this.time * 0.22 + car.id * 2.1) * accuracy;
    let passing = false;
    for (const other of this.cars) {
      if (other.id === car.id || other.finished) continue;
      const otherState = this.dynamics[other.id],
        gap = wrap(
          otherState.projection.progress - p.progress,
          this.geometry.length,
        );
      if (
        gap < 30 &&
        Math.abs(otherState.projection.lateral - p.lateral) < 3.5
      ) {
        const room = Math.max(0.5, this.circuit.width * 0.5 - 2);
        desiredLane =
          (otherState.projection.lateral > 0 ? -1 : 1) * Math.min(room, 3.4);
        const lateralGap = Math.abs(otherState.projection.lateral - p.lateral);
        const safeGap = Math.sqrt(
          Math.max(0, 3.1 * 3.1 - lateralGap * lateralGap),
        );
        if (gap < 14 + car.speed * (0.45 - level * 0.04)) {
          desiredSpeed = Math.min(
            desiredSpeed,
            Math.max(2, other.speed + (gap - safeGap) * 0.8),
          );
          state.lane = desiredLane;
          passing = true;
        }
      }
    }
    state.lane += clamp(
      desiredLane - state.lane,
      -reactionTimes[level] * 3,
      reactionTimes[level] * 3,
    );
    const target = this.at(
      p.progress +
        (passing
          ? 3.5 + car.speed * 0.22
          : 9 + car.speed * (0.48 + level * 0.015)),
    );
    const tx = target.x + target.tz * state.lane - car.x,
      tz = target.z - target.tx * state.lane - car.z;
    const error = angleDelta(Math.atan2(tx, tz) - car.heading);
    const steer = clamp(
      Math.atan2(
        2 * WHEELBASE * Math.sin(error),
        Math.max(1, Math.hypot(tx, tz)),
      ) / this.maxSteering(car.speed),
      -1,
      1,
    );
    if (p.distance > this.circuit.width * 0.5)
      desiredSpeed = Math.min(desiredSpeed, 19);
    state.aiInput = {
      throttle: clamp((desiredSpeed - car.speed) * 0.35, 0, 1),
      brake: clamp((car.speed - desiredSpeed) * 0.24, 0, 1),
      steer,
      drs: Math.abs(target.curvature) < 0.0015 && car.speed > 45,
    };
    return state.aiInput;
  }

  private resolveCarContacts(): void {
    const minimumDistance = 2.15;
    for (let a = 0; a < this.cars.length; a++)
      for (let b = a + 1; b < this.cars.length; b++) {
        const ca = this.cars[a],
          cb = this.cars[b];
        if (ca.finished || cb.finished || Math.abs(ca.y - cb.y) > 2) continue;
        const dx = cb.x - ca.x,
          dz = cb.z - ca.z,
          distance = Math.hypot(dx, dz);
        if (distance >= minimumDistance) continue;
        const nx = distance > 0.001 ? dx / distance : 1,
          nz = distance > 0.001 ? dz / distance : 0;
        const separation = (minimumDistance - distance) * 0.5;
        ca.x -= nx * separation;
        ca.z -= nz * separation;
        cb.x += nx * separation;
        cb.z += nz * separation;
        const impact =
          (Math.sin(ca.heading) * ca.speed - Math.sin(cb.heading) * cb.speed) *
            nx +
          (Math.cos(ca.heading) * ca.speed - Math.cos(cb.heading) * cb.speed) *
            nz;
        if (impact > 0) {
          // Equal-mass impulse: a rear-ended car is pushed forward rather than repeatedly pinned.
          const impulse = impact * 0.575;
          ca.speed = Math.max(
            0,
            ca.speed -
              impulse * (Math.sin(ca.heading) * nx + Math.cos(ca.heading) * nz),
          );
          cb.speed = Math.max(
            0,
            cb.speed +
              impulse * (Math.sin(cb.heading) * nx + Math.cos(cb.heading) * nz),
          );
          this.dynamics[ca.id].lateralVelocity -=
            impulse * (Math.cos(ca.heading) * nx - Math.sin(ca.heading) * nz);
          this.dynamics[cb.id].lateralVelocity +=
            impulse * (Math.cos(cb.heading) * nx - Math.sin(cb.heading) * nz);
          const damagingImpact = Math.max(0, impact - 2);
          ca.damage = Math.min(100, ca.damage + damagingImpact * 0.12);
          cb.damage = Math.min(100, cb.damage + damagingImpact * 0.12);
        }
      }
  }

  private trackProgress(car: CarState, dt: number): void {
    const state = this.dynamics[car.id],
      p = this.project(car.x, car.z, state.projection.index),
      length = this.geometry.length;
    let delta = p.progress - state.projection.progress;
    if (delta < -length * 0.5) delta += length;
    if (delta > length * 0.5) delta -= length;
    car.lapTime += dt;
    // Reject progress jumps at intersections and require all sectors in forward order.
    const plausible = Math.abs(delta) < Math.max(5, (car.speed + 10) * dt * 3);
    if (!plausible || p.distance > this.circuit.width * 0.5 + 3.5)
      state.validLap = false;
    if (plausible) {
      state.creditedDistance += delta;
      if (state.started) state.sinceLine += delta;
      const oldSector = Math.floor(
          (state.projection.progress / length) * SECTORS,
        ),
        newSector = Math.floor((p.progress / length) * SECTORS);
      if (delta > 0 && newSector !== oldSector) {
        if (newSector === state.nextSector) state.nextSector++;
        else if (newSector !== 0 && newSector > state.nextSector)
          state.validLap = false;
      }
      const forwardCrossing =
        delta > 0 &&
        state.projection.progress > length * 0.9 &&
        p.progress < length * 0.1;
      if (forwardCrossing) {
        if (
          state.started &&
          state.validLap &&
          state.nextSector === SECTORS &&
          state.sinceLine > length * 0.9
        ) {
          state.completedLaps++;
          car.bestLap =
            car.bestLap === null
              ? car.lapTime
              : Math.min(car.bestLap, car.lapTime);
          car.lap = state.completedLaps + 1;
          if (state.completedLaps >= this.settings.laps) {
            car.finished = true;
            state.finishTime = this.time;
          }
        }
        state.started = true;
        state.sinceLine = p.progress;
        state.nextSector = 1;
        state.validLap = true;
        car.lapTime = 0;
      }
    }
    car.progress = p.progress;
    state.projection = p;
  }

  private at(distance: number): TrackSample {
    const samples = this.geometry.samples,
      length = this.geometry.length,
      d = wrap(distance, length);
    let lo = 0,
      hi = samples.length - 1;
    while (lo < hi) {
      const mid = Math.ceil((lo + hi) * 0.5);
      if (samples[mid].distance <= d) lo = mid;
      else hi = mid - 1;
    }
    const a = samples[lo],
      b = samples[(lo + 1) % samples.length];
    const span = (lo === samples.length - 1 ? length : b.distance) - a.distance;
    const t = clamp((d - a.distance) / Math.max(0.0001, span), 0, 1);
    let tx = a.tx + (b.tx - a.tx) * t,
      tz = a.tz + (b.tz - a.tz) * t;
    const norm = Math.hypot(tx, tz) || 1;
    tx /= norm;
    tz /= norm;
    return {
      x: a.x + (b.x - a.x) * t,
      y: a.y + (b.y - a.y) * t,
      z: a.z + (b.z - a.z) * t,
      tx,
      tz,
      curvature: a.curvature + (b.curvature - a.curvature) * t,
      distance: d,
    };
  }

  private project(x: number, z: number, nearby?: number): Projection {
    const samples = this.geometry.samples,
      length = this.geometry.length;
    let best: Projection | undefined;
    const inspect = (index: number) => {
      const a = samples[index],
        b = samples[(index + 1) % samples.length],
        dx = b.x - a.x,
        dz = b.z - a.z;
      const t = clamp(
        ((x - a.x) * dx + (z - a.z) * dz) /
          Math.max(dx * dx + dz * dz, 0.00001),
        0,
        1,
      );
      const px = a.x + dx * t,
        pz = a.z + dz * t,
        distance = Math.hypot(x - px, z - pz);
      if (best && best.distance <= distance) return;
      const norm = Math.hypot(dx, dz) || 1,
        tx = dx / norm,
        tz = dz / norm;
      const endDistance = index === samples.length - 1 ? length : b.distance;
      best = {
        x: px,
        y: a.y + (b.y - a.y) * t,
        z: pz,
        tx,
        tz,
        progress: wrap(a.distance + (endDistance - a.distance) * t, length),
        lateral: (x - px) * tz - (z - pz) * tx,
        distance,
        index,
      };
    };
    if (nearby !== undefined)
      for (let offset = -18; offset <= 18; offset++)
        inspect(wrap(nearby + offset, samples.length));
    if (!best || best.distance > this.circuit.width + 8)
      for (let i = 0; i < samples.length; i++) inspect(i);
    return best!;
  }
}

/**
 * Versioned snapshot contract between the Python physics (Pyodide worker) and
 * the renderer. Mirrors python/physics_engine/contract.py field by field; the
 * worker refuses to start when versions or field lists diverge, and
 * python/tests/test_bridge.py parses these arrays to check them.
 */
export const CONTRACT_VERSION = 1;

export const HEADER_FIELDS = [
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
] as const;

export const CAR_FIELDS = [
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
] as const;

export type HeaderField = (typeof HEADER_FIELDS)[number];
export type CarField = (typeof CAR_FIELDS)[number];

const H = Object.fromEntries(HEADER_FIELDS.map((f, i) => [f, i])) as Record<
  HeaderField,
  number
>;
const C = Object.fromEntries(CAR_FIELDS.map((f, i) => [f, i])) as Record<
  CarField,
  number
>;

export interface ContractDescription {
  version: number;
  header: string[];
  car: string[];
}

/** Throws when the Python side speaks a different contract. */
export function assertContract(description: ContractDescription): void {
  const same = (a: readonly string[], b: readonly string[]) =>
    a.length === b.length && a.every((v, i) => v === b[i]);
  if (
    description.version !== CONTRACT_VERSION ||
    !same(description.header, HEADER_FIELDS) ||
    !same(description.car, CAR_FIELDS)
  )
    throw new Error(
      `Contrato de dados incompatível: Python v${description.version}, navegador v${CONTRACT_VERSION}.`,
    );
}

/** Physics state of one car as consumed by the renderer and the HUD. */
export interface PhysicsCar {
  id: number;
  x: number;
  y: number;
  z: number;
  heading: number;
  pitch: number;
  roll: number;
  speed: number;
  yawRate: number;
  steerAngle: number;
  gear: number;
  rpm: number;
  throttle: number;
  brake: number;
  lap: number;
  lapTime: number;
  bestLap: number | null;
  lastLap: number | null;
  progress: number;
  lateral: number;
  finished: boolean;
  position: number;
  fuel: number;
  tyreWear: number;
  damage: number;
  drsOpen: boolean;
  drsAllowed: boolean;
  ers: number;
  waterFilm: number;
  downforce: number;
  wheelSpin: [number, number, number, number];
  suspension: [number, number, number, number];
  tyreTemp: [number, number, number, number];
  wheelWear: [number, number, number, number];
  slip: [number, number, number, number];
  surface: [number, number, number, number];
  brakeTemp: [number, number];
}

export interface PhysicsHeader {
  time: number;
  step: number;
  cars: number;
  finished: boolean;
  playerPosition: number;
  alpha: number;
  surfaceVersion: number;
  eventsPending: number;
  playerReverse: boolean;
  physicsMs: number;
}

export interface PhysicsFrame {
  header: PhysicsHeader;
  prev: PhysicsCar[];
  curr: PhysicsCar[];
}

const four = (d: Float64Array, o: number, first: CarField) =>
  [d[o + C[first]], d[o + C[first] + 1], d[o + C[first] + 2], d[o + C[first] + 3]] as [
    number,
    number,
    number,
    number,
  ];

function decodeCar(d: Float64Array, o: number): PhysicsCar {
  const lap = (v: number) => (v < 0 ? null : v);
  return {
    id: d[o + C.id],
    x: d[o + C.x],
    y: d[o + C.y],
    z: d[o + C.z],
    heading: d[o + C.heading],
    pitch: d[o + C.pitch],
    roll: d[o + C.roll],
    speed: d[o + C.speed],
    yawRate: d[o + C.yaw_rate],
    steerAngle: d[o + C.steer_angle],
    gear: d[o + C.gear],
    rpm: d[o + C.rpm],
    throttle: d[o + C.throttle],
    brake: d[o + C.brake],
    lap: d[o + C.lap],
    lapTime: d[o + C.lap_time],
    bestLap: lap(d[o + C.best_lap]),
    lastLap: lap(d[o + C.last_lap]),
    progress: d[o + C.progress],
    lateral: d[o + C.lateral],
    finished: d[o + C.finished] > 0.5,
    position: d[o + C.position],
    fuel: d[o + C.fuel_pct],
    tyreWear: d[o + C.tyre_wear],
    damage: d[o + C.damage],
    drsOpen: d[o + C.drs_open] > 0.5,
    drsAllowed: d[o + C.drs_allowed] > 0.5,
    ers: d[o + C.ers_pct],
    waterFilm: d[o + C.water_film],
    downforce: d[o + C.downforce],
    wheelSpin: four(d, o, "spin_fl"),
    suspension: four(d, o, "susp_fl"),
    tyreTemp: four(d, o, "tyre_t_fl"),
    wheelWear: four(d, o, "wear_fl"),
    slip: four(d, o, "slip_fl"),
    surface: four(d, o, "surface_fl"),
    brakeTemp: [d[o + C.brake_t_front], d[o + C.brake_t_rear]],
  };
}

/** Decode one advance() buffer: header, previous and current fixed-step states. */
export function decodeFrame(data: Float64Array): PhysicsFrame {
  if (data.length < HEADER_FIELDS.length) throw new Error("Snapshot vazio.");
  if (data[H.contract_version] !== CONTRACT_VERSION)
    throw new Error("Versão do snapshot físico incompatível.");
  const cars = data[H.n_cars];
  const stride = data[H.car_fields];
  if (stride !== CAR_FIELDS.length)
    throw new Error("Snapshot físico com campos inesperados.");
  const expected = HEADER_FIELDS.length + 2 * cars * stride;
  if (data.length !== expected)
    throw new Error(`Snapshot físico com ${data.length} valores; esperado ${expected}.`);
  const prev: PhysicsCar[] = [];
  const curr: PhysicsCar[] = [];
  const base = HEADER_FIELDS.length;
  for (let i = 0; i < cars; i++) {
    prev.push(decodeCar(data, base + i * stride));
    curr.push(decodeCar(data, base + (cars + i) * stride));
  }
  return {
    header: {
      time: data[H.time],
      step: data[H.step],
      cars,
      finished: data[H.finished] > 0.5,
      playerPosition: data[H.player_position],
      alpha: data[H.alpha],
      surfaceVersion: data[H.surface_version],
      eventsPending: data[H.events_pending],
      playerReverse: data[H.player_reverse] > 0.5,
      physicsMs: data[H.physics_ms],
    },
    prev,
    curr,
  };
}

const wrapAngle = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));

/**
 * Visual interpolation between two fixed physics steps (never physics):
 * positions linearly, angles along the shortest arc.
 */
export function interpolateCar(
  a: PhysicsCar,
  b: PhysicsCar,
  t: number,
): PhysicsCar {
  const l = (p: number, q: number) => p + (q - p) * t;
  const ang = (p: number, q: number) => p + wrapAngle(q - p) * t;
  const l4 = (p: number[], q: number[]) =>
    [l(p[0], q[0]), l(p[1], q[1]), l(p[2], q[2]), l(p[3], q[3])] as [
      number,
      number,
      number,
      number,
    ];
  return {
    ...b,
    x: l(a.x, b.x),
    y: l(a.y, b.y),
    z: l(a.z, b.z),
    heading: ang(a.heading, b.heading),
    pitch: l(a.pitch, b.pitch),
    roll: l(a.roll, b.roll),
    speed: l(a.speed, b.speed),
    steerAngle: l(a.steerAngle, b.steerAngle),
    wheelSpin: [
      ang(a.wheelSpin[0], b.wheelSpin[0]),
      ang(a.wheelSpin[1], b.wheelSpin[1]),
      ang(a.wheelSpin[2], b.wheelSpin[2]),
      ang(a.wheelSpin[3], b.wheelSpin[3]),
    ],
    suspension: l4(a.suspension, b.suspension),
  };
}

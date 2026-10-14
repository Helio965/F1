import { describe, expect, it } from "vitest";
import { RaceSimulation } from "../src/racing/simulation";
import { defaultCircuit } from "../src/circuits/model";
import type { Circuit, DriverInput, RaceSettings } from "../src/core/types";

const idle: DriverInput = { throttle: 0, brake: 0, steer: 0, drs: false };
const settings: RaceSettings = {
  cars: 2,
  laps: 1,
  difficulty: 2,
  weather: "sun",
};
const circle: Circuit = {
  id: "circle",
  name: "Circle",
  version: 1,
  width: 24,
  points: Array.from({ length: 16 }, (_, i) => ({
    x: 150 * Math.cos((i / 16) * Math.PI * 2),
    y: 0,
    z: 150 * Math.sin((i / 16) * Math.PI * 2),
  })),
};

function place(sim: RaceSimulation, index: number): void {
  const sample =
    sim.geometry.samples[
      (index + sim.geometry.samples.length) % sim.geometry.samples.length
    ];
  Object.assign(sim.cars[0], {
    x: sample.x,
    y: sample.y + 0.35,
    z: sample.z,
    speed: 0,
    heading: Math.atan2(sample.tx, sample.tz),
  });
  sim.update(1 / 120, idle);
}

describe("racing simulation", () => {
  it("creates 2–10 separate cars facing forward and clamps unsupported grids", () => {
    for (let count = 2; count <= 10; count++) {
      const sim = new RaceSimulation(defaultCircuit(), {
        ...settings,
        cars: count,
      });
      expect(sim.cars).toHaveLength(count);
      expect(new Set(sim.cars.map((c) => `${c.x},${c.z}`)).size).toBe(count);
      expect(sim.position).toBe(1);
      for (const car of sim.cars) {
        const near = sim.geometry.samples.reduce((a, b) =>
          Math.abs(b.distance - car.progress) <
          Math.abs(a.distance - car.progress)
            ? b
            : a,
        );
        expect(
          Math.sin(car.heading) * near.tx + Math.cos(car.heading) * near.tz,
        ).toBeGreaterThan(0.99);
      }
    }
    expect(
      new RaceSimulation(circle, { ...settings, cars: 99 }).cars,
    ).toHaveLength(10);
  });

  it("keeps fixed physics independent of 30, 60, or 120 Hz render cadence", () => {
    const results = [30, 60, 120].map((fps) => {
      const sim = new RaceSimulation(circle, settings);
      for (let i = 0; i < fps * 4; i++)
        sim.update(1 / fps, { ...idle, throttle: 1, steer: -0.07 });
      return sim.cars[0];
    });
    for (const result of results.slice(1)) {
      expect(result.x).toBeCloseTo(results[0].x, 7);
      expect(result.z).toBeCloseTo(results[0].z, 7);
      expect(result.speed).toBeCloseTo(results[0].speed, 7);
    }
  });

  it("brakes and turns with less grip in rain", () => {
    const dry = new RaceSimulation(circle, settings);
    const wet = new RaceSimulation(circle, { ...settings, weather: "rain" });
    const initial = dry.cars[0].heading;
    dry.cars[0].speed = wet.cars[0].speed = 50;
    for (let i = 0; i < 24; i++) {
      dry.update(1 / 120, { ...idle, brake: 1, steer: 1 });
      wet.update(1 / 120, { ...idle, brake: 1, steer: 1 });
    }
    expect(dry.cars[0].speed).toBeLessThan(wet.cars[0].speed);
    expect(Math.abs(dry.cars[0].heading - initial)).toBeGreaterThan(
      Math.abs(wet.cars[0].heading - initial),
    );
  });

  it("counts a complete ordered lap and ends the chosen race distance", () => {
    const sim = new RaceSimulation(circle, settings);
    const n = sim.geometry.samples.length;
    for (let i = n - 6; i <= n; i++) place(sim, i);
    expect(sim.cars[0].lap).toBe(1);
    for (let i = 1; i <= n; i++) place(sim, i);
    expect(sim.cars[0].lap).toBe(2);
    expect(sim.cars[0].bestLap).toBeGreaterThan(0);
    expect(sim.finished).toBe(true);
  });

  it("does not grant a lap for crossing backwards or cutting to the finish", () => {
    const reverse = new RaceSimulation(circle, settings),
      n = reverse.geometry.samples.length;
    for (let i = n - 6; i <= n; i++) place(reverse, i);
    for (let i = n - 1; i >= 0; i--) place(reverse, i);
    expect(reverse.cars[0].lap).toBe(1);
    const cut = new RaceSimulation(circle, settings);
    for (let i = n - 6; i <= n; i++) place(cut, i);
    place(cut, Math.floor(n / 2));
    for (let i = n - 6; i <= n; i++) place(cut, i);
    expect(cut.cars[0].lap).toBe(1);
    expect(cut.finished).toBe(false);
  });

  it("lets opponents navigate the default circuit and complete a race", () => {
    const sim = new RaceSimulation(defaultCircuit(), { ...settings, cars: 6 });
    for (let i = 0; i < 120 * 100; i++) sim.update(1 / 120, idle);
    const opponents = sim.cars.slice(1);
    expect(opponents.filter((c) => c.finished).length).toBeGreaterThanOrEqual(
      4,
    );
    for (const car of opponents) {
      expect(Number.isFinite(car.x + car.z + car.speed)).toBe(true);
      expect(car.damage).toBeLessThan(30);
    }
  }, 20000);

  it("transfers a rear impact instead of pinning the stopped car", () => {
    const sim = new RaceSimulation(circle, settings),
      [front, rear] = sim.cars;
    rear.x = front.x - Math.sin(front.heading) * 1.5;
    rear.z = front.z - Math.cos(front.heading) * 1.5;
    rear.heading = front.heading;
    rear.speed = 10;
    sim.update(1 / 120, idle);
    expect(front.speed).toBeGreaterThan(1);
    expect(rear.speed).toBeLessThan(9);
    expect(
      Math.hypot(front.x - rear.x, front.z - rear.z),
    ).toBeGreaterThanOrEqual(2.14);
    expect(front.damage).toBeGreaterThan(0);
  });

  it("loses speed on grass and keeps tyres and fuel bounded", () => {
    const road = new RaceSimulation(circle, settings),
      grass = new RaceSimulation(circle, settings);
    const start = grass.geometry.samples.find(
      (s) => s.distance >= grass.cars[0].progress,
    )!;
    grass.cars[0].x = start.x + start.tz * 13.5;
    grass.cars[0].z = start.z - start.tx * 13.5;
    road.cars[0].speed = grass.cars[0].speed = 30;
    for (let i = 0; i < 30; i++) {
      road.update(1 / 120, idle);
      grass.update(1 / 120, idle);
    }
    expect(grass.cars[0].speed).toBeLessThan(road.cars[0].speed);
    expect(grass.cars[0].fuel).toBeLessThan(100);
    expect(grass.cars[0].tyreWear).toBeGreaterThan(0);
  });

  it("supports all five distinct difficulty behaviours, including rain", () => {
    const positions: number[] = [];
    for (let difficulty = 0; difficulty < 5; difficulty++) {
      const sim = new RaceSimulation(defaultCircuit(), {
        ...settings,
        difficulty,
        weather: "rain",
      });
      expect(sim.settings.difficulty).toBe(difficulty);
      for (let i = 0; i < 120 * 10; i++) sim.update(1 / 120, idle);
      expect(sim.cars[1].progress).toBeGreaterThan(30);
      positions.push(sim.cars[1].progress);
    }
    expect(new Set(positions.map((n) => n.toFixed(2))).size).toBe(5);
  });

  it("resets an off-road player locally without adding race progress", () => {
    const sim = new RaceSimulation(circle, settings),
      car = sim.cars[0];
    const before = car.progress;
    car.x += 4;
    car.speed = 20;
    car.heading += 1;
    sim.resetPlayer();
    expect(car.speed).toBe(0);
    expect(car.progress).toBe(before);
    expect(car.lap).toBe(1);
  });
});

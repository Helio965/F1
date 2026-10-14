import { describe, expect, it } from "vitest";
import type { Circuit } from "../src/core/types";
import {
  cloneCircuit,
  defaultCircuit,
  importCircuit,
  reviseCircuit,
} from "../src/circuits/model";
import { sampleCircuit, validateCircuit } from "../src/circuits/geometry";

describe("circuit document safety", () => {
  it("round-trips a versioned export while revoking imported homologation", () => {
    const circuit = defaultCircuit();
    circuit.homologatedVersion = circuit.version;
    const imported = importCircuit(
      JSON.stringify({ ...circuit, schemaVersion: 1 }),
    );
    expect(imported).toEqual(defaultCircuit());
    expect(imported.homologatedVersion).toBeUndefined();
    expect(() =>
      importCircuit(JSON.stringify({ ...circuit, schemaVersion: 2 })),
    ).toThrow(/não suportada/);
  });
  it("rejects unknown fields and unsafe IDs, versions, coordinate values and document sizes", () => {
    const circuit = defaultCircuit();
    for (const patch of [
      { id: "../../unsafe" },
      { version: -1 },
      { version: 1.5 },
      { width: 500 },
      { unexpected: true },
      { name: "\u0000" },
    ]) {
      expect(() =>
        importCircuit(JSON.stringify({ ...circuit, ...patch })),
      ).toThrow();
    }
    expect(() =>
      importCircuit(
        JSON.stringify({
          ...circuit,
          points: [{ x: 1e100, y: 0, z: 0 }, ...circuit.points],
        }),
      ),
    ).toThrow();
    expect(() =>
      importCircuit(
        JSON.stringify({
          ...circuit,
          points: Array.from({ length: 129 }, () => circuit.points[0]),
        }),
      ),
    ).toThrow();
    expect(() => importCircuit(" ".repeat(100_001))).toThrow(/grande/);
    expect(() => importCircuit('{"__proto__":{},"id":"x"}')).toThrow();
  });
  it("invalidates approval and increments revision without mutating the source", () => {
    const original = defaultCircuit();
    original.homologatedVersion = 1;
    const revised = reviseCircuit(original, { width: 15 });
    expect(revised.version).toBe(2);
    expect(revised.homologatedVersion).toBeUndefined();
    revised.points[0].x = 999;
    expect(original.width).toBe(13);
    expect(original.points[0].x).toBe(-170);
    const copy = cloneCircuit(original);
    copy.points[0].y = 30;
    expect(original.points[0].y).toBe(1);
  });
});

describe("closed circuit geometry and homologation", () => {
  it("provides a playable default circuit with finite, arc-spaced geometry and a smooth seam", () => {
    const circuit = defaultCircuit(),
      geometry = sampleCircuit(circuit),
      samples = geometry.samples;
    expect(validateCircuit(circuit).errors).toEqual([]);
    expect(geometry.length).toBeGreaterThan(900);
    expect(geometry.length).toBeLessThan(1200);
    expect(samples).toHaveLength(768);
    expect(samples[0].distance).toBe(0);
    const gaps = samples.map((p, i) => {
      const next = samples[(i + 1) % samples.length];
      expect(Object.values(p).every(Number.isFinite)).toBe(true);
      expect(Math.hypot(p.tx, p.tz)).toBeCloseTo(1, 6);
      if (i) expect(p.distance).toBeGreaterThan(samples[i - 1].distance);
      return Math.hypot(next.x - p.x, next.y - p.y, next.z - p.z);
    });
    expect(Math.max(...gaps) / Math.min(...gaps)).toBeLessThan(1.03);
    expect(
      samples[0].tx * samples[767].tx + samples[0].tz * samples[767].tz,
    ).toBeGreaterThan(0.98);
  });
  it("rejects a crossing centre line, including an otherwise long and wide track", () => {
    const circuit: Circuit = {
      id: "crossed",
      name: "Crossed",
      version: 1,
      width: 12,
      points: [
        { x: -200, y: 0, z: -100 },
        { x: -100, y: 0, z: -50 },
        { x: 200, y: 0, z: 100 },
        { x: 200, y: 0, z: -100 },
        { x: 100, y: 0, z: -50 },
        { x: -200, y: 0, z: 100 },
      ],
    };
    expect(
      validateCircuit(circuit).errors.some((error) => error.includes("cruza")),
    ).toBe(true);
  });
  it("rejects insufficient length, repeated closure points and dangerous elevation changes", () => {
    const short = defaultCircuit();
    short.points = short.points.map((p) => ({
      x: p.x * 0.1,
      y: 0,
      z: p.z * 0.1,
    }));
    expect(
      validateCircuit(short).errors.some((error) =>
        error.includes("300 metros"),
      ),
    ).toBe(true);
    const duplicate = defaultCircuit();
    duplicate.points.push({ ...duplicate.points[0] });
    expect(
      validateCircuit(duplicate).errors.some((error) =>
        error.includes("vizinhos"),
      ),
    ).toBe(true);
    const steep = defaultCircuit();
    steep.points[2].y = 100;
    expect(
      validateCircuit(steep).errors.some((error) =>
        error.includes("Inclinação"),
      ),
    ).toBe(true);
  });
});

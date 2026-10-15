import { describe, expect, it } from "vitest";
import {
  cloneCircuit,
  defaultCircuit,
  exportCircuit,
  importCircuit,
  reviseCircuit,
} from "../src/circuits/model";
import { sampleCircuit } from "../src/circuits/geometry";

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
      importCircuit(JSON.stringify({ ...circuit, schemaVersion: 3 })),
    ).toThrow(/não suportada/);
  });
  it("round-trips schema 2 physical fields (banking, asphalt, run-off, environment)", () => {
    const circuit = defaultCircuit();
    circuit.points[3].bank = 4.5;
    circuit.surface = { asphalt: "abrasive" };
    circuit.runoff = { type: "gravel", width: 9 };
    circuit.environment = { altitude: 800, exposure: 0.8 };
    circuit.homologatedVersion = 1;
    circuit.homologatedHash = "abc";
    const imported = importCircuit(exportCircuit(circuit));
    expect(imported.points[3].bank).toBe(4.5);
    expect(imported.surface).toEqual({ asphalt: "abrasive" });
    expect(imported.runoff).toEqual({ type: "gravel", width: 9 });
    expect(imported.environment).toEqual({ altitude: 800, exposure: 0.8 });
    expect(imported.homologatedVersion).toBeUndefined();
    expect(imported.homologatedHash).toBeUndefined();
    for (const patch of [
      { surface: { asphalt: "ice" } },
      { runoff: { type: "lava", width: 5 } },
      { runoff: { type: "grass", width: 400 } },
      { environment: { altitude: 99999, exposure: 1 } },
    ])
      expect(() => importCircuit(JSON.stringify({ ...circuit, ...patch }))).toThrow();
    expect(() =>
      importCircuit(
        JSON.stringify({ ...circuit, points: [{ x: 0, y: 0, z: 0, bank: 90 }, ...circuit.points] }),
      ),
    ).toThrow();
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
    expect(revised.homologatedHash).toBeUndefined();
    revised.points[0].x = 999;
    expect(original.width).toBe(13);
    expect(original.points[0].x).toBe(-170);
    const copy = cloneCircuit(original);
    copy.points[0].y = 30;
    expect(original.points[0].y).toBe(1);
  });
});

// Geometric validation moved to the Python analyser (python/tests/test_track_analyzer.py).
describe("closed circuit visual sampling", () => {
  it("provides a playable default circuit with finite, arc-spaced geometry and a smooth seam", () => {
    const circuit = defaultCircuit(),
      geometry = sampleCircuit(circuit),
      samples = geometry.samples;
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
});

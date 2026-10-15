import { CatmullRomCurve3, Vector3 } from "three";
import type { Circuit, CircuitGeometry, TrackSample } from "../core/types";

const SAMPLE_COUNT = 768;
const planarDistance = (
  a: { x: number; z: number },
  b: { x: number; z: number },
) => Math.hypot(a.x - b.x, a.z - b.z);

/**
 * Visual sampling of the circuit spline for previews (editor canvas, mini
 * maps, 3-D preview before analysis). Physics, validation and racing use the
 * Python track analyser, which reproduces this exact Three.js construction
 * (verified in tests/physics-bridge.test.ts).
 */
export function sampleCircuit(circuit: Circuit): CircuitGeometry {
  if (
    circuit.points.length < 3 ||
    circuit.points.some((p) => ![p.x, p.y, p.z].every(Number.isFinite))
  ) {
    return { samples: [], length: 0 };
  }
  const curve = new CatmullRomCurve3(
    circuit.points.map((p) => new Vector3(p.x, p.y, p.z)),
    true,
    "centripetal",
  );
  curve.arcLengthDivisions = 4096;
  const points = curve.getSpacedPoints(SAMPLE_COUNT).slice(0, SAMPLE_COUNT);
  let length = 0;
  const samples: TrackSample[] = points.map((point, index) => {
    const before = points[(index + points.length - 1) % points.length];
    const after = points[(index + 1) % points.length];
    const dx = after.x - before.x;
    const dz = after.z - before.z;
    const tangentLength = Math.hypot(dx, dz) || 1;
    const ab = planarDistance(before, point);
    const bc = planarDistance(point, after);
    const ac = planarDistance(before, after);
    const cross =
      (point.x - before.x) * (after.z - point.z) -
      (point.z - before.z) * (after.x - point.x);
    if (index) length += point.distanceTo(points[index - 1]);
    return {
      x: point.x,
      y: point.y,
      z: point.z,
      tx: dx / tangentLength,
      tz: dz / tangentLength,
      curvature: (2 * cross) / Math.max(ab * bc * ac, 1e-9),
      distance: length,
    };
  });
  length += points[points.length - 1].distanceTo(points[0]);
  return { samples, length };
}

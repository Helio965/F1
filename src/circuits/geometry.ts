import { CatmullRomCurve3, Vector3 } from "three";
import type {
  Circuit,
  CircuitGeometry,
  TrackSample,
  ValidationResult,
} from "../core/types";

const SAMPLE_COUNT = 768;
const planarDistance = (
  a: { x: number; z: number },
  b: { x: number; z: number },
) => Math.hypot(a.x - b.x, a.z - b.z);

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

function cross(a: TrackSample, b: TrackSample, c: TrackSample): number {
  return (b.x - a.x) * (c.z - a.z) - (b.z - a.z) * (c.x - a.x);
}

function segmentsCross(
  a: TrackSample,
  b: TrackSample,
  c: TrackSample,
  d: TrackSample,
): boolean {
  const abC = cross(a, b, c),
    abD = cross(a, b, d),
    cdA = cross(c, d, a),
    cdB = cross(c, d, b);
  return abC * abD < -1e-8 && cdA * cdB < -1e-8;
}

export function validateCircuit(circuit: Circuit): ValidationResult {
  const errors: string[] = [],
    warnings: string[] = [];
  if (circuit.points.length < 6)
    errors.push(
      "Use pelo menos 6 pontos de controle para fechar um circuito suave.",
    );
  if (circuit.points.length > 128)
    errors.push("O limite é de 128 pontos de controle.");
  if (
    !Number.isFinite(circuit.width) ||
    circuit.width < 8 ||
    circuit.width > 24
  )
    errors.push("A pista precisa ter entre 8 e 24 metros de largura.");
  if (
    circuit.points.some(
      (p) =>
        ![p.x, p.y, p.z].every(Number.isFinite) ||
        Math.abs(p.x) > 10_000 ||
        Math.abs(p.z) > 10_000 ||
        Math.abs(p.y) > 400,
    )
  ) {
    errors.push("Há coordenadas inválidas ou fora da área permitida.");
    return { errors, warnings, length: 0 };
  }
  const geometry = sampleCircuit(circuit);
  if (geometry.length < 300)
    errors.push(
      "O circuito precisa ter pelo menos 300 metros para acomodar o grid e uma volta.",
    );
  if (geometry.length > 15_000)
    errors.push("O circuito excede o limite de 15 km.");
  if (!geometry.samples.length)
    return { errors, warnings, length: geometry.length };
  let duplicate = false;
  for (let i = 0; i < circuit.points.length; i++) {
    if (
      planarDistance(
        circuit.points[i],
        circuit.points[(i + 1) % circuit.points.length],
      ) < 6
    )
      duplicate = true;
  }
  if (duplicate)
    errors.push(
      "Pontos vizinhos muito próximos: mantenha pelo menos 6 metros, inclusive no fechamento.",
    );

  // Use short chords on the actual interpolated centre line, including its closing segment.
  const line = geometry.samples.filter((_, index) => index % 3 === 0);
  let crossing = false,
    overlapping = false;
  const localSeparation = Math.max(circuit.width * 3, 24);
  for (let i = 0; i < line.length; i++) {
    for (let j = i + 2; j < line.length; j++) {
      if (i === 0 && j === line.length - 1) continue;
      const along = Math.abs(line[j].distance - line[i].distance);
      const apart = Math.min(along, geometry.length - along);
      if (apart < localSeparation) continue;
      if (
        segmentsCross(
          line[i],
          line[(i + 1) % line.length],
          line[j],
          line[(j + 1) % line.length],
        )
      )
        crossing = true;
      if (
        planarDistance(line[i], line[j]) < circuit.width * 1.12 &&
        Math.abs(line[i].y - line[j].y) < 5
      )
        overlapping = true;
    }
  }
  if (crossing)
    errors.push(
      "A pista cruza a si mesma. Separe os trechos para formar uma volta contínua.",
    );
  else if (overlapping)
    errors.push(
      "Há trechos sem espaço para a largura da pista. Afaste as curvas vizinhas.",
    );
  const tightest = Math.max(
    ...geometry.samples.map((p) => Math.abs(p.curvature)),
  );
  if (tightest > 1 / (circuit.width * 0.65))
    errors.push(
      "Uma curva é fechada demais para essa largura. Afaste os pontos da curva.",
    );
  else if (tightest > 1 / 18)
    warnings.push("Há curvas lentas: freie antes da entrada.");
  const maxSlope = Math.max(
    ...geometry.samples.map((p, i) => {
      const next = geometry.samples[(i + 1) % geometry.samples.length];
      return Math.abs(next.y - p.y) / Math.max(planarDistance(p, next), 0.01);
    }),
  );
  if (maxSlope > 0.18)
    errors.push("Inclinação acima de 18%. Suavize a elevação entre os pontos.");
  else if (maxSlope > 0.08)
    warnings.push("Há subidas íngremes; atenção à aderência nas cristas.");
  if (geometry.length < 700)
    warnings.push(
      "Traçado compacto: um grid menor deixa a largada mais espaçada.",
    );
  return { errors, warnings, length: geometry.length };
}

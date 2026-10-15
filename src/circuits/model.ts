import type { AsphaltType, Circuit, Point3, RunoffType } from "../core/types";

const ASPHALT: AsphaltType[] = ["standard", "smooth", "abrasive"];
const RUNOFF: RunoffType[] = ["mixed", "grass", "gravel", "asphalt"];

const MAX_POINTS = 128;
const MAX_COORDINATE = 10_000;
const ownKeys = (value: object, allowed: string[]) =>
  Object.keys(value).every((key) => allowed.includes(key));
const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

/** An original, compact circuit. Coordinates and widths are measured in metres. */
export function defaultCircuit(): Circuit {
  return {
    id: "aurora-park",
    version: 1,
    name: "Aurora Park",
    width: 13,
    points: [
      { x: -170, y: 1, z: 70 },
      { x: -170, y: 1, z: -65 },
      { x: -125, y: 2, z: -120 },
      { x: -10, y: 4, z: -120 },
      { x: 90, y: 7, z: -100 },
      { x: 150, y: 8, z: -45 },
      { x: 150, y: 7, z: 45 },
      { x: 115, y: 6, z: 70 },
      { x: 60, y: 5, z: 50 },
      { x: 20, y: 4, z: 90 },
      { x: 0, y: 3, z: 125 },
      { x: -35, y: 1, z: 150 },
      { x: -115, y: 1, z: 125 },
    ],
  };
}

export function cloneCircuit(circuit: Circuit): Circuit {
  const copy: Circuit = { ...circuit, points: circuit.points.map((point) => ({ ...point })) };
  if (circuit.surface) copy.surface = { ...circuit.surface };
  if (circuit.runoff) copy.runoff = { ...circuit.runoff };
  if (circuit.environment) copy.environment = { ...circuit.environment };
  return copy;
}

/** Every edit produces a new revision and revokes an earlier homologation. */
export function reviseCircuit(
  circuit: Circuit,
  changes: Partial<Pick<Circuit, "name" | "width" | "points" | "surface" | "runoff">>,
): Circuit {
  const revision = cloneCircuit({
    ...circuit,
    ...changes,
    version: circuit.version + 1,
  });
  delete revision.homologatedVersion;
  delete revision.homologatedHash;
  return revision;
}

/** Parse a small, explicit JSON document; imported approval is never authoritative. */
export function importCircuit(text: string): Circuit {
  if (text.length > 100_000)
    throw new Error("Arquivo muito grande. O limite é 100 KB.");
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error("O arquivo não contém JSON válido.");
  }
  if (
    !isObject(data) ||
    !ownKeys(data, [
      "schemaVersion",
      "id",
      "version",
      "name",
      "width",
      "points",
      "homologatedVersion",
      "homologatedHash",
      "surface",
      "runoff",
      "environment",
    ])
  ) {
    throw new Error("Formato de circuito desconhecido.");
  }
  if (data.schemaVersion !== undefined && data.schemaVersion !== 1 && data.schemaVersion !== 2)
    throw new Error("Versão do formato de circuito não suportada.");
  if (
    typeof data.id !== "string" ||
    !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/.test(data.id)
  ) {
    throw new Error("Identificador do circuito inválido.");
  }
  if (
    !Number.isSafeInteger(data.version) ||
    (data.version as number) < 1 ||
    (data.version as number) > 1_000_000_000
  ) {
    throw new Error("A versão deve ser um inteiro positivo.");
  }
  if (
    typeof data.name !== "string" ||
    !data.name.trim() ||
    data.name.length > 80 ||
    /[\u0000-\u001f]/.test(data.name)
  ) {
    throw new Error("O nome deve ter entre 1 e 80 caracteres.");
  }
  if (!finite(data.width) || data.width < 4 || data.width > 30) {
    throw new Error("A largura deve estar entre 4 e 30 metros.");
  }
  if (
    !Array.isArray(data.points) ||
    data.points.length < 3 ||
    data.points.length > MAX_POINTS
  ) {
    throw new Error(`O circuito deve ter entre 3 e ${MAX_POINTS} pontos.`);
  }
  const points: Point3[] = data.points.map((point) => {
    if (
      !isObject(point) ||
      !ownKeys(point, ["x", "y", "z", "bank"]) ||
      (point.bank !== undefined && (!finite(point.bank) || Math.abs(point.bank) > 20)) ||
      !finite(point.x) ||
      !finite(point.y) ||
      !finite(point.z) ||
      Math.abs(point.x) > MAX_COORDINATE ||
      Math.abs(point.z) > MAX_COORDINATE ||
      Math.abs(point.y) > 400
    ) {
      throw new Error(
        "Cada ponto precisa de coordenadas finitas dentro da área permitida.",
      );
    }
    const parsed: Point3 = { x: point.x, y: point.y, z: point.z };
    if (point.bank !== undefined && point.bank !== 0) parsed.bank = point.bank as number;
    return parsed;
  });
  const circuit: Circuit = {
    id: data.id,
    version: data.version as number,
    name: data.name.trim(),
    width: data.width,
    points,
  };
  if (data.surface !== undefined) {
    if (
      !isObject(data.surface) ||
      !ownKeys(data.surface, ["asphalt"]) ||
      !ASPHALT.includes(data.surface.asphalt as AsphaltType)
    )
      throw new Error("Tipo de asfalto inválido.");
    circuit.surface = { asphalt: data.surface.asphalt as AsphaltType };
  }
  if (data.runoff !== undefined) {
    if (
      !isObject(data.runoff) ||
      !ownKeys(data.runoff, ["type", "width"]) ||
      !RUNOFF.includes(data.runoff.type as RunoffType) ||
      !finite(data.runoff.width) ||
      data.runoff.width < 1.5 ||
      data.runoff.width > 40
    )
      throw new Error("Área de escape inválida.");
    circuit.runoff = { type: data.runoff.type as RunoffType, width: data.runoff.width };
  }
  if (data.environment !== undefined) {
    const env = data.environment;
    if (
      !isObject(env) ||
      !ownKeys(env, ["altitude", "exposure"]) ||
      !finite(env.altitude) ||
      env.altitude < -500 ||
      env.altitude > 4500 ||
      !finite(env.exposure) ||
      env.exposure < 0 ||
      env.exposure > 1
    )
      throw new Error("Condições ambientais inválidas.");
    circuit.environment = { altitude: env.altitude, exposure: env.exposure };
  }
  return circuit;
}

/** JSON document for export (schema 2 adds banking, surface, run-off and environment). */
export function exportCircuit(circuit: Circuit): string {
  const { homologatedVersion: _v, homologatedHash: _h, ...rest } = circuit;
  return JSON.stringify({ schemaVersion: 2, ...rest }, null, 2);
}

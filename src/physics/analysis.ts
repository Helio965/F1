/**
 * Types of the Python track analysis (contract "apex.track-analysis/1") and
 * the conversion into the layout consumed by the renderer.
 */
import type { Circuit, CircuitGeometry } from "../core/types";

export interface AnalysisIssue {
  code: string;
  message: string;
  [key: string]: unknown;
}

export interface PhysicsTable {
  length: number;
  spacing: number;
  x: number[];
  y: number[];
  z: number[];
  tx: number[];
  tz: number[];
  curvature: number[];
  bank: number[];
  grade: number[];
  vertical_curvature: number[];
  half_width: number[];
  barrier_left: number[];
  barrier_right: number[];
  kerb_left: number[];
  kerb_right: number[];
  runoff_left: number[];
  runoff_right: number[];
  grip: number[];
  roughness: number[];
  exposure: number[];
}

export interface CornerInfo {
  number: number;
  start: number;
  apex: number;
  end: number;
  direction: "left" | "right";
  min_radius: number;
  angle_deg: number;
  kind: string;
  apex_speed_kmh?: number;
}

export interface ZoneInfo {
  start: number;
  end: number;
  length: number;
  entry_speed_kmh: number;
  exit_speed_kmh: number;
  mean_decel_g?: number;
  corner?: number;
}

export interface HazardInfo {
  type: string;
  s: number;
  severity: "low" | "medium" | "high";
  message: string;
}

export interface ProfileInfo {
  compound: string;
  lap_time: number;
  top_speed_kmh: number;
  min_speed_kmh: number;
  nodes: number;
  speed: number[];
}

export interface TrackAnalysis {
  contract: string;
  analyzer_version: string;
  engine_version: string;
  level: "quick" | "full";
  hash: string;
  circuit: { id: string; version: number; name: string };
  valid: boolean;
  errors: AnalysisIssue[];
  warnings: AnalysisIssue[];
  length: number;
  stats?: Record<string, number | string | string[]>;
  physics_table?: PhysicsTable;
  corners?: CornerInfo[];
  straights?: { start: number; end: number; length: number }[];
  segments?: Record<string, unknown>[];
  racing_line?: { offset: number[] };
  profiles?: Record<string, { dry: ProfileInfo; wet: ProfileInfo }>;
  braking_zones?: ZoneInfo[];
  acceleration_zones?: ZoneInfo[];
  hazards?: HazardInfo[];
  navigable?: boolean;
  recommendations?: string[];
  reference_vehicle?: string;
  timings?: Record<string, number>;
}

/** Surface materials (physics_engine.track SURFACE_*). */
export const SURFACE_ASPHALT = 0;
export const SURFACE_KERB = 1;
export const SURFACE_GRASS = 2;
export const SURFACE_GRAVEL = 3;
export const SURFACE_RUNOFF_ASPHALT = 4;

export interface LayoutSample {
  x: number;
  y: number;
  z: number;
  tx: number;
  tz: number;
  distance: number;
  bank: number;
  half: number;
  barrierLeft: number;
  barrierRight: number;
  kerbLeft: number;
  kerbRight: number;
  runoffLeft: number;
  runoffRight: number;
}

export interface TrackLayout {
  samples: LayoutSample[];
  length: number;
  /** true when built from the Python analysis (physics-coherent). */
  analysed: boolean;
}

/** Height of the banked surface at a lateral offset (left positive). */
export const surfaceHeight = (s: LayoutSample, offset: number) =>
  s.y + Math.sin(s.bank) * offset;

export function layoutFromAnalysis(table: PhysicsTable): TrackLayout {
  const samples: LayoutSample[] = table.x.map((x, i) => ({
    x,
    y: table.y[i],
    z: table.z[i],
    tx: table.tx[i],
    tz: table.tz[i],
    distance: i * table.spacing,
    bank: table.bank[i],
    half: table.half_width[i],
    barrierLeft: table.barrier_left[i],
    barrierRight: table.barrier_right[i],
    kerbLeft: table.kerb_left[i],
    kerbRight: table.kerb_right[i],
    runoffLeft: table.runoff_left[i],
    runoffRight: table.runoff_right[i],
  }));
  return { samples, length: table.length, analysed: true };
}

/**
 * Visual-only preview layout from the Three.js spline sampling, used before a
 * Python analysis exists (editor preview). It is never used by physics.
 */
export function layoutFromGeometry(
  circuit: Circuit,
  geometry: CircuitGeometry,
): TrackLayout {
  const half = circuit.width / 2;
  const runoff = circuit.runoff?.width ?? 6.5;
  return {
    length: geometry.length,
    analysed: false,
    samples: geometry.samples.map((s) => ({
      ...s,
      bank: 0,
      half,
      barrierLeft: half + runoff,
      barrierRight: half + runoff,
      kerbLeft: 1,
      kerbRight: 1,
      runoffLeft: SURFACE_GRASS,
      runoffRight: SURFACE_GRASS,
    })),
  };
}

/** Physical fields of a circuit document sent to the analyser. */
export function analysisDocument(circuit: Circuit) {
  return {
    id: circuit.id,
    version: circuit.version,
    name: circuit.name,
    width: circuit.width,
    points: circuit.points.map((p) => ({ x: p.x, y: p.y, z: p.z, bank: p.bank ?? 0 })),
    surface: circuit.surface ?? { asphalt: "standard" },
    runoff: circuit.runoff ?? { type: "mixed", width: 6.5 },
    environment: circuit.environment ?? { altitude: 0, exposure: 1 },
  };
}

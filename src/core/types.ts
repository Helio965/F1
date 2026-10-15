import type { PhysicsCar } from "../physics/contract";

export interface Point3 {
  x: number;
  y: number;
  z: number;
  /** Cross slope in degrees; positive = left side of travel higher. */
  bank?: number;
}
export type AsphaltType = "standard" | "smooth" | "abrasive";
export type RunoffType = "mixed" | "grass" | "gravel" | "asphalt";
export interface Circuit {
  id: string;
  version: number;
  name: string;
  width: number;
  points: Point3[];
  surface?: { asphalt: AsphaltType };
  runoff?: { type: RunoffType; width: number };
  environment?: { altitude: number; exposure: number };
  homologatedVersion?: number;
  /** Hash of the Python analysis that was homologated. */
  homologatedHash?: string;
}
export interface TrackSample extends Point3 {
  tx: number;
  tz: number;
  curvature: number;
  distance: number;
}
export interface CircuitGeometry {
  samples: TrackSample[];
  length: number;
}
export type Quality = "low" | "medium" | "high";
export type Weather = "sun" | "night" | "light-rain" | "rain" | "drying";
export type CameraMode = "orbit" | "chase" | "cockpit" | "rear" | "broadcast";
export type AssistLevel = "full" | "medium" | "off";
export interface RaceSettings {
  cars: number;
  laps: number;
  difficulty: number;
  weather: Weather;
  vehicle?: string;
  assists?: AssistLevel;
  brakeBias?: number;
}
export interface DriverInput {
  throttle: number;
  brake: number;
  steer: number;
  drs: boolean;
}
/** Car state for rendering and the HUD: Python physics state plus identity. */
export interface CarState extends PhysicsCar {
  name: string;
  color: string;
}
export interface GraphicsSettings {
  quality: Quality;
  scale: number;
  adaptive: boolean;
  weather: Weather;
  camera: CameraMode;
  showFps: boolean;
}

export interface Point3 {
  x: number;
  y: number;
  z: number;
}
export interface Circuit {
  id: string;
  version: number;
  name: string;
  width: number;
  points: Point3[];
  homologatedVersion?: number;
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
export interface ValidationResult {
  errors: string[];
  warnings: string[];
  length: number;
}
export type Quality = "low" | "medium" | "high";
export type Weather = "sun" | "night" | "rain";
export type CameraMode = "orbit" | "chase" | "cockpit" | "rear" | "broadcast";
export interface RaceSettings {
  cars: number;
  laps: number;
  difficulty: number;
  weather: Weather;
}
export interface DriverInput {
  throttle: number;
  brake: number;
  steer: number;
  drs: boolean;
}
export interface CarState {
  id: number;
  name: string;
  color: string;
  x: number;
  y: number;
  z: number;
  heading: number;
  speed: number;
  gear: number;
  rpm: number;
  lap: number;
  lapTime: number;
  bestLap: number | null;
  progress: number;
  finished: boolean;
  fuel: number;
  tyreWear: number;
  damage: number;
}
export interface GraphicsSettings {
  quality: Quality;
  scale: number;
  adaptive: boolean;
  weather: Weather;
  camera: CameraMode;
  showFps: boolean;
}

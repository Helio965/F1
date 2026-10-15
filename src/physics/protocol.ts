/** Messages exchanged between the main thread and the physics worker. */
import type { ContractDescription } from "./contract";

export interface DriverCommand {
  throttle: number;
  brake: number;
  steer: number;
  drs: boolean;
  analog: boolean;
}

export interface SessionConfig {
  cars: number;
  laps: number;
  difficulty: number;
  weather: string;
  vehicle: string;
  assists: "full" | "medium" | "off";
  brakeBias?: number;
  seed: number;
  training: boolean;
  autopilot?: boolean;
  compound?: string;
}

export type WorkerRequest =
  | { type: "init"; indexURL: string }
  | { type: "analyze"; id: number; document: string; level: "quick" | "full" }
  | { type: "remember"; id: number; analysis: string }
  | { type: "start"; id: number; hash: string; config: SessionConfig }
  | { type: "advance"; elapsed: number; input: DriverCommand; sentAt: number }
  | { type: "reset" }
  | { type: "brakeBias"; id: number; value: number }
  | { type: "metrics"; id: number }
  | { type: "vehicles"; id: number }
  | { type: "benchmark"; id: number; quick: boolean }
  | { type: "ping"; id: number }
  | { type: "end" };

export interface SurfaceGrid {
  version: number;
  segments: number;
  lanes: number;
  water: Float32Array;
}

export interface PhysicsEvent {
  type: "lap" | "finish" | "contact" | "barrier";
  car?: number;
  cars?: number[];
  dv?: number;
  lap?: number;
  time?: number;
  clean?: boolean;
  x?: number;
  z?: number;
}

export type WorkerResponse =
  | {
      type: "ready";
      versions: { engine: string; analyzer: string; contract: ContractDescription };
      initMs: number;
      importMs: number;
      pyodideVersion: string;
    }
  | { type: "progress"; message: string }
  | { type: "result"; id: number; value: unknown }
  | { type: "error"; id?: number; message: string }
  | {
      type: "frame";
      buffer: Float64Array;
      events: PhysicsEvent[];
      surface: SurfaceGrid | null;
      sentAt: number;
      workerMs: number;
    };

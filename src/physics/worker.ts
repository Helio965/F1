/// <reference lib="webworker" />
/**
 * Physics Web Worker: hosts Pyodide (CPython on WebAssembly) and the APEX
 * Python packages. All physics, track analysis and AI run here, off the
 * rendering thread. The runtime files are served from the same origin
 * (public/pyodide), so no network service is involved during a race.
 */
import { assertContract } from "./contract";
import type { PhysicsEvent, SurfaceGrid, WorkerRequest, WorkerResponse } from "./protocol";
import { installSources } from "./sources";

declare const self: DedicatedWorkerGlobalScope;

interface PyProxy {
  destroy(): void;
  getBuffer(type?: string): { data: ArrayBufferView; release(): void };
  toJs(): unknown;
}
interface PyApi {
  versions(): string;
  vehicles(): string;
  analyze_track(document: string, level: string): string;
  remember_analysis(analysis: string): string;
  create_session(hash: string, config: string): string;
  advance(
    elapsed: number,
    throttle: number,
    brake: number,
    steer: number,
    drs: boolean,
    analog: boolean,
  ): PyProxy;
  reset_player(): void;
  set_brake_bias(value: number): number;
  drain_events(): string;
  surface_grid(): PyProxy;
  metrics(): string;
  end_session(): void;
}
interface Pyodide {
  FS: { mkdirTree(path: string): void; writeFile(path: string, data: string): void };
  runPython(code: string): unknown;
  pyimport(name: string): unknown;
  version: string;
}

let api: PyApi | undefined;
let pyodide: Pyodide | undefined;
let sessionActive = false;
let lastSurfaceVersion = -1;
let lastSurfaceSent = 0;

const post = (message: WorkerResponse, transfer: Transferable[] = []) =>
  self.postMessage(message, transfer);

async function init(indexURL: string) {
  const t0 = performance.now();
  post({ type: "progress", message: "Carregando Python (WebAssembly)…" });
  const module = (await import(/* @vite-ignore */ `${indexURL}pyodide.mjs`)) as {
    loadPyodide(options: { indexURL: string }): Promise<Pyodide>;
  };
  pyodide = await module.loadPyodide({ indexURL });
  const initMs = performance.now() - t0;
  post({ type: "progress", message: "Carregando motor físico…" });
  const t1 = performance.now();
  installSources(pyodide);
  api = pyodide.pyimport("apex_bridge.api") as PyApi;
  const versions = JSON.parse(api.versions());
  assertContract(versions.contract);
  post({
    type: "ready",
    versions,
    initMs,
    importMs: performance.now() - t1,
    pyodideVersion: pyodide.version,
  });
}

function surfaceIfChanged(version: number): SurfaceGrid | null {
  const now = performance.now();
  if (!api || version === lastSurfaceVersion || now - lastSurfaceSent < 400) return null;
  const proxy = api.surface_grid();
  const [v, segments, lanes, water] = proxy.toJs() as [number, number, number, Float32Array];
  proxy.destroy();
  lastSurfaceVersion = v;
  lastSurfaceSent = now;
  return { version: v, segments, lanes, water };
}

function advance(request: Extract<WorkerRequest, { type: "advance" }>) {
  if (!api || !sessionActive) return;
  const t0 = performance.now();
  const { input } = request;
  const proxy = api.advance(
    request.elapsed,
    input.throttle,
    input.brake,
    input.steer,
    input.drs,
    input.analog,
  );
  const view = proxy.getBuffer("f64");
  const buffer = new Float64Array(view.data as Float64Array);
  view.release();
  proxy.destroy();
  // header indices: 8 = surface_version, 9 = events_pending (see contract.ts)
  const events: PhysicsEvent[] = buffer[9] > 0 ? JSON.parse(api.drain_events()) : [];
  const surface = surfaceIfChanged(buffer[8]);
  const transfer: Transferable[] = [buffer.buffer];
  if (surface) transfer.push(surface.water.buffer);
  post(
    {
      type: "frame",
      buffer,
      events,
      surface,
      sentAt: request.sentAt,
      workerMs: performance.now() - t0,
    },
    transfer,
  );
}

function handle(request: WorkerRequest): void | Promise<void> {
  switch (request.type) {
    case "init":
      return init(request.indexURL);
    case "advance":
      return advance(request);
    case "reset":
      api?.reset_player();
      return;
    case "end":
      api?.end_session();
      sessionActive = false;
      return;
  }
  if (!api) throw new Error("Motor físico ainda não carregado.");
  let value: unknown;
  switch (request.type) {
    case "analyze":
      value = JSON.parse(api.analyze_track(request.document, request.level));
      break;
    case "remember":
      value = api.remember_analysis(request.analysis);
      break;
    case "start":
      value = JSON.parse(api.create_session(request.hash, JSON.stringify(request.config)));
      sessionActive = true;
      lastSurfaceVersion = -1;
      break;
    case "brakeBias":
      value = api.set_brake_bias(request.value);
      break;
    case "metrics":
      value = JSON.parse(api.metrics());
      break;
    case "vehicles":
      value = JSON.parse(api.vehicles());
      break;
    case "ping":
      value = performance.now();
      break;
    case "benchmark": {
      const bench = pyodide!.pyimport("apex_bridge.bench") as {
        run_benchmarks_json(quick: boolean): string;
      };
      value = JSON.parse(bench.run_benchmarks_json(request.quick));
      break;
    }
  }
  post({ type: "result", id: request.id, value });
}

self.onmessage = async (event: MessageEvent<WorkerRequest>) => {
  const request = event.data;
  try {
    await handle(request);
  } catch (error) {
    const message = error instanceof Error ? error.message.split("\n").slice(-3).join(" ") : String(error);
    post({ type: "error", id: "id" in request ? request.id : undefined, message });
  }
};

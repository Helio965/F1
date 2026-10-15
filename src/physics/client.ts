/**
 * Main-thread side of the Python physics worker.
 *
 * Frame protocol: the renderer calls `advance(elapsed, input)` every
 * animation frame. Only one advance request is in flight at a time; elapsed
 * time accumulates while the worker is busy (back-pressure), so a slow frame
 * never queues an unbounded backlog. The worker runs the fixed 1/120 s steps
 * and returns the previous and current fixed states; the renderer only
 * interpolates between them.
 */
import { decodeFrame, type PhysicsFrame } from "./contract";
import type {
  DriverCommand,
  PhysicsEvent,
  SessionConfig,
  SurfaceGrid,
  WorkerRequest,
  WorkerResponse,
} from "./protocol";

type Pending = { resolve(value: unknown): void; reject(error: Error): void };
type DistributiveOmit<T, K extends keyof never> = T extends unknown ? Omit<T, K> : never;

export interface PhysicsStatus {
  state: "loading" | "ready" | "error";
  message: string;
  initMs?: number;
  importMs?: number;
  engine?: string;
  pyodide?: string;
}

export class PhysicsClient {
  readonly worker: Worker;
  status: PhysicsStatus = { state: "loading", message: "Iniciando motor físico…" };
  frame: PhysicsFrame | undefined;
  surface: SurfaceGrid | undefined;
  /** Round-trip times (ms) of the last advance requests. */
  readonly latencies: number[] = [];
  readonly workerTimes: number[] = [];
  onEvents: (events: PhysicsEvent[]) => void = () => {};
  onSurface: (grid: SurfaceGrid) => void = () => {};
  onStatus: (status: PhysicsStatus) => void = () => {};
  private readonly ready: Promise<void>;
  private pending = new Map<number, Pending>();
  private nextId = 1;
  private inFlight = false;
  private carried = 0;
  private lastInput: DriverCommand = { throttle: 0, brake: 0, steer: 0, drs: false, analog: false };

  constructor(indexURL = new URL("pyodide/", document.baseURI).href) {
    this.worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module" });
    this.ready = new Promise((resolve, reject) => {
      this.worker.onmessage = (event: MessageEvent<WorkerResponse>) => {
        const message = event.data;
        if (message.type === "ready") {
          this.setStatus({
            state: "ready",
            message: "Motor físico Python pronto",
            initMs: message.initMs,
            importMs: message.importMs,
            engine: message.versions.engine,
            pyodide: message.pyodideVersion,
          });
          resolve();
        } else if (message.type === "error" && message.id === undefined && this.status.state === "loading") {
          this.setStatus({ state: "error", message: message.message });
          reject(new Error(message.message));
        } else this.receive(message);
      };
      this.worker.onerror = (event) => {
        const text = event.message || "Falha ao iniciar o Web Worker de física.";
        this.setStatus({ state: "error", message: text });
        reject(new Error(text));
      };
    });
    this.send({ type: "init", indexURL });
  }

  whenReady(): Promise<void> {
    return this.ready;
  }

  private setStatus(status: PhysicsStatus) {
    this.status = status;
    this.onStatus(status);
  }

  private send(message: WorkerRequest) {
    this.worker.postMessage(message);
  }

  private receive(message: WorkerResponse) {
    switch (message.type) {
      case "progress":
        if (this.status.state === "loading") this.setStatus({ state: "loading", message: message.message });
        return;
      case "result": {
        const pending = this.pending.get(message.id);
        this.pending.delete(message.id);
        pending?.resolve(message.value);
        return;
      }
      case "error": {
        if (message.id !== undefined) {
          const pending = this.pending.get(message.id);
          this.pending.delete(message.id);
          pending?.reject(new Error(message.message));
        } else {
          this.inFlight = false;
          console.error("Physics worker:", message.message);
        }
        return;
      }
      case "frame": {
        this.inFlight = false;
        this.frame = decodeFrame(message.buffer);
        this.latencies.push(performance.now() - message.sentAt);
        this.workerTimes.push(message.workerMs);
        if (this.latencies.length > 600) {
          this.latencies.splice(0, 300);
          this.workerTimes.splice(0, 300);
        }
        if (message.events.length) this.onEvents(message.events);
        if (message.surface) {
          this.surface = message.surface;
          this.onSurface(message.surface);
        }
        return;
      }
    }
  }

  private request<T>(message: DistributiveOmit<Extract<WorkerRequest, { id: number }>, "id">): Promise<T> {
    const id = this.nextId++;
    return this.ready.then(
      () =>
        new Promise<T>((resolve, reject) => {
          this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject });
          this.send({ ...message, id } as WorkerRequest);
        }),
    );
  }

  analyze<T>(document: unknown, level: "quick" | "full"): Promise<T> {
    return this.request<T>({ type: "analyze", document: JSON.stringify(document), level });
  }

  remember(analysis: unknown): Promise<string> {
    return this.request<string>({ type: "remember", analysis: JSON.stringify(analysis) });
  }

  async startSession<T>(hash: string, config: SessionConfig): Promise<T> {
    this.frame = undefined;
    this.inFlight = false;
    this.carried = 0;
    return this.request<T>({ type: "start", hash, config });
  }

  /** Send the elapsed wall time and current input; never blocks the render loop. */
  advance(elapsed: number, input: DriverCommand): void {
    this.lastInput = input;
    this.carried += elapsed;
    if (this.inFlight || this.status.state !== "ready") return;
    this.inFlight = true;
    const sendElapsed = this.carried;
    this.carried = 0;
    this.send({ type: "advance", elapsed: sendElapsed, input: this.lastInput, sentAt: performance.now() });
  }

  get busy(): boolean {
    return this.inFlight;
  }

  reset(): void {
    this.send({ type: "reset" });
  }

  setBrakeBias(value: number): Promise<number> {
    return this.request<number>({ type: "brakeBias", value });
  }

  metrics<T>(): Promise<T> {
    return this.request<T>({ type: "metrics" });
  }

  vehicles<T>(): Promise<T> {
    return this.request<T>({ type: "vehicles" });
  }

  benchmark<T>(quick: boolean): Promise<T> {
    return this.request<T>({ type: "benchmark", quick });
  }

  /** Round-trip latency of an empty request (postMessage + worker dispatch). */
  async ping(samples = 100): Promise<{ median: number; p95: number; max: number }> {
    const times: number[] = [];
    for (let i = 0; i < samples; i++) {
      const t0 = performance.now();
      await this.request<number>({ type: "ping" });
      times.push(performance.now() - t0);
    }
    times.sort((a, b) => a - b);
    return {
      median: times[Math.floor(times.length / 2)],
      p95: times[Math.min(times.length - 1, Math.ceil(times.length * 0.95) - 1)],
      max: times[times.length - 1],
    };
  }

  endSession(): void {
    this.frame = undefined;
    this.inFlight = false;
    this.send({ type: "end" });
  }
}

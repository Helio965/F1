import type { Quality, Weather } from "../core/types";
export interface BenchmarkReport {
  id: string;
  date: string;
  scene: string;
  gpu: string;
  backend: string;
  width: number;
  height: number;
  quality: Quality;
  weather: Weather;
  target: number;
  duration: number;
  frames: number;
  average: number;
  minimum: number;
  low1: number;
  median: number;
  p95: number;
  spikes: number;
  stability: string;
  cpuMs: number;
  gpuMs: number | null;
  heapMB: number | null;
  recommendation: Quality;
  frameTimes: number[];
}
export function summarizeFrames(times: number[]) {
  if (!times.length || times.some((t) => !Number.isFinite(t) || t <= 0))
    throw new Error("Amostras inválidas ou ausentes.");
  const sorted = [...times].sort((a, b) => a - b);
  const mean = (v: number[]) => v.reduce((a, b) => a + b, 0) / v.length;
  const slowest = sorted.slice(-Math.max(1, Math.ceil(times.length * 0.01)));
  return {
    average: 1000 / mean(times),
    minimum: 1000 / sorted[sorted.length - 1],
    low1: 1000 / mean(slowest),
    median: sorted[Math.floor((sorted.length - 1) * 0.5)],
    p95: sorted[Math.ceil((sorted.length - 1) * 0.95)],
    spikes: times.filter((t) => t > 50).length,
  };
}
export class Benchmark {
  state: "idle" | "warmup" | "measuring" | "done" | "invalid" = "idle";
  elapsed = 0;
  reason = "";
  private frames: number[] = [];
  private cpu: number[] = [];
  private gpu: number[] = [];
  constructor(
    public duration = 30,
    public warmup = 3,
  ) {
    this.duration = Math.max(30, duration);
  }
  start() {
    this.state = "warmup";
    this.elapsed = 0;
    this.frames = [];
    this.cpu = [];
    this.gpu = [];
    this.reason = "";
  }
  invalidate(reason: string) {
    if (this.state === "warmup" || this.state === "measuring") {
      this.state = "invalid";
      this.reason = reason;
    }
  }
  get active() {
    return this.state === "warmup" || this.state === "measuring";
  }
  get progress() {
    return Math.min(
      1,
      this.elapsed / (this.state === "warmup" ? this.warmup : this.duration),
    );
  }
  tick(ms: number, cpuMs: number, gpuMs: number | null = null) {
    if (!this.active) return;
    if (!Number.isFinite(ms) || ms <= 0) return;
    if (ms > 2000) {
      this.invalidate(
        "Pausa de mais de 2 segundos detectada. Repita sem interromper o teste.",
      );
      return;
    }
    this.elapsed += ms / 1000;
    if (this.state === "warmup") {
      if (this.elapsed >= this.warmup) {
        this.state = "measuring";
        this.elapsed = 0;
      }
      return;
    }
    this.frames.push(ms);
    this.cpu.push(cpuMs);
    if (gpuMs !== null) this.gpu.push(gpuMs);
    if (this.elapsed >= this.duration) this.state = "done";
  }
  report(
    metadata: Pick<
      BenchmarkReport,
      "gpu" | "width" | "height" | "quality" | "weather" | "target"
    >,
  ): BenchmarkReport {
    if (this.state !== "done") throw new Error("Benchmark incompleto.");
    const stats = summarizeFrames(this.frames);
    const avg = (v: number[]) => v.reduce((a, b) => a + b, 0) / v.length;
    const memory = (
      performance as Performance & { memory?: { usedJSHeapSize: number } }
    ).memory;
    const levels: Quality[] = ["low", "medium", "high"];
    const level = levels.indexOf(metadata.quality);
    const recommendation =
      stats.low1 < metadata.target * 0.8
        ? levels[Math.max(0, level - 1)]
        : metadata.quality;
    return {
      ...metadata,
      ...stats,
      id: crypto.randomUUID(),
      date: new Date().toISOString(),
      backend: "WebGL 2",
      scene: "aurora-park-v1",
      duration: this.elapsed,
      frames: this.frames.length,
      cpuMs: avg(this.cpu),
      gpuMs: this.gpu.length ? avg(this.gpu) : null,
      heapMB: memory ? memory.usedJSHeapSize / 1048576 : null,
      stability:
        stats.low1 >= stats.average * 0.8 ? "Estável" : "Oscilações detectadas",
      recommendation,
      frameTimes: [...this.frames],
    };
  }
}
/** Nonblocking, one pending WebGL2 GPU timing query. Disjoint samples discarded. */
export class GpuTimer {
  private ext: { TIME_ELAPSED_EXT: number; GPU_DISJOINT_EXT: number } | null;
  private pending: WebGLQuery | null = null;
  private measuring = false;
  constructor(private gl: WebGL2RenderingContext) {
    this.ext = gl.getExtension("EXT_disjoint_timer_query_webgl2");
  }
  begin() {
    if (!this.ext || this.pending) return;
    this.pending = this.gl.createQuery();
    if (this.pending) {
      this.gl.beginQuery(this.ext.TIME_ELAPSED_EXT, this.pending);
      this.measuring = true;
    }
  }
  end() {
    if (this.ext && this.measuring) {
      this.gl.endQuery(this.ext.TIME_ELAPSED_EXT);
      this.measuring = false;
    }
  }
  poll(): number | null {
    if (!this.pending || !this.ext || this.measuring) return null;
    const gl = this.gl;
    if (!gl.getQueryParameter(this.pending, gl.QUERY_RESULT_AVAILABLE))
      return null;
    const valid = !gl.getParameter(this.ext.GPU_DISJOINT_EXT);
    const ns = Number(gl.getQueryParameter(this.pending, gl.QUERY_RESULT));
    gl.deleteQuery(this.pending);
    this.pending = null;
    return valid ? ns / 1e6 : null;
  }
}

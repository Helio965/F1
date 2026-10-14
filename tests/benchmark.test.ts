import { describe, it, expect } from "vitest";
import { Benchmark, summarizeFrames } from "../src/graphics/benchmark";
import { AdaptiveQuality } from "../src/graphics/quality";
describe("benchmark real", () => {
  it("uses elapsed-time FPS and documents slowest one percent", () => {
    const s = summarizeFrames([...Array(99).fill(10), 100]);
    expect(s.average).toBeCloseTo(100000 / 1090);
    expect(s.minimum).toBe(10);
    expect(s.low1).toBe(10);
    expect(s.spikes).toBe(1);
    expect(s.p95).toBe(10);
  });
  it("excludes warmup and requires 30 seconds minimum", () => {
    const b = new Benchmark(1, 1);
    expect(b.duration).toBe(30);
    b.start();
    b.tick(1000, 2);
    expect(b.state).toBe("measuring");
    for (let i = 0; i < 300; i++) b.tick(100, 1);
    expect(b.state).toBe("done");
    expect(b.elapsed).toBeCloseTo(30);
  });
  it("invalidates interrupted measurements without manufacturing a report", () => {
    const b = new Benchmark();
    b.start();
    b.invalidate("Aba oculta");
    b.tick(1000, 1);
    expect(b.state).toBe("invalid");
    expect(() =>
      b.report({
        gpu: "test",
        quality: "high",
        weather: "sun",
        target: 60,
        width: 1920,
        height: 1080,
      }),
    ).toThrow();
  });
  it("rejects invalid frame samples", () =>
    expect(() => summarizeFrames([0])).toThrow());
  it("waits for sustained low FPS and cooldown before reducing quality", () => {
    const q = new AdaptiveQuality();
    let changes = 0;
    for (let i = 0; i < 120; i++) if (q.tick(0.1, "high")) changes++;
    expect(changes).toBe(1);
  });
});

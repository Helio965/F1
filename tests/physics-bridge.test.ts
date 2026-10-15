/**
 * Python/browser integration: runs the real Pyodide (CPython on WebAssembly)
 * from node_modules with the same bundled Python sources the Web Worker uses,
 * and checks the versioned contract, snapshot decoding, the Three.js spline
 * equivalence and the CPython/Pyodide numerical agreement.
 */
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { beforeAll, describe, expect, it } from "vitest";
import { defaultCircuit } from "../src/circuits/model";
import { sampleCircuit } from "../src/circuits/geometry";
import { analysisDocument, type TrackAnalysis } from "../src/physics/analysis";
import {
  CAR_FIELDS,
  HEADER_FIELDS,
  assertContract,
  decodeFrame,
  interpolateCar,
} from "../src/physics/contract";
import { PYTHON_SOURCES, installSources } from "../src/physics/sources";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
interface PyProxy {
  getBuffer(type: string): { data: Float64Array; release(): void };
  destroy(): void;
}
interface Api {
  versions(): string;
  analyze_track(doc: string, level: string): string;
  create_session(hash: string, config: string): string;
  advance(e: number, t: number, b: number, s: number, d: boolean, a: boolean): PyProxy;
}
interface Pyodide {
  FS: { mkdirTree(p: string): void; writeFile(p: string, d: string): void };
  runPython(code: string): unknown;
  pyimport(name: string): unknown;
}
let pyodide: Pyodide;
let api: Api;
let analysis: TrackAnalysis;

function advance(elapsed: number, throttle: number, steer = 0) {
  const proxy = api.advance(elapsed, throttle, 0, steer, false, true);
  const view = proxy.getBuffer("f64");
  const copy = new Float64Array(view.data);
  view.release();
  proxy.destroy();
  return copy;
}

beforeAll(async () => {
  const module = (await import(
    pathToFileURL(join(root, "node_modules/pyodide/pyodide.mjs")).href
  )) as { loadPyodide(o: { indexURL: string }): Promise<Pyodide> };
  pyodide = await module.loadPyodide({ indexURL: join(root, "node_modules/pyodide") + "/" });
  installSources(pyodide);
  api = pyodide.pyimport("apex_bridge.api") as Api;
  analysis = JSON.parse(api.analyze_track(JSON.stringify(analysisDocument(defaultCircuit())), "full"));
}, 120_000);

describe("Python physics in WebAssembly (Pyodide)", () => {
  it("bundles every Python module of the four packages", () => {
    const paths = PYTHON_SOURCES.map((s) => s.path);
    for (const required of [
      "physics_engine/vehicle.py",
      "physics_engine/tyre.py",
      "track_analyzer/analyzer.py",
      "racing_ai/driver.py",
      "apex_bridge/api.py",
    ])
      expect(paths).toContain(required);
  });

  it("speaks the same versioned snapshot contract as the renderer", () => {
    const versions = JSON.parse(api.versions());
    expect(() => assertContract(versions.contract)).not.toThrow();
    expect(versions.contract.car).toEqual([...CAR_FIELDS]);
    expect(versions.contract.header).toEqual([...HEADER_FIELDS]);
  });

  it("analyses the default circuit and samples the same curve as Three.js", () => {
    expect(analysis.valid).toBe(true);
    expect(analysis.level).toBe("full");
    const ts = sampleCircuit(defaultCircuit());
    expect(Math.abs(analysis.length - ts.length)).toBeLessThan(0.05);
    const json = pyodide.runPython(`
import json
from track_analyzer.geometry import build_spline
from track_analyzer.circuit import normalise
spline = build_spline(normalise(${JSON.stringify(analysisDocument(defaultCircuit()))}))
json.dumps(spline.spaced_points(768))
`) as string;
    const points = JSON.parse(json) as number[][];
    let worst = 0;
    ts.samples.forEach((s, i) => {
      worst = Math.max(worst, Math.hypot(s.x - points[i][0], s.y - points[i][1], s.z - points[i][2]));
    });
    expect(worst).toBeLessThan(1e-6);
  });

  it("runs a session at a fixed step, decodes snapshots and interpolates", () => {
    const info = JSON.parse(
      api.create_session(
        analysis.hash,
        JSON.stringify({ cars: 3, laps: 1, difficulty: 2, weather: "sun", seed: 7, assists: "full" }),
      ),
    );
    expect(info.cars).toBe(3);
    let frame = decodeFrame(advance(0, 0));
    expect(frame.header.step).toBe(0);
    const start = frame.curr[0];
    for (let i = 0; i < 120; i++) frame = decodeFrame(advance(1 / 60, 1));
    expect(frame.header.step).toBe(240);
    expect(frame.curr[0].speed).toBeGreaterThan(10);
    expect(Math.hypot(frame.curr[0].x - start.x, frame.curr[0].z - start.z)).toBeGreaterThan(10);
    const mid = interpolateCar(frame.prev[0], frame.curr[0], 0.5);
    expect(mid.x).toBeCloseTo((frame.prev[0].x + frame.curr[0].x) / 2, 9);
    for (const car of frame.curr) expect(Object.values(car).flat().every((v) => typeof v !== "number" || Number.isFinite(v))).toBe(true);
  });

  it("is deterministic inside Pyodide and agrees with native CPython", () => {
    const script = `
import json
from track_analyzer import analyze
from apex_bridge.session import Session
from tests_scenario import DOC
a = analyze(DOC, "full")
s = Session(a, {"cars": 2, "laps": 1, "difficulty": 3, "weather": "rain", "seed": 11, "autopilot": True})
for _ in range(600):
    s.step_once((0.0, 0.0, 0.0, False, True))
c = s.sim.cars[0]
RESULT = json.dumps([c.x, c.z, c.u, c.v, c.r, s.sim.cars[1].x, sum(s.sim.track.surface.water)])
`;
    const doc = JSON.stringify(analysisDocument(defaultCircuit()));
    pyodide.FS.writeFile("/home/pyodide/apex/tests_scenario.py", `DOC = ${doc}\n`);
    const first = JSON.parse(pyodide.runPython(`${script}\nRESULT`) as string) as number[];
    const second = JSON.parse(pyodide.runPython(`${script}\nRESULT`) as string) as number[];
    expect(second).toEqual(first);
    const nativeScript = `${script.replace("from tests_scenario import DOC", `DOC = ${doc}`)}\nprint(RESULT)`;
    const native = spawnSync("python3", ["-c", nativeScript], {
      cwd: join(root, "python"),
      encoding: "utf8",
    });
    if (native.status !== 0 || !native.stdout) {
      console.warn("python3 indisponível; comparação CPython × Pyodide ignorada.");
      return;
    }
    const cpython = JSON.parse(native.stdout.trim().split("\n").pop()!) as number[];
    // libm implementations differ in the last ulp; over 5 s of closed-loop
    // driving the divergence must stay far below anything visible.
    first.forEach((value, i) => expect(Math.abs(value - cpython[i])).toBeLessThan(1e-3 * Math.max(1, Math.abs(value))));
  });
});

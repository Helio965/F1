#!/usr/bin/env node
// APEX physics benchmark under Pyodide (CPython compiled to WebAssembly) in Node.
//
// Usage (from the repository root, Node 22+):
//   node scripts/bench-physics.mjs              full run, JSON on stdout
//   node scripts/bench-physics.mjs --quick      reduced sample sizes
//   node scripts/bench-physics.mjs --compare    also run native CPython
//                                               (python3 -m apex_bridge.bench --json)
//                                               and print a ratio table (stderr)
//                                               plus a combined JSON (stdout)
// Options: --python <exe> (default: python3), --quiet (no progress on stderr).
//
// The Python sources of physics_engine, track_analyzer, racing_ai and
// apex_bridge are copied into the Pyodide virtual FS and the very same
// apex_bridge.bench.run_benchmarks() is executed. Nothing is fetched from the
// network: Pyodide is loaded from the installed npm package and no extra
// Python packages are needed (standard library only).

import { spawn } from "node:child_process";
import { readFile, readdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { fileURLToPath } from "node:url";
import { loadPyodide } from "pyodide";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PY_ROOT = path.join(ROOT, "python");
const PACKAGES = ["physics_engine", "track_analyzer", "racing_ai", "apex_bridge"];
const VFS_ROOT = "/home/pyodide/apex";
const IMPORTS = "import physics_engine.simulation, track_analyzer, racing_ai.driver, apex_bridge.api, apex_bridge.bench";

const args = process.argv.slice(2);
const flag = (name) => args.includes(name);
const option = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0 && i + 1 < args.length ? args[i + 1] : fallback;
};
const QUICK = flag("--quick");
const COMPARE = flag("--compare");
const QUIET = flag("--quiet");
const PYTHON = option("--python", "python3");

const log = (message) => {
  if (!QUIET) process.stderr.write(`[bench-physics] ${message}\n`);
};
const mb = (bytes) => Math.round((bytes / 1048576) * 10) / 10;
const round = (value, digits = 2) => {
  const k = 10 ** digits;
  return Math.round(value * k) / k;
};

async function listPythonFiles(dir) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    if (entry.name === "__pycache__" || entry.name.startsWith(".")) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await listPythonFiles(full)));
    else if (entry.isFile() && entry.name.endsWith(".py")) out.push(full);
  }
  return out;
}

function wasmHeapMb(pyodide) {
  const heap = pyodide?._module?.HEAP8;
  return heap ? round(heap.length / 1048576, 1) : null;
}

async function runPyodide() {
  const rssBefore = process.memoryUsage().rss;
  const t0 = performance.now();
  const pyodide = await loadPyodide({
    stdout: (line) => process.stderr.write(`${line}\n`),
    stderr: (line) => {
      if (!QUIET) process.stderr.write(`${line}\n`);
    },
  });
  const initMs = performance.now() - t0;
  const heapAfterInit = wasmHeapMb(pyodide);
  log(`loadPyodide ${initMs.toFixed(0)} ms (Pyodide ${pyodide.version})`);

  // Copy the four packages into the virtual FS.
  const t1 = performance.now();
  let files = 0;
  let bytes = 0;
  for (const pkg of PACKAGES) {
    const base = path.join(PY_ROOT, pkg);
    for (const file of await listPythonFiles(base)) {
      const rel = path.relative(PY_ROOT, file).split(path.sep).join("/");
      const target = `${VFS_ROOT}/${rel}`;
      pyodide.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
      const data = await readFile(file);
      pyodide.FS.writeFile(target, data);
      files += 1;
      bytes += data.length;
    }
  }
  const fsMs = performance.now() - t1;

  pyodide.runPython(`import sys\nif ${JSON.stringify(VFS_ROOT)} not in sys.path: sys.path.insert(0, ${JSON.stringify(VFS_ROOT)})`);
  const t2 = performance.now();
  pyodide.runPython(IMPORTS);
  const importMs = performance.now() - t2;
  log(`copied ${files} files (${(bytes / 1024).toFixed(0)} KiB) in ${fsMs.toFixed(1)} ms; import ${importMs.toFixed(0)} ms`);

  pyodide.globals.set("BENCH_QUICK", QUICK);
  pyodide.globals.set("BENCH_VERBOSE", !QUIET);
  const t3 = performance.now();
  const json = pyodide.runPython(`
import json, sys
import apex_bridge.bench as _bench

def _progress(message):
    print("[bench] " + message, file=sys.stderr)

json.dumps(_bench.run_benchmarks(quick=BENCH_QUICK, progress=_progress if BENCH_VERBOSE else None))
`);
  const benchMs = performance.now() - t3;
  const results = JSON.parse(json);

  return {
    runtime: "pyodide",
    node_version: process.version,
    pyodide_version: pyodide.version,
    python_version: results.environment?.python_version ?? null,
    init_ms: round(initMs, 1),
    fs_copy_ms: round(fsMs, 1),
    files_copied: files,
    source_kib: round(bytes / 1024, 1),
    import_ms: round(importMs, 1),
    bench_wall_s: round(benchMs / 1000, 2),
    wasm_heap_mb: wasmHeapMb(pyodide),
    wasm_heap_after_init_mb: heapAfterInit,
    node_rss_mb: mb(process.memoryUsage().rss),
    node_rss_before_init_mb: mb(rssBefore),
    host: { platform: process.platform, arch: process.arch, cpu: os.cpus()[0]?.model ?? null, cpus: os.cpus().length },
    results,
  };
}

function runChild(command, argv, options) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, argv, { ...options, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
      if (!QUIET) process.stderr.write(chunk);
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve(stdout);
      else reject(new Error(`${command} ${argv.join(" ")} exited with ${code}\n${stderr}`));
    });
  });
}

async function runCPython() {
  // Cold import (sources compiled, no .pyc read or written) to compare with
  // Pyodide, which always compiles in its fresh in-memory FS; warm = cached .pyc.
  const snippet = `import time\nt=time.perf_counter()\n${IMPORTS}\nprint((time.perf_counter()-t)*1000.0)`;
  const noCache = path.join(os.tmpdir(), `apex-bench-no-pycache-${process.pid}`); // never created (-B)
  const cold = Number((await runChild(PYTHON, ["-B", "-X", `pycache_prefix=${noCache}`, "-c", snippet], { cwd: PY_ROOT })).trim());
  const warm = Number((await runChild(PYTHON, ["-c", snippet], { cwd: PY_ROOT })).trim());
  const argv = ["-m", "apex_bridge.bench", "--json"];
  if (QUICK) argv.push("--quick");
  if (QUIET) argv.push("--quiet");
  const t0 = performance.now();
  const stdout = await runChild(PYTHON, argv, { cwd: PY_ROOT });
  const wallMs = performance.now() - t0;
  const results = JSON.parse(stdout);
  return {
    runtime: "cpython",
    python_version: results.environment?.python_version ?? null,
    import_cold_ms: round(cold, 1),
    import_warm_ms: round(warm, 1),
    process_wall_s: round(wallMs / 1000, 2),
    results,
  };
}

// ------------------------------------------------------------- comparison
function metricRows(cp, pd) {
  const rows = [];
  const add = (label, a, b, unit = "ms") => {
    if (typeof a === "number" && typeof b === "number") rows.push({ label, cpython: a, pyodide: b, unit, ratio: a > 0 ? b / a : null });
  };
  for (const name of Object.keys(cp.analysis)) {
    add(`analysis ${name} median`, cp.analysis[name].median_ms, pd.analysis[name]?.median_ms);
  }
  for (const name of Object.keys(cp.setup)) {
    add(`setup ${name} median`, cp.setup[name].median_ms, pd.setup[name]?.median_ms);
  }
  for (const name of Object.keys(cp.physics)) {
    const a = cp.physics[name];
    const b = pd.physics[name];
    if (!b) continue;
    for (const key of ["mean", "p50", "p95", "p99", "max"]) add(`step ${name} ${key}`, a.step_ms[key], b.step_ms[key]);
    add(`step ${name} per car`, a.per_car_ms, b.per_car_ms);
    add(`step ${name} AI mean`, a.ai_ms.mean, b.ai_ms.mean);
    add(`step ${name} AI share`, a.ai_share * 100, b.ai_share * 100, "%");
    add(`step ${name} RT load`, a.realtime_load * 100, b.realtime_load * 100, "%");
  }
  add("snapshot state (10 cars)", cp.snapshot.state_ms.mean, pd.snapshot.state_ms.mean);
  add("snapshot pack (advance 0 s)", cp.snapshot.pack_ms.mean, pd.snapshot.pack_ms.mean);
  add("snapshot per advance()", cp.snapshot.snapshot_ms, pd.snapshot.snapshot_ms);
  add("advance() 60 Hz frame mean", cp.snapshot.frame_60hz_ms.mean, pd.snapshot.frame_60hz_ms.mean);
  add("advance() 60 Hz frame p99", cp.snapshot.frame_60hz_ms.p99, pd.snapshot.frame_60hz_ms.p99);
  add("tracemalloc analysis peak", cp.memory.analysis_full_peak_mb, pd.memory.analysis_full_peak_mb, "MB");
  add("tracemalloc 10-car session peak", cp.memory.session10_peak_mb, pd.memory.session10_peak_mb, "MB");
  add("benchmark total", cp.total_s, pd.total_s, "s");
  return rows;
}

function formatTable(rows, cpy, pyo) {
  const fmt = (v) => (Math.abs(v) >= 100 ? v.toFixed(1) : Math.abs(v) >= 1 ? v.toFixed(3) : v.toFixed(4));
  const width = Math.max(...rows.map((r) => r.label.length), 10) + 2;
  const lines = [];
  lines.push(
    `CPython ${cpy.python_version} vs Pyodide ${pyo.pyodide_version} (Python ${pyo.python_version}, Node ${pyo.node_version}) - ${QUICK ? "quick" : "full"} mode`,
  );
  lines.push(`${"metric".padEnd(width)}${"CPython".padStart(12)}${"Pyodide".padStart(12)}  unit  ${"ratio".padStart(7)}`);
  lines.push("-".repeat(width + 12 + 12 + 8 + 7));
  for (const r of rows) {
    const ratio = r.ratio == null ? "   -" : `${r.ratio.toFixed(2)}x`;
    lines.push(`${r.label.padEnd(width)}${fmt(r.cpython).padStart(12)}${fmt(r.pyodide).padStart(12)}  ${r.unit.padEnd(4)}  ${ratio.padStart(7)}`);
  }
  lines.push("");
  lines.push(`Pyodide init (loadPyodide) ${pyo.init_ms} ms, FS copy ${pyo.fs_copy_ms} ms, import ${pyo.import_ms} ms`);
  lines.push(`CPython import: cold (no .pyc) ${cpy.import_cold_ms} ms, warm ${cpy.import_warm_ms} ms`);
  lines.push(`WASM heap ${pyo.wasm_heap_mb} MB (after init ${pyo.wasm_heap_after_init_mb} MB); Node RSS ${pyo.node_rss_mb} MB; CPython peak RSS ${cpy.results.memory.process_peak_rss_mb} MB`);
  const same = Object.keys(cpy.results.physics).map((k) => `${k}=${cpy.results.physics[k].fingerprint === pyo.results.physics[k]?.fingerprint ? "same" : "differs"}`);
  lines.push(`Trajectory fingerprints: ${same.join(", ")}`);
  return lines.join("\n");
}

async function main() {
  const major = Number(process.versions.node.split(".")[0]);
  if (major < 22) throw new Error(`Node 22+ required (found ${process.version})`);
  let cpython = null;
  if (COMPARE) {
    log(`running native ${PYTHON} -m apex_bridge.bench${QUICK ? " --quick" : ""} ...`);
    cpython = await runCPython();
  }
  log(`running Pyodide${QUICK ? " (quick)" : ""} ...`);
  const pyodide = await runPyodide();
  if (!COMPARE) {
    process.stdout.write(`${JSON.stringify(pyodide, null, 2)}\n`);
    return;
  }
  const rows = metricRows(cpython.results, pyodide.results);
  process.stderr.write(`\n${formatTable(rows, cpython, pyodide)}\n\n`);
  const ratios = Object.fromEntries(rows.filter((r) => r.ratio != null).map((r) => [r.label, round(r.ratio, 3)]));
  const fingerprints = Object.fromEntries(
    Object.keys(cpython.results.physics).map((k) => [k, cpython.results.physics[k].fingerprint === pyodide.results.physics[k]?.fingerprint]),
  );
  process.stdout.write(
    `${JSON.stringify({ mode: QUICK ? "quick" : "full", cpython, pyodide, ratios, identical_trajectories: fingerprints }, null, 2)}\n`,
  );
}

main().catch((error) => {
  process.stderr.write(`${error?.stack ?? error}\n`);
  process.exit(1);
});

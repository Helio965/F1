"""Reproducible performance benchmarks for CPython and Pyodide.

Native CPython (run from the ``python/`` directory)::

    python3 -m apex_bridge.bench            # full run, human-readable table
    python3 -m apex_bridge.bench --json     # full run, JSON on stdout
    python3 -m apex_bridge.bench --quick    # reduced sample sizes (< 20 s)

Under Pyodide (CPython compiled to WebAssembly) the very same module is
driven by ``scripts/bench-physics.mjs``, which calls :func:`run_benchmarks`
and can compare the result with a native run (``--compare``).

Every scenario is deterministic: fixed circuits, fixed seed, every car driven
by the AI (``autopilot``), fixed physics step of 1/120 s. Only wall-clock
times change between runs or runtimes; the ``fingerprint`` of each physics
scenario summarises the final car states so that runs can be checked for
identical trajectories. Times come from ``time.perf_counter``; percentiles
use linear interpolation between closest ranks. Standard library only.
"""

import gc
import hashlib
import json
import math
import platform
import sys
import time
import tracemalloc

from physics_engine import ENGINE_VERSION
from physics_engine.contract import CAR_SIZE, HEADER_SIZE
from physics_engine.simulation import FIXED_DT
from track_analyzer.analyzer import ANALYZER_VERSION, analyze

from . import api
from .session import Session

BENCH_VERSION = 1

# Default editor circuit (copy of tests/conftest.py::AURORA, ~1.0 km).
AURORA = {
    "id": "aurora-park",
    "version": 1,
    "name": "Aurora Park",
    "width": 13,
    "points": [
        {"x": -170, "y": 1, "z": 70},
        {"x": -170, "y": 1, "z": -65},
        {"x": -125, "y": 2, "z": -120},
        {"x": -10, "y": 4, "z": -120},
        {"x": 90, "y": 7, "z": -100},
        {"x": 150, "y": 8, "z": -45},
        {"x": 150, "y": 7, "z": 45},
        {"x": 115, "y": 6, "z": 70},
        {"x": 60, "y": 5, "z": 50},
        {"x": 20, "y": 4, "z": 90},
        {"x": 0, "y": 3, "z": 125},
        {"x": -35, "y": 1, "z": 150},
        {"x": -115, "y": 1, "z": 125},
    ],
}


def oval_document(straight=1200.0, radius=150.0, width=20.0, runoff=30.0, elevation=0.0):
    """Stadium oval (copy of tests/conftest.py::oval_document, ~3.3 km)."""
    pts = []
    half = straight / 2.0
    for k in range(9):
        pts.append({"x": -half + straight * k / 8.0, "y": elevation, "z": -radius})
    for k in range(1, 8):
        a = -math.pi / 2 + math.pi * k / 8.0
        pts.append({"x": half + radius * math.cos(a), "y": elevation, "z": radius * math.sin(a)})
    for k in range(9):
        pts.append({"x": half - straight * k / 8.0, "y": elevation, "z": radius})
    for k in range(1, 8):
        a = math.pi / 2 + math.pi * k / 8.0
        pts.append({"x": -half + radius * math.cos(a), "y": elevation, "z": radius * math.sin(a)})
    return {
        "id": "oval",
        "version": 1,
        "name": "Oval",
        "width": width,
        "points": pts,
        "runoff": {"type": "asphalt", "width": runoff},
    }


# Sample sizes. ``quick`` keeps a native run well under 20 s.
PLANS = {
    "quick": {
        "analysis_runs": 2,
        "warmup_steps": 120,  # 1 s simulated
        "measure_steps": 600,  # 5 s simulated
        "setup_runs": 2,
        "memory_steps": 24,  # tracemalloc slows Python ~20x
        "state_reps": 200,
        "frames": 120,  # advance() calls at 60 Hz (2 physics steps each)
    },
    "full": {
        "analysis_runs": 5,
        "warmup_steps": 600,  # 5 s simulated
        "measure_steps": 3600,  # 30 s simulated
        "setup_runs": 5,
        "memory_steps": 240,
        "state_reps": 2000,
        "frames": 1200,
    },
}

# (name, cars, weather)
SCENARIOS = (
    ("cars2_sun", 2, "sun"),
    ("cars6_sun", 6, "sun"),
    ("cars10_sun", 10, "sun"),
    ("cars10_rain", 10, "rain"),
)

SEED = 7
DIFFICULTY = 2
LAPS = 10  # long enough that nobody finishes inside the measurement window
NEUTRAL_INPUT = (0.0, 0.0, 0.0, False, True)  # ignored: autopilot drives car 0 too
BUDGET_MS = FIXED_DT * 1000.0  # 8.333 ms per step at 120 Hz


def session_config(cars, weather):
    return {
        "cars": cars,
        "laps": LAPS,
        "difficulty": DIFFICULTY,
        "weather": weather,
        "seed": SEED,
        "autopilot": True,
        "assists": "full",
    }


# ----------------------------------------------------------------- helpers
def runtime_name():
    if sys.platform == "emscripten":
        return "pyodide"
    return platform.python_implementation().lower()


def percentile(ordered, q):
    """Linear interpolation between closest ranks; ``ordered`` must be sorted."""
    if not ordered:
        return 0.0
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def summarize(samples, digits=4):
    ordered = sorted(samples)
    n = len(ordered)
    mean = sum(ordered) / n if n else 0.0
    var = sum((v - mean) ** 2 for v in ordered) / (n - 1) if n > 1 else 0.0
    return {
        "n": n,
        "mean": round(mean, digits),
        "std": round(math.sqrt(var), digits),
        "min": round(ordered[0] if n else 0.0, digits),
        "p50": round(percentile(ordered, 0.50), digits),
        "p95": round(percentile(ordered, 0.95), digits),
        "p99": round(percentile(ordered, 0.99), digits),
        "max": round(ordered[-1] if n else 0.0, digits),
    }


def _ms(t0, t1):
    return (t1 - t0) * 1000.0


def _mb(n_bytes):
    return round(n_bytes / 1048576.0, 3)


def environment_info():
    try:
        clock = time.get_clock_info("perf_counter")
        clock_info = {"implementation": clock.implementation, "resolution": clock.resolution}
    except Exception:  # pragma: no cover - defensive
        clock_info = {}
    return {
        "runtime": runtime_name(),
        "python_version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": sys.platform,
        "machine": platform.machine(),
        "engine_version": ENGINE_VERSION,
        "analyzer_version": ANALYZER_VERSION,
        "perf_counter": clock_info,
        "gc_thresholds": list(gc.get_threshold()),
    }


def _process_peak_rss_mb():
    """Peak resident set size of the whole process (CPython on POSIX only)."""
    try:
        import resource
    except ImportError:
        return None
    try:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:
        return None
    # Linux reports KiB, macOS bytes.
    return round(peak / 1048576.0, 1) if sys.platform == "darwin" else round(peak / 1024.0, 1)


def _fingerprint(session):
    """Short digest of the final car states (equal => identical trajectories)."""
    sim = session.sim
    parts = [str(sim.steps)]
    for car in sim.cars:
        parts.append("%.6f,%.6f,%.6f,%.6f" % (car.x, car.z, car.u, car.psi))
    return hashlib.sha256(";".join(parts).encode("ascii")).hexdigest()[:16]


# ---------------------------------------------------------------- analysis
def bench_analysis(plan, progress=None):
    circuits = (("aurora", AURORA), ("oval", oval_document()))
    out = {}
    analyses = {}
    for name, doc in circuits:
        runs = []
        internal = None
        result = None
        for _ in range(plan["analysis_runs"]):
            gc.collect()
            t0 = time.perf_counter()
            result = analyze(doc, "full")
            runs.append(_ms(t0, time.perf_counter()))
            internal = result.get("timings")
        ordered = sorted(runs)
        out[name] = {
            "valid": bool(result.get("valid")),
            "length_m": round(result.get("length", 0.0), 1),
            "table_points": len(result["physics_table"]["x"]) if result.get("valid") else 0,
            "corners": len(result.get("corners") or []),
            "runs": len(runs),
            "first_ms": round(runs[0], 2),
            "median_ms": round(percentile(ordered, 0.5), 2),
            "min_ms": round(ordered[0], 2),
            "max_ms": round(ordered[-1], 2),
            "stages_ms": {k: round(v, 2) for k, v in (internal or {}).items()},
        }
        analyses[name] = result
        if progress:
            progress("analysis %s: median %.1f ms" % (name, out[name]["median_ms"]))
    return out, analyses


# ----------------------------------------------------------------- physics
def _drive(session, steps, times=None, ai=None, events=None):
    """Run ``steps`` fixed steps; optionally record per-step and AI times (ms)."""
    sim = session.sim
    step_once = session.step_once
    ai_times = session.ai_times
    perf = time.perf_counter
    for k in range(steps):
        t0 = perf()
        step_once(NEUTRAL_INPUT)
        t1 = perf()
        if times is not None:
            times.append((t1 - t0) * 1000.0)
            ai.append(ai_times[-1])
        if k % 120 == 119:  # drain like the worker does, outside the timed region
            for ev in sim.drain_events():
                if events is not None:
                    events[ev["type"]] = events.get(ev["type"], 0) + 1


def bench_physics_scenario(analysis, cars, weather, plan, progress=None, name=""):
    gc.collect()
    t0 = time.perf_counter()
    session = Session(analysis, session_config(cars, weather))
    setup_ms = _ms(t0, time.perf_counter())
    _drive(session, plan["warmup_steps"])
    session.step_times.clear()
    session.ai_times.clear()
    gc.collect()
    gc_before = sum(s.get("collections", 0) for s in gc.get_stats())
    times, ai, events = [], [], {}
    t_start = time.perf_counter()
    _drive(session, plan["measure_steps"], times, ai, events)
    wall_ms = _ms(t_start, time.perf_counter())
    gc_after = sum(s.get("collections", 0) for s in gc.get_stats())
    step = summarize(times)
    ai_stats = summarize(ai)
    mean = sum(times) / len(times)
    ai_mean = sum(ai) / len(ai)
    sim = session.sim
    speeds = [car.u for car in sim.cars]
    result = {
        "cars": cars,
        "weather": weather,
        "compound": sim.cars[0].compound_id,
        "setup_ms": round(setup_ms, 2),
        "warmup_steps": plan["warmup_steps"],
        "steps": plan["measure_steps"],
        "simulated_s": round(plan["measure_steps"] * FIXED_DT, 3),
        "wall_ms": round(wall_ms, 1),
        "step_ms": step,
        "ai_ms": ai_stats,
        "physics_ms_mean": round(mean - ai_mean, 4),
        "per_car_ms": round(mean / cars, 4),
        "ai_share": round(ai_mean / mean, 4) if mean > 0 else 0.0,
        "realtime_load": round(mean / BUDGET_MS, 4),
        "realtime_load_p99": round(step["p99"] / BUDGET_MS, 4),
        "steps_per_s": round(1000.0 / mean, 1) if mean > 0 else 0.0,
        "gc_collections": gc_after - gc_before,
        "events": events,
        "mean_speed_kmh": round(3.6 * sum(speeds) / len(speeds), 2),
        "laps_completed": sum(t.completed for t in sim.trackers),
        "fingerprint": _fingerprint(session),
        "session_metrics": session.metrics(),
    }
    if progress:
        progress(
            "physics %s: mean %.3f ms p99 %.3f ms load %.1f%%"
            % (name, step["mean"], step["p99"], 100.0 * result["realtime_load"])
        )
    return result


def bench_physics(analysis, plan, progress=None):
    return {
        name: bench_physics_scenario(analysis, cars, weather, plan, progress, name)
        for name, cars, weather in SCENARIOS
    }


# ------------------------------------------------------------------- setup
def bench_setup(analysis, plan, progress=None):
    out = {}
    for name, cars, weather in (("cars2_sun", 2, "sun"), ("cars10_sun", 10, "sun"), ("cars10_rain", 10, "rain")):
        total, internal = [], []
        for _ in range(plan["setup_runs"]):
            gc.collect()
            t0 = time.perf_counter()
            session = Session(analysis, session_config(cars, weather))
            total.append(_ms(t0, time.perf_counter()))
            internal.append(session.setup_ms)
        ordered = sorted(total)
        out[name] = {
            "runs": len(total),
            "first_ms": round(total[0], 2),
            "median_ms": round(percentile(ordered, 0.5), 2),
            "min_ms": round(ordered[0], 2),
            # Session.setup_ms: Simulation + SpeedPlan only (no AI drivers / first snapshot)
            "sim_and_plan_median_ms": round(percentile(sorted(internal), 0.5), 2),
        }
        if progress:
            progress("setup %s: median %.1f ms" % (name, out[name]["median_ms"]))
    return out


# ------------------------------------------------------------------ memory
def bench_memory(plan, progress=None):
    """Peak Python heap (tracemalloc) for a full analysis and a 10-car session.

    tracemalloc only sees allocations made through Python's allocators; it
    excludes interpreter/runtime overhead (and, under Pyodide, the WASM heap
    reserve). It also slows execution, so no timing is taken here.
    """
    gc.collect()
    was_tracing = tracemalloc.is_tracing()
    if not was_tracing:
        tracemalloc.start()
    try:
        tracemalloc.clear_traces()
        base, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        analysis = analyze(AURORA, "full")
        current, peak = tracemalloc.get_traced_memory()
        analysis_peak = peak - base
        analysis_retained = current - base

        base, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        session = Session(analysis, session_config(10, "rain"))
        after_setup, setup_peak = tracemalloc.get_traced_memory()
        api._session = session
        try:
            for _ in range(plan["memory_steps"] // 2):
                api.advance(2.0 * FIXED_DT, 0.0, 0.0, 0.0, False, True)
                session.sim.drain_events()
        finally:
            api._session = None
        current, peak = tracemalloc.get_traced_memory()
        result = {
            "method": "tracemalloc",
            "analysis_full_peak_mb": _mb(analysis_peak),
            "analysis_result_mb": _mb(analysis_retained),
            "session10_setup_peak_mb": _mb(setup_peak - base),
            "session10_retained_after_setup_mb": _mb(after_setup - base),
            "session10_peak_mb": _mb(peak - base),
            "session10_retained_mb": _mb(current - base),
            "session10_steps": 2 * (plan["memory_steps"] // 2),
            "session10_weather": "rain",
            "peak_total_mb": _mb(peak),
        }
        del session
    finally:
        if not was_tracing:
            tracemalloc.stop()
    gc.collect()
    result["process_peak_rss_mb"] = _process_peak_rss_mb()
    if progress:
        progress(
            "memory: analysis peak %.2f MB, 10-car session peak %.2f MB"
            % (result["analysis_full_peak_mb"], result["session10_peak_mb"])
        )
    return result


# ---------------------------------------------------------------- snapshot
def bench_snapshot(analysis, plan, progress=None):
    """Cost of the per-frame snapshot that ``advance()`` hands to the worker.

    * ``state_ms``: one ``Session._state()`` (all cars, CAR_SIZE floats each).
    * ``pack_ms``: ``api.advance`` with ``elapsed=0`` - no physics step, only the
      header, the ``prev + curr`` concatenation and the ``array('d')`` copy.
    * ``snapshot_ms`` = 2 x state + pack: what one ``advance()`` call that runs
      at least one step spends outside the physics (prev and curr states).
    * ``frame_60hz_ms``: real ``api.advance(1/60)`` calls (2 steps + snapshot).
    """
    session = Session(analysis, session_config(10, "sun"))
    _drive(session, plan["warmup_steps"])
    api._session = session
    try:
        perf = time.perf_counter
        state, pack = [], []
        reps = plan["state_reps"]
        for _ in range(reps):
            t0 = perf()
            session._state()
            t1 = perf()
            api.advance(0.0, 0.0, 0.0, 0.0, False, True)
            t2 = perf()
            state.append((t1 - t0) * 1000.0)
            pack.append((t2 - t1) * 1000.0)
        frame = []
        length = 0
        for k in range(plan["frames"]):
            t0 = perf()
            out = api.advance(2.0 * FIXED_DT, 0.0, 0.0, 0.0, False, True)
            frame.append((perf() - t0) * 1000.0)
            length = len(out)
            if k % 60 == 59:
                session.sim.drain_events()
    finally:
        api._session = None
    state_s = summarize(state)
    pack_s = summarize(pack)
    frame_s = summarize(frame)
    result = {
        "cars": 10,
        "floats": length,
        "layout": {"header": HEADER_SIZE, "car": CAR_SIZE},
        "state_ms": state_s,
        "pack_ms": pack_s,
        "snapshot_ms": round(2.0 * state_s["mean"] + pack_s["mean"], 4),
        "frame_60hz_ms": frame_s,
        "frames": plan["frames"],
    }
    if progress:
        progress("snapshot: %.4f ms per advance() snapshot (%d floats)" % (result["snapshot_ms"], length))
    return result


# --------------------------------------------------------------------- run
def run_benchmarks(quick=False, progress=None):
    """Run every benchmark and return a JSON-serialisable dict."""
    mode = "quick" if quick else "full"
    plan = PLANS[mode]
    t0 = time.perf_counter()
    report = {
        "bench_version": BENCH_VERSION,
        "mode": mode,
        "plan": dict(plan),
        "environment": environment_info(),
        "config": {
            "circuit": AURORA["id"],
            "difficulty": DIFFICULTY,
            "seed": SEED,
            "laps": LAPS,
            "autopilot": True,
            "fixed_dt": FIXED_DT,
            "budget_ms": round(BUDGET_MS, 4),
        },
    }
    sections = {}

    def timed(key, fn, *args):
        t = time.perf_counter()
        value = fn(*args)
        sections[key] = round(time.perf_counter() - t, 2)
        return value

    analysis_out, analyses = timed("analysis", bench_analysis, plan, progress)
    report["analysis"] = analysis_out
    aurora = analyses["aurora"]
    report["setup"] = timed("setup", bench_setup, aurora, plan, progress)
    report["physics"] = timed("physics", bench_physics, aurora, plan, progress)
    report["snapshot"] = timed("snapshot", bench_snapshot, aurora, plan, progress)
    report["memory"] = timed("memory", bench_memory, plan, progress)
    report["sections_s"] = sections
    report["total_s"] = round(time.perf_counter() - t0, 2)
    return report


def browser_summary(report):
    """Flat view of a report for the in-game benchmark panel (main.ts)."""
    physics = []
    for case in report["physics"].values():
        step = case["step_ms"]
        physics.append(
            {
                "cars": case["cars"],
                "weather": case["weather"],
                "step_mean_ms": step["mean"],
                "step_p95_ms": step["p95"],
                "step_p99_ms": step["p99"],
                "step_max_ms": step["max"],
                "per_car_ms": case["per_car_ms"],
                "ai_mean_ms": case["ai_ms"]["mean"],
                "realtime_load": case["realtime_load"],
                "realtime_load_p99": case["realtime_load_p99"],
            }
        )
    memory = report["memory"]
    return {
        "mode": report["mode"],
        "physics": physics,
        "analysis": {name: entry["median_ms"] for name, entry in report["analysis"].items()},
        "snapshot": {
            "snapshot_ms": report["snapshot"]["snapshot_ms"],
            "frame_60hz_mean_ms": report["snapshot"]["frame_60hz_ms"]["mean"],
            "frame_60hz_p99_ms": report["snapshot"]["frame_60hz_ms"]["p99"],
        },
        "memory": {
            "peak_mb": memory["session10_peak_mb"],
            "analysis_peak_mb": memory["analysis_full_peak_mb"],
        },
        "environment": report["environment"],
        "total_s": report["total_s"],
        "report": report,
    }


def run_benchmarks_json(quick=True):
    """Entry point used by the Web Worker: JSON text of :func:`browser_summary`."""
    return json.dumps(browser_summary(run_benchmarks(bool(quick))))


# --------------------------------------------------------------------- CLI
def format_report(report):
    lines = []
    env = report["environment"]
    lines.append(
        "APEX bench v%d (%s) - %s %s on %s"
        % (report["bench_version"], report["mode"], env["runtime"], env["python_version"], env["platform"])
    )
    lines.append("")
    lines.append("Analysis (full)        median ms     min ms   length m")
    for name, a in report["analysis"].items():
        lines.append("  %-20s %9.1f  %9.1f  %9.1f" % (name, a["median_ms"], a["min_ms"], a["length_m"]))
    lines.append("")
    lines.append("Session setup          median ms     min ms")
    for name, s in report["setup"].items():
        lines.append("  %-20s %9.1f  %9.1f" % (name, s["median_ms"], s["min_ms"]))
    lines.append("")
    lines.append(
        "Physics step (ms)         mean     p50     p95     p99     max  per-car  AI%  load%"
    )
    for name, p in report["physics"].items():
        s = p["step_ms"]
        lines.append(
            "  %-20s %7.3f %7.3f %7.3f %7.3f %7.3f  %7.4f %4.0f %6.1f"
            % (
                name,
                s["mean"],
                s["p50"],
                s["p95"],
                s["p99"],
                s["max"],
                p["per_car_ms"],
                100.0 * p["ai_share"],
                100.0 * p["realtime_load"],
            )
        )
    snap = report["snapshot"]
    lines.append("")
    lines.append(
        "Snapshot (10 cars, %d floats): state %.4f ms, pack %.4f ms, per advance() %.4f ms; 60 Hz frame mean %.3f ms p99 %.3f ms"
        % (
            snap["floats"],
            snap["state_ms"]["mean"],
            snap["pack_ms"]["mean"],
            snap["snapshot_ms"],
            snap["frame_60hz_ms"]["mean"],
            snap["frame_60hz_ms"]["p99"],
        )
    )
    mem = report["memory"]
    lines.append(
        "Memory (tracemalloc): analysis peak %.2f MB, 10-car session peak %.2f MB (retained %.2f MB); process peak RSS %s MB"
        % (
            mem["analysis_full_peak_mb"],
            mem["session10_peak_mb"],
            mem["session10_retained_mb"],
            mem["process_peak_rss_mb"],
        )
    )
    lines.append("Total %.1f s" % report["total_s"])
    return "\n".join(lines)


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="APEX physics/analysis benchmarks (stdlib only).")
    parser.add_argument("--quick", action="store_true", help="reduced sample sizes (< 20 s natively)")
    parser.add_argument("--json", action="store_true", help="print the JSON report on stdout")
    parser.add_argument("--quiet", action="store_true", help="no progress lines on stderr")
    args = parser.parse_args(argv)

    def progress(message):
        print("[bench] " + message, file=sys.stderr, flush=True)

    report = run_benchmarks(quick=args.quick, progress=None if args.quiet else progress)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())

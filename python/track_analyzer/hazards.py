"""Detection of potentially dangerous areas (warnings, not physics rules).

Every hazard is derived from geometry plus the reference speed profile; the
thresholds are documented game-design choices (docs/TRACK_ANALYSIS.md).
"""

import math

GRAVITY = 9.80665


def detect(table, layout, corners, profile, clearance_left, clearance_right):
    n = len(table["x"])
    ds = table["spacing"]
    speed = profile["speed"]
    vcurv = table["vertical_curvature"]
    grade = table["grade"]
    bank = table["bank"]
    curvature = table["curvature"]
    half = layout["half_width"]
    hazards = []

    def add(kind, i, severity, message, **extra):
        item = {"type": kind, "s": round(i * ds, 1), "severity": severity, "message": message}
        item.update(extra)
        hazards.append(item)

    # 1. crests: load loss (or take-off) at reference speed
    flagged = -1e9
    for i in range(n):
        v = speed[i]
        g_n = GRAVITY + v * v * vcurv[i]
        if vcurv[i] < -0.002 and g_n < 0.6 * GRAVITY and i * ds - flagged > 60.0:
            corner_near = any(abs(curvature[(i + d) % n]) > 1.0 / 150.0 for d in range(-int(40 / ds), int(40 / ds)))
            if g_n <= 0.0:
                add("crest.airborne", i, "high", "Crista onde o carro pode perder contato com o solo na velocidade de referência.",
                    normal_g=round(g_n / GRAVITY, 2))
            elif corner_near:
                add("crest.corner", i, "high", "Crista combinada com curva: a carga nos pneus cai antes da tomada.",
                    normal_g=round(g_n / GRAVITY, 2))
            else:
                add("crest", i, "medium", "Crista rápida: aderência reduzida pela perda de carga vertical.",
                    normal_g=round(g_n / GRAVITY, 2))
            flagged = i * ds
    # 2. compressions (bottoming risk)
    flagged = -1e9
    for i in range(n):
        v = speed[i]
        extra_g = v * v * vcurv[i] / GRAVITY
        if extra_g > 1.2 and i * ds - flagged > 60.0:
            add("compression", i, "medium", "Compressão forte: risco de o assoalho tocar o solo.", extra_g=round(extra_g, 2))
            flagged = i * ds
    # 3. short run-off at high speed on the outside of corners
    for c in corners:
        apex = c["apex_index"]
        v = speed[apex]
        outside = layout["barrier_right"] if c["direction"] == "left" else layout["barrier_left"]
        room = min(outside[(apex + d) % n] for d in range(0, max(1, int(60 / ds)))) - half[apex]
        if v > 45.0 and room < 6.0:
            add("runoff.short", apex, "high",
                f"Curva {c['number']}: escape curto ({room:.1f} m) para a velocidade de referência.",
                corner=c["number"], runoff=round(room, 1), speed_kmh=round(v * 3.6))
    # 4. severe braking zones
    v = speed
    for c in corners:
        start = c["start_index"]
        before = max(v[(start - d) % n] for d in range(0, int(400 / ds)))
        drop = before - v[c["apex_index"]]
        if drop * 3.6 > 170.0:
            add("braking.severe", start, "medium",
                f"Curva {c['number']}: frenagem de {before * 3.6:.0f} para {v[c['apex_index']] * 3.6:.0f} km/h.",
                corner=c["number"])
    # 5. off-camber high-speed corners (banking tilts toward the outside)
    flagged = -1e9
    for i in range(n):
        k = curvature[i]
        b = bank[i]
        if abs(k) > 1.0 / 300.0 and k * b > 0.0 and abs(math.degrees(b)) > 2.0 and speed[i] > 35.0:
            if i * ds - flagged > 80.0:
                add("camber.adverse", i, "medium", "Inclinação lateral contrária à curva (off-camber) em trecho rápido.")
                flagged = i * ds
    # 6. puddle-prone sag points: local minima of elevation with little slope
    ys = table["y"]
    window = max(2, int(25.0 / ds))
    flagged = -1e9
    for i in range(n):
        y = ys[i]
        if all(y <= ys[(i + d) % n] for d in range(-window, window + 1)) and abs(math.sin(bank[i])) < 0.01:
            neighbourhood = max(ys[(i + d) % n] for d in range(-4 * window, 4 * window + 1))
            if neighbourhood - y > 0.4 and i * ds - flagged > 60.0:
                add("water.puddle", i, "low", "Ponto baixo com pouca inclinação lateral: acumula água na chuva.")
                flagged = i * ds
    # 7. close parallel sections
    flagged = -1e9
    for i in range(n):
        gap = min(clearance_left[i], clearance_right[i])
        if gap < 2.0 * half[i] + 10.0 and speed[i] > 40.0 and i * ds - flagged > 100.0:
            add("proximity", i, "medium", "Trecho rápido muito próximo de outra parte do circuito.", gap=round(gap, 1))
            flagged = i * ds
    # 8. blind corner: crest within 80 m before a slow corner
    for c in corners:
        if c["kind"] not in ("slow", "hairpin"):
            continue
        start = c["start_index"]
        if any(vcurv[(start - d) % n] < -0.003 for d in range(0, int(80 / ds))):
            add("corner.blind", start, "medium", f"Curva {c['number']}: entrada cega logo após uma crista.", corner=c["number"])
    hazards.sort(key=lambda h: h["s"])
    return hazards

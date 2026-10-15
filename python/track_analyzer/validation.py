"""Geometric validation (errors block racing, warnings inform).

Each issue has a stable ``code`` (for tests/UI logic) and a Portuguese
``message`` (shown to the player). Limits keep the physics and rendering in
their tested envelope; they are game-design limits, not FIA circuit rules.
"""

import math

from .geometry import segments_cross, validation_line

MIN_POINTS = 6
MAX_POINTS = 128
MIN_WIDTH = 8.0
MAX_WIDTH = 24.0
MAX_COORDINATE = 10_000.0
MAX_ELEVATION = 400.0
MIN_LENGTH = 300.0
MAX_LENGTH = 15_000.0
MIN_POINT_SPACING = 6.0
MAX_GRADE = 0.18
WARN_GRADE = 0.08
MAX_BANK_DEG = 12.0
WARN_BANK_DEG = 8.0
MIN_RUNOFF = 1.5
MAX_RUNOFF = 40.0


def issue(code, message, **extra):
    data = {"code": code, "message": message}
    data.update(extra)
    return data


def validate_document(circuit):
    """Checks that do not need the spline. Returns (errors, warnings, fatal)."""
    errors, warnings = [], []
    pts = circuit["points"]
    if len(pts) < MIN_POINTS:
        errors.append(issue("points.too_few", "Use pelo menos 6 pontos de controle para fechar um circuito suave."))
    if len(pts) > MAX_POINTS:
        errors.append(issue("points.too_many", "O limite é de 128 pontos de controle."))
    w = circuit["width"]
    if not math.isfinite(w) or w < MIN_WIDTH or w > MAX_WIDTH:
        errors.append(issue("width.range", "A pista precisa ter entre 8 e 24 metros de largura."))
    bad = any(
        not all(math.isfinite(p[k]) for k in ("x", "y", "z", "bank"))
        or abs(p["x"]) > MAX_COORDINATE
        or abs(p["z"]) > MAX_COORDINATE
        or abs(p["y"]) > MAX_ELEVATION
        for p in pts
    )
    if bad:
        errors.append(issue("points.invalid", "Há coordenadas inválidas ou fora da área permitida."))
        return errors, warnings, True
    if len(pts) < 3:
        return errors, warnings, True
    banks = [abs(p["bank"]) for p in pts]
    if max(banks) > MAX_BANK_DEG:
        errors.append(issue("bank.range", "Inclinação lateral acima de 12°. Reduza a inclinação lateral dos pontos."))
    elif max(banks) > WARN_BANK_DEG:
        warnings.append(issue("bank.high", "Inclinação lateral acentuada: a carga nos pneus muda bastante na curva."))
    rw = circuit["runoff"]["width"]
    if not math.isfinite(rw) or rw < MIN_RUNOFF or rw > MAX_RUNOFF:
        errors.append(issue("runoff.range", "A área de escape precisa ter entre 1,5 e 40 metros."))
    exposure = circuit["environment"]["exposure"]
    if not math.isfinite(exposure) or exposure < 0.0 or exposure > 1.0:
        errors.append(issue("environment.exposure", "A exposição à chuva deve estar entre 0 e 1."))
    alt = circuit["environment"]["altitude"]
    if not math.isfinite(alt) or alt < -500.0 or alt > 4500.0:
        errors.append(issue("environment.altitude", "A altitude deve estar entre -500 e 4500 metros."))
    duplicate = False
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        if math.hypot(a["x"] - b["x"], a["z"] - b["z"]) < MIN_POINT_SPACING:
            duplicate = True
    if duplicate:
        errors.append(
            issue(
                "points.too_close",
                "Pontos vizinhos muito próximos: mantenha pelo menos 6 metros, inclusive no fechamento.",
            )
        )
    return errors, warnings, False


def validate_geometry(circuit, spline, table):
    """Checks on the interpolated centre line."""
    errors, warnings = [], []
    length = spline.length
    width = circuit["width"]
    if length < MIN_LENGTH:
        errors.append(
            issue("length.short", "O circuito precisa ter pelo menos 300 metros para acomodar o grid e uma volta.")
        )
    if length > MAX_LENGTH:
        errors.append(issue("length.long", "O circuito excede o limite de 15 km."))
    line = validation_line(spline)
    sub = line[::3]
    crossing = False
    overlapping = False
    local = max(width * 3.0, 24.0)
    m = len(sub)
    for i in range(m):
        a = sub[i]
        a2 = sub[(i + 1) % m]
        for j in range(i + 2, m):
            if i == 0 and j == m - 1:
                continue
            b = sub[j]
            along = abs(b[3] - a[3])
            apart = min(along, length - along)
            if apart < local:
                continue
            if not crossing and segments_cross(a, a2, b, sub[(j + 1) % m]):
                crossing = True
            if (
                not overlapping
                and math.hypot(a[0] - b[0], a[2] - b[2]) < width * 1.12
                and abs(a[1] - b[1]) < 5.0
            ):
                overlapping = True
        if crossing and overlapping:
            break
    if crossing:
        errors.append(
            issue("geometry.crossing", "A pista cruza a si mesma. Separe os trechos para formar uma volta contínua.")
        )
    elif overlapping:
        errors.append(
            issue("geometry.overlap", "Há trechos sem espaço para a largura da pista. Afaste as curvas vizinhas.")
        )
    tightest = max(abs(k) for k in table["curvature"])
    if tightest > 1.0 / (width * 0.65):
        errors.append(
            issue(
                "curvature.too_tight",
                "Uma curva é fechada demais para essa largura. Afaste os pontos da curva.",
                min_radius=round(1.0 / tightest, 2),
            )
        )
    elif tightest > 1.0 / 18.0:
        warnings.append(issue("curvature.slow", "Há curvas lentas: freie antes da entrada."))
    max_grade = max(abs(g) for g in table["grade"])
    if max_grade > MAX_GRADE:
        errors.append(issue("grade.too_steep", "Inclinação acima de 18%. Suavize a elevação entre os pontos."))
    elif max_grade > WARN_GRADE:
        warnings.append(issue("grade.steep", "Há subidas íngremes; atenção à aderência nas cristas."))
    if length < 700.0:
        warnings.append(issue("length.compact", "Traçado compacto: um grid menor deixa a largada mais espaçada."))
    return errors, warnings

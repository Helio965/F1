"""Historical circuit records (1980 to the present) with provenance.

A historical record wraps an ordinary circuit document (the same input as
:func:`track_analyzer.analyzer.analyze`) with the metadata needed to say
*where the geometry came from* and *how far it has been checked*::

    {
      "catalog": 1,
      "id": "example-layout-1985",
      "name": "Circuito Exemplo",
      "layout": "Traçado curto",
      "country": "BR",
      "years": {"from": 1985, "to": 1989},       # "to": null = still in use
      "reference_length_m": 3800.0,              # published lap length, optional
      "provenance": {
        "geometry": "published-map",             # see GEOMETRY_PROVENANCE
        "sources": [{"title": "...", "url": "https://..."}],
        "estimated": ["width", "bank", "runoff"],  # fields that are estimates
        "notes": "..."
      },
      "document": { ...circuit document (schema 2)... }
    }

Rules enforced here:

* The physics never reads anything from the record except ``document``: a
  historical track is analysed by the same analyser and driven with the same
  engine as any user track. Cached analyses are derived data keyed by the
  analysis hash, never physical laws.
* Every physical field that is not measured must be listed in
  ``provenance.estimated``; the result exposes that list so the interface can
  flag those values as estimates.
* The validation state is *computed* (``rejected``, ``unverified``,
  ``geometry-checked``, ``length-matched``); a record cannot declare itself
  validated.

No geometry of real circuits is bundled: records must come with their own
sources. Nothing here downloads, generates or completes geometry.
"""

import datetime

from .analyzer import analyze

CATALOG_VERSION = 1
FIRST_YEAR = 1980
GEOMETRY_PROVENANCE = {
    # value: (description, can reach "length-matched")
    "survey": ("Levantamento topográfico/CAD do traçado", True),
    "published-map": ("Mapa oficial ou publicado, digitalizado", True),
    "satellite-trace": ("Traçado sobre imagem aérea ou de satélite", True),
    "approximate": ("Aproximação a partir de esboços ou memória", False),
    "user": ("Criado pelo usuário, sem fonte externa", False),
}
ESTIMABLE_FIELDS = ("width", "elevation", "bank", "surface", "runoff", "environment")
LENGTH_TOLERANCE = 0.01  # 1 % of the published length
VALIDATION_STATES = ("rejected", "unverified", "geometry-checked", "length-matched")
_ALLOWED_KEYS = {
    "catalog", "id", "name", "layout", "country", "years",
    "reference_length_m", "provenance", "document",
}


def _issue(issues, code, severity, message):
    issues.append({"code": code, "severity": severity, "message": message})


def validate_record(record, current_year=None):
    """Check the metadata of a record (not its geometry). Returns a list of issues."""
    issues = []
    if not isinstance(record, dict):
        _issue(issues, "record.type", "error", "O registro deve ser um objeto JSON.")
        return issues
    year_now = current_year or datetime.date.today().year
    unknown = sorted(set(record) - _ALLOWED_KEYS)
    if unknown:
        _issue(issues, "record.unknown", "error", "Campos desconhecidos: %s." % ", ".join(unknown))
    if record.get("catalog") != CATALOG_VERSION:
        _issue(issues, "record.catalog", "error", "Versão de catálogo não suportada.")
    for key in ("id", "name", "layout"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 120:
            _issue(issues, "record.%s" % key, "error", "Campo '%s' ausente ou inválido." % key)
    years = record.get("years")
    if not isinstance(years, dict):
        _issue(issues, "years.missing", "error", "Informe o período de uso do traçado.")
    else:
        first, last = years.get("from"), years.get("to")
        if not isinstance(first, int) or isinstance(first, bool) or not FIRST_YEAR <= first <= year_now:
            _issue(issues, "years.from", "error", "Ano inicial deve estar entre %d e %d." % (FIRST_YEAR, year_now))
        elif last is not None and (
            not isinstance(last, int) or isinstance(last, bool) or not first <= last <= year_now
        ):
            _issue(issues, "years.to", "error", "Ano final deve ser nulo ou estar entre o inicial e %d." % year_now)
    reference = record.get("reference_length_m")
    if reference is not None and (
        isinstance(reference, bool) or not isinstance(reference, (int, float)) or not 500.0 <= reference <= 30000.0
    ):
        _issue(issues, "length.reference", "error", "Comprimento de referência deve estar entre 500 m e 30 km.")
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        _issue(issues, "provenance.missing", "error", "Informe a procedência da geometria.")
    else:
        kind = provenance.get("geometry")
        if kind not in GEOMETRY_PROVENANCE:
            _issue(issues, "provenance.geometry", "error", "Procedência da geometria desconhecida.")
        sources = provenance.get("sources") or []
        if not isinstance(sources, list) or not all(
            isinstance(s, dict) and isinstance(s.get("title"), str) and s["title"].strip() for s in sources
        ):
            _issue(issues, "provenance.sources", "error", "Cada fonte precisa de um título.")
        elif kind in GEOMETRY_PROVENANCE and GEOMETRY_PROVENANCE[kind][1] and not sources:
            _issue(issues, "provenance.sources", "error", "Geometria '%s' exige ao menos uma fonte." % kind)
        estimated = provenance.get("estimated", [])
        if not isinstance(estimated, list) or any(f not in ESTIMABLE_FIELDS for f in estimated):
            _issue(
                issues, "provenance.estimated", "error",
                "Campos estimados devem estar entre: %s." % ", ".join(ESTIMABLE_FIELDS),
            )
        if kind in ("approximate", "user"):
            _issue(
                issues, "provenance.approximate", "warning",
                "Geometria aproximada: o traçado não reproduz o circuito real com precisão.",
            )
    if not isinstance(record.get("document"), dict):
        _issue(issues, "document.missing", "error", "O registro não contém o documento do circuito.")
    return issues


def analyze_record(record, level="full", current_year=None):
    """Validate a historical record and analyse its circuit with the standard analyser.

    Returns ``{"record", "state", "issues", "estimated", "length_error", "analysis"}``.
    ``state`` is computed from the evidence, never read from the record.
    """
    issues = validate_record(record, current_year)
    meta_ok = not any(i["severity"] == "error" for i in issues)
    analysis = analyze(record["document"], level) if isinstance(record, dict) and isinstance(record.get("document"), dict) else None
    provenance = (record.get("provenance") if isinstance(record, dict) else None) or {}
    estimated = list(provenance.get("estimated") or []) if isinstance(provenance.get("estimated"), list) else []
    length_error = None
    if analysis is None or not meta_ok or not analysis.get("valid"):
        state = "rejected"
    else:
        state = "geometry-checked"
        reference = record.get("reference_length_m")
        kind = provenance.get("geometry")
        if reference:
            length_error = (analysis["length"] - reference) / reference
            if abs(length_error) > LENGTH_TOLERANCE:
                _issue(
                    issues, "length.mismatch", "warning",
                    "Comprimento analisado difere %.1f%% do publicado." % (100.0 * length_error),
                )
            elif GEOMETRY_PROVENANCE[kind][1]:
                state = "length-matched"
        if kind in ("approximate", "user") or not reference:
            state = "geometry-checked" if state != "length-matched" else state
    years = record.get("years") if isinstance(record, dict) else None
    return {
        "catalog": CATALOG_VERSION,
        "record": {
            "id": record.get("id") if isinstance(record, dict) else None,
            "name": record.get("name") if isinstance(record, dict) else None,
            "layout": record.get("layout") if isinstance(record, dict) else None,
            "country": record.get("country") if isinstance(record, dict) else None,
            "years": years,
            "provenance": provenance.get("geometry"),
            "sources": provenance.get("sources") or [],
        },
        "state": state,
        "issues": issues,
        "estimated": estimated,
        "length_error": None if length_error is None else round(length_error, 5),
        "analysis": analysis,
    }

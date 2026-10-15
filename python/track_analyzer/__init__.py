"""APEX automatic track analyser (deterministic, pure Python).

Entry point: :func:`track_analyzer.analyzer.analyze`.
"""

from .analyzer import ANALYZER_VERSION, analysis_hash, analyze  # noqa: F401

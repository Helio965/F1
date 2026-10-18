"""Five deterministic skill levels.

Levels differ only in driver behaviour - reaction time, precision,
consistency, how much of the computed grip they dare to use, traction/lock-up
management, racecraft aggression - never in car performance. All AI cars use
the same vehicle parameters and physics as the player.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Skill:
    id: int
    name: str
    grip_usage: float  # fraction of the QSS grip limit used for target speeds
    reaction_time: float  # s, perception delay for opponents and surprises
    lateral_noise: float  # m, amplitude of path-following imprecision
    consistency: float  # relative std. dev. of corner speed targets per lap
    slip_management: float  # 0..1, how well wheelspin/lock-ups are corrected
    aggression: float  # 0..1, willingness to attempt overtakes
    defends: bool
    lookahead_gain: float  # preview distance multiplier (smaller = sharper)
    braking_margin: float  # extra metres of braking anticipation


SKILLS = (
    Skill(0, "Fácil", 0.80, 0.45, 0.45, 0.035, 0.55, 0.15, False, 1.15, 14.0),
    Skill(1, "Intermediário", 0.855, 0.36, 0.32, 0.025, 0.68, 0.30, False, 1.10, 9.0),
    Skill(2, "Médio", 0.90, 0.28, 0.22, 0.018, 0.80, 0.50, True, 1.05, 6.0),
    Skill(3, "Difícil", 0.935, 0.21, 0.12, 0.010, 0.90, 0.70, True, 1.00, 3.0),
    Skill(4, "Realista", 0.962, 0.17, 0.06, 0.005, 1.00, 0.85, True, 0.97, 1.0),
)


def skill(level):
    level = int(level)
    if level < 0:
        level = 0
    if level >= len(SKILLS):
        level = len(SKILLS) - 1
    return SKILLS[level]

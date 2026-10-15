"""Small numeric helpers used in hot loops (kept dependency free)."""

import math

TWO_PI = 2.0 * math.pi


def clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value


def lerp(a, b, t):
    return a + (b - a) * t


def smoothstep(edge0, edge1, x):
    """Hermite smoothstep: 0 below edge0, 1 above edge1, C1 continuous."""
    if edge1 == edge0:
        return 0.0 if x < edge0 else 1.0
    t = clamp((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def wrap_angle(angle):
    """Wrap to (-pi, pi]."""
    return math.atan2(math.sin(angle), math.cos(angle))


def sign(value):
    if value > 0.0:
        return 1.0
    if value < 0.0:
        return -1.0
    return 0.0


def interp_table(xs, ys, x):
    """Piecewise-linear interpolation, clamped at the table ends."""
    if x <= xs[0]:
        return ys[0]
    last = len(xs) - 1
    if x >= xs[last]:
        return ys[last]
    lo, hi = 0, last
    while hi - lo > 1:
        mid = (lo + hi) >> 1
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    t = (x - xs[lo]) / (xs[hi] - xs[lo])
    return ys[lo] + (ys[hi] - ys[lo]) * t


class SplitMix64:
    """Deterministic PRNG with an explicitly specified algorithm.

    Python's ``random`` module would also be reproducible, but an explicit,
    tiny generator guarantees identical sequences in CPython and Pyodide and
    documents the algorithm (Steele, Lea & Flood, 2014).
    """

    __slots__ = ("state",)
    MASK = (1 << 64) - 1

    def __init__(self, seed):
        self.state = int(seed) & self.MASK

    def next_u64(self):
        self.state = (self.state + 0x9E3779B97F4A7C15) & self.MASK
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & self.MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & self.MASK
        return z ^ (z >> 31)

    def random(self):
        """Uniform float in [0, 1)."""
        return (self.next_u64() >> 11) * (1.0 / 9007199254740992.0)

    def uniform(self, low, high):
        return low + (high - low) * self.random()

    def gauss(self):
        """Standard normal deviate (Box-Muller, one value per call)."""
        u1 = max(self.random(), 1e-300)
        u2 = self.random()
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(TWO_PI * u2)

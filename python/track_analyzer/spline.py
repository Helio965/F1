"""Closed centripetal Catmull-Rom spline, bit-for-bit compatible in method
with Three.js ``CatmullRomCurve3(points, closed=true, "centripetal")``.

Using the same construction keeps the rendered ribbon (built from the
analyser's samples during a race, or by Three.js in editor previews) and
the physics centre line on the same curve. Arc-length re-parameterisation
also mirrors Three.js ``getLengths``/``getUtoTmapping`` with 4096 divisions.

Besides positions, this module evaluates analytic first and second
derivatives of the cubic segments, giving smooth tangents and curvature.
"""

import math

ARC_LENGTH_DIVISIONS = 4096


class _Cubic:
    __slots__ = ("c0", "c1", "c2", "c3")

    def __init__(self, x0, x1, x2, x3, dt0, dt1, dt2):
        # Non-uniform Catmull-Rom tangents (Three.js initNonuniformCatmullRom).
        t1 = (x1 - x0) / dt0 - (x2 - x0) / (dt0 + dt1) + (x2 - x1) / dt1
        t2 = (x2 - x1) / dt1 - (x3 - x1) / (dt1 + dt2) + (x3 - x2) / dt2
        t1 *= dt1
        t2 *= dt1
        self.c0 = x1
        self.c1 = t1
        self.c2 = -3.0 * x1 + 3.0 * x2 - 2.0 * t1 - t2
        self.c3 = 2.0 * x1 - 2.0 * x2 + t1 + t2

    def value(self, t):
        return self.c0 + t * (self.c1 + t * (self.c2 + t * self.c3))

    def d1(self, t):
        return self.c1 + t * (2.0 * self.c2 + 3.0 * t * self.c3)

    def d2(self, t):
        return 2.0 * self.c2 + 6.0 * t * self.c3


class ClosedCatmullRom:
    """Closed centripetal Catmull-Rom through 3-D points (x, y, z).

    ``extra`` may carry per-point scalar channels (e.g. banking) interpolated
    with the same non-uniform basis.
    """

    def __init__(self, points, extra=None):
        if len(points) < 3:
            raise ValueError("at least three control points are required")
        self.points = [(float(p[0]), float(p[1]), float(p[2])) for p in points]
        self.count = len(self.points)
        self.extra = [list(map(float, channel)) for channel in (extra or [])]
        self.segments = []
        pts = self.points
        n = self.count
        for i in range(n):
            p0 = pts[(i - 1) % n]
            p1 = pts[i]
            p2 = pts[(i + 1) % n]
            p3 = pts[(i + 2) % n]
            dt0 = _dist2(p0, p1) ** 0.25
            dt1 = _dist2(p1, p2) ** 0.25
            dt2 = _dist2(p2, p3) ** 0.25
            if dt1 < 1e-4:
                dt1 = 1.0
            if dt0 < 1e-4:
                dt0 = dt1
            if dt2 < 1e-4:
                dt2 = dt1
            cubics = [
                _Cubic(p0[k], p1[k], p2[k], p3[k], dt0, dt1, dt2) for k in range(3)
            ]
            for channel in self.extra:
                cubics.append(
                    _Cubic(
                        channel[(i - 1) % n],
                        channel[i],
                        channel[(i + 1) % n],
                        channel[(i + 2) % n],
                        dt0,
                        dt1,
                        dt2,
                    )
                )
            self.segments.append(cubics)
        self._lengths = None

    # Three.js getPoint(t): segment = floor(n t), local weight = n t - segment
    def _locate(self, t):
        p = self.count * t
        seg = int(math.floor(p))
        weight = p - seg
        seg %= self.count
        return self.segments[seg], weight

    def point(self, t):
        cubics, w = self._locate(t)
        return cubics[0].value(w), cubics[1].value(w), cubics[2].value(w)

    def channel(self, t, index):
        cubics, w = self._locate(t)
        return cubics[3 + index].value(w)

    def derivatives(self, t):
        """(x', y', z', x'', y'', z'') with respect to the local segment weight."""
        cubics, w = self._locate(t)
        return (
            cubics[0].d1(w),
            cubics[1].d1(w),
            cubics[2].d1(w),
            cubics[0].d2(w),
            cubics[1].d2(w),
            cubics[2].d2(w),
        )

    # ----------------------------------------------------------- arc length
    def lengths(self):
        if self._lengths is None:
            divisions = ARC_LENGTH_DIVISIONS
            cache = [0.0]
            last = self.point(0.0)
            total = 0.0
            for p in range(1, divisions + 1):
                current = self.point(p / divisions)
                total += math.sqrt(_dist2(current, last))
                cache.append(total)
                last = current
            self._lengths = cache
        return self._lengths

    @property
    def length(self):
        return self.lengths()[-1]

    def u_to_t(self, u):
        """Three.js getUtoTmapping: arc-length fraction -> curve parameter."""
        arc = self.lengths()
        il = len(arc)
        target = u * arc[il - 1]
        low, high = 0, il - 1
        while low <= high:
            i = low + (high - low) // 2
            comparison = arc[i] - target
            if comparison < 0:
                low = i + 1
            elif comparison > 0:
                high = i - 1
            else:
                high = i
                break
        i = high
        if arc[i] == target:
            return i / (il - 1)
        before = arc[i]
        after = arc[i + 1]
        fraction = (target - before) / (after - before)
        return (i + fraction) / (il - 1)

    def spaced_points(self, divisions):
        """Three.js getSpacedPoints(divisions)[0:divisions] (closing point omitted)."""
        return [self.point(self.u_to_t(d / divisions)) for d in range(divisions)]


def _dist2(a, b):
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return dx * dx + dy * dy + dz * dz

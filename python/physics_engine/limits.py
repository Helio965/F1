"""Quasi-steady-state (QSS) performance limits of a vehicle.

Used by the track analyser (reference speed profiles) and the AI planner.
It reuses the *same* parameter sets and component models as the dynamic
simulation (tyre load sensitivity, aero coefficients, engine torque curve,
gearing, brake system) so the reference speed emerges from physics instead of
per-corner tables. QSS ignores transients (yaw inertia, load-transfer
dynamics, tyre relaxation, thermal state), so it is a reference, not a
mandatory speed: the dynamic model may be faster or slower.

Lateral limit per axle j with load transfer:
    demand_j  = m_j a_y,  m_j = m b/L (front) or m a/L (rear)
    capacity_j = sum over its two tyres of mu(Fz_i) Fz_i
with Fz from static weight (effective normal gravity incl. banking and vertical
curvature), aero load split and lateral transfer m a_y h / t_j distributed
by roll-stiffness share. The maximum speed is the largest v for which both
axles can supply their share (balance-limited cornering).
"""

import math

from .aero import AeroModel
from .constants import GRAVITY
from .environment import Atmosphere
from .mathutil import interp_table
from .tyre import TyreModel
from .vehicles import COMPOUNDS

RPM_TO_RAD = math.pi / 30.0


class PerformanceEnvelope:
    """Pre-computed QSS model for one vehicle, compound and condition."""

    def __init__(
        self,
        params,
        compound=None,
        atmosphere=None,
        grip_scale=1.0,
        wing=0.5,
        fuel=None,
        use_ers=True,
    ):
        self.p = params
        compound = compound if compound in COMPOUNDS else params.default_compound
        self.tyre = TyreModel(COMPOUNDS[compound])
        self.atm = atmosphere or Atmosphere("sun")
        self.grip_scale = grip_scale
        self.aero = AeroModel(params, wing)
        fuel = params.fuel_capacity * 0.5 if fuel is None else fuel
        self.m = params.mass + fuel
        self.L = params.wheelbase
        self.a = params.wheelbase * (1.0 - params.front_weight_fraction)
        self.b = params.wheelbase * params.front_weight_fraction
        cla_f, cla_r, cda = self.aero.coefficients(
            params.ride_height_front, params.ride_height_rear, False, 0.0, 1.0, 1.0
        )
        rho = self.atm.density
        self.k_down_f = 0.5 * rho * cla_f
        self.k_down_r = 0.5 * rho * cla_r
        self.k_drag = 0.5 * rho * cda
        roll_f = params.arb_front + params.wheel_rate_front * params.track_front ** 2 / 2.0
        roll_r = params.arb_rear + params.wheel_rate_rear * params.track_rear ** 2 / 2.0
        self.roll_share_f = roll_f / (roll_f + roll_r)
        self.h = params.cg_height
        self.rr = self.tyre.compound.rolling_resistance
        self.use_ers = use_ers and params.ers_power > 0.0
        self.radius = params.tyre_radius_rear
        self.top_speed = self._top_speed()

    # ------------------------------------------------------------ engine side
    def wheel_force_max(self, v):
        """Maximum tractive force at the rear contact patches (N) at speed v."""
        p = self.p
        best = 0.0
        omega_w = max(v, 0.5) / self.radius
        for ratio in p.gear_ratios:
            g = ratio * p.final_drive
            rpm = omega_w * g / RPM_TO_RAD
            if rpm > p.rev_limit:
                continue
            rpm_eff = rpm if rpm > p.launch_rpm else p.launch_rpm
            torque = interp_table(p.torque_rpm, p.torque_nm, rpm_eff)
            force = torque * g * p.driveline_efficiency / self.radius
            if force > best:
                best = force
        if self.use_ers:
            best += p.ers_power / max(v, 8.0)
        return best

    def _top_speed(self):
        lo, hi = 10.0, 160.0
        for _ in range(50):
            mid = 0.5 * (lo + hi)
            resist = self.k_drag * mid * mid + self.rr * (self.m * GRAVITY + (self.k_down_f + self.k_down_r) * mid * mid)
            if self.wheel_force_max(mid) > resist:
                lo = mid
            else:
                hi = mid
        return lo

    # -------------------------------------------------------------- tyre side
    def _axle_capacity(self, load_axle, transfer, mu_scale, width):
        """Total friction capability of an axle (N) with a lateral transfer."""
        tyre = self.tyre
        lo = 0.5 * load_axle - transfer
        hi = 0.5 * load_axle + transfer
        cap = 0.0
        for fz in (lo, hi):
            if fz > 0.0:
                cap += tyre.peak_mu(fz, width) * fz
        return cap * mu_scale

    def loads(self, v, g_n):
        static = self.m * g_n
        front = static * self.b / self.L + self.k_down_f * v * v
        rear = static * self.a / self.L + self.k_down_r * v * v
        return front, rear

    def lateral_ok(self, v, curvature, bank, vcurv, mu_scale):
        """Return (front_utilisation, rear_utilisation) of lateral capacity."""
        a_req = abs(v * v * curvature + GRAVITY * math.sin(bank))
        g_n = GRAVITY * math.cos(bank) - v * v * curvature * math.sin(bank) + v * v * vcurv
        if g_n < 0.5:
            g_n = 0.5
        front, rear = self.loads(v, g_n)
        transfer_total = self.m * a_req * self.h
        tf = transfer_total * self.roll_share_f / self.p.track_front
        tr = transfer_total * (1.0 - self.roll_share_f) / self.p.track_rear
        cap_f = self._axle_capacity(front, tf, mu_scale, self.p.tyre_width_front)
        cap_r = self._axle_capacity(rear, tr, mu_scale, self.p.tyre_width_rear)
        demand_f = self.m * a_req * self.b / self.L
        demand_r = self.m * a_req * self.a / self.L
        return demand_f / max(cap_f, 1e-6), demand_r / max(cap_r, 1e-6)

    def max_corner_speed(self, curvature, bank=0.0, vcurv=0.0, mu_scale=1.0):
        """Largest steady speed for a path curvature (1/m)."""
        mu_scale *= self.grip_scale
        if abs(curvature) < 1e-5:
            return self.top_speed
        lo, hi = 1.0, self.top_speed
        f, r = self.lateral_ok(hi, curvature, bank, vcurv, mu_scale)
        if f <= 1.0 and r <= 1.0:
            return hi
        for _ in range(36):
            mid = 0.5 * (lo + hi)
            f, r = self.lateral_ok(mid, curvature, bank, vcurv, mu_scale)
            if f <= 1.0 and r <= 1.0:
                lo = mid
            else:
                hi = mid
        return lo

    def lateral_utilisation(self, v, curvature, bank, vcurv, mu_scale):
        f, r = self.lateral_ok(v, curvature, bank, vcurv, mu_scale * self.grip_scale)
        return max(f, r)

    def acceleration(self, v, curvature, bank, grade, vcurv, mu_scale=1.0):
        """Max forward acceleration (m/s^2) limited by power, traction and the ellipse."""
        mu_scale *= self.grip_scale
        util = self.lateral_utilisation(v, curvature, bank, vcurv, mu_scale / self.grip_scale)
        remaining = math.sqrt(max(0.0, 1.0 - min(1.0, util) ** 2))
        g_n = GRAVITY * math.cos(bank) + v * v * vcurv
        front, rear = self.loads(v, max(g_n, 0.5))
        # longitudinal transfer to the rear under acceleration (one fixed-point pass)
        a_guess = 8.0
        rear_l = rear + self.m * a_guess * self.h / self.L
        traction = self._axle_capacity(rear_l, 0.0, mu_scale, self.p.tyre_width_rear) * remaining
        force = min(traction, self.wheel_force_max(v))
        resist = self.k_drag * v * v + self.rr * (front + rear)
        slope = self.m * GRAVITY * grade / math.sqrt(1.0 + grade * grade)
        return (force - resist - slope) / self.m

    def deceleration(self, v, curvature, bank, grade, vcurv, mu_scale=1.0):
        """Max braking deceleration magnitude (m/s^2) incl. drag and grade."""
        mu_scale *= self.grip_scale
        util = self.lateral_utilisation(v, curvature, bank, vcurv, mu_scale / self.grip_scale)
        remaining = math.sqrt(max(0.0, 1.0 - min(1.0, util) ** 2))
        g_n = GRAVITY * math.cos(bank) + v * v * vcurv
        front, rear = self.loads(v, max(g_n, 0.5))
        decel = 0.0
        for _ in range(2):
            shift = self.m * decel * self.h / self.L
            cap = (
                self._axle_capacity(front + shift, 0.0, mu_scale, self.p.tyre_width_front)
                + self._axle_capacity(max(rear - shift, 0.0), 0.0, mu_scale, self.p.tyre_width_rear)
            ) * remaining
            brake_limit = self.p.brake_torque_total / self.radius
            force = min(cap, brake_limit)
            resist = self.k_drag * v * v + self.rr * (front + rear)
            slope = self.m * GRAVITY * grade / math.sqrt(1.0 + grade * grade)
            decel = (force + resist + slope) / self.m
        return decel


def speed_profile(envelope, ds_list, curvature, bank, grade, vcurv, mu_scale):
    """Forward/backward QSS speed profile on a closed path.

    ``ds_list[i]`` is the distance from node i to node i+1 (wrapping). Returns
    (v, v_corner, accel_limited, brake_limited) lists.
    """
    n = len(curvature)
    v_corner = [
        envelope.max_corner_speed(curvature[i], bank[i], vcurv[i], mu_scale[i]) for i in range(n)
    ]
    start = min(range(n), key=lambda i: v_corner[i])
    v = list(v_corner)
    accel_limited = [False] * n
    brake_limited = [False] * n
    for _ in range(2):
        # forward pass from the slowest point
        for k in range(n):
            i = (start + k) % n
            j = (i + 1) % n
            a = envelope.acceleration(v[i], curvature[i], bank[i], grade[i], vcurv[i], mu_scale[i])
            reach = math.sqrt(max(0.0, v[i] * v[i] + 2.0 * a * ds_list[i])) if a > 0.0 else v[i]
            if reach < v[j]:
                v[j] = max(reach, 1.0)
                accel_limited[j] = True
        # backward pass
        for k in range(n):
            j = (start - k) % n
            i = (j - 1) % n
            d = envelope.deceleration(v[j], curvature[j], bank[j], grade[j], vcurv[j], mu_scale[j])
            reach = math.sqrt(v[j] * v[j] + 2.0 * max(d, 0.1) * ds_list[i])
            if reach < v[i]:
                v[i] = reach
                brake_limited[i] = True
                accel_limited[i] = False
    return v, v_corner, accel_limited, brake_limited

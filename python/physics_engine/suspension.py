"""Sprung-body suspension model: heave, pitch and roll with four corners.

State: heave z (m, + up from static), pitch theta (rad, + nose down),
roll phi (rad, + right side down) and their rates.

Corner i at body coordinates (x_i forward, y_i left) moves vertically by
    d_i = z - x_i theta + y_i phi
and receives the spring/damper force on the body (upward positive)
    f_i = -k_i d_i - c_i d'_i (+ bump stop beyond the travel)
plus anti-roll bar forces f_L = -K_arb phi / t, f_R = +K_arb phi / t per axle.

External generalised loads (relative to the static equilibrium):
    heave:  -(F_aero_f + F_aero_r) - m_s (g_n - g)
    pitch:  F_aero_f a - F_aero_r b - (1 - chi) m_s a_x h_s
    roll:   m_s a_y (h_s - h_ra) + m_s g_n (h_s - h_ra) phi
where a_x, a_y are the specific forces (accelerometer readings) and chi is the
anti-dive/anti-squat fraction. Geometric (link) load transfer bypasses the
springs:
    lateral, axle j:     (m_sj h_rcj + 2 m_uj r_j) a_y / t_j  (to the outside)
    longitudinal (front): (chi m_s h_s + sum m_u r) (-a_x) / L
Integration: backward Euler on each DOF's own stiffness and damping,
explicit coupling (stable for the stiff race-car rates at 120 Hz).
"""

from .constants import GRAVITY


class Suspension:
    __slots__ = (
        "x", "y", "k", "c", "travel", "k_bump", "arb", "track",
        "m_s", "m_u", "h_s", "h_ra", "rc", "radius", "a", "b", "L",
        "anti_dive", "anti_squat", "i_pitch", "i_roll",
        "z", "theta", "phi", "vz", "vtheta", "vphi",
        "static_sprung", "loads", "deflection", "ride_front", "ride_rear",
        "h_front0", "h_rear0", "m_s_front", "m_s_rear",
    )

    def __init__(self, params, total_mass):
        p = params
        self.L = p.wheelbase
        self.a = p.wheelbase * (1.0 - p.front_weight_fraction)
        self.b = p.wheelbase * p.front_weight_fraction
        tf, tr = p.track_front, p.track_rear
        self.track = (tf, tr)
        self.x = (self.a, self.a, -self.b, -self.b)
        self.y = (0.5 * tf, -0.5 * tf, 0.5 * tr, -0.5 * tr)
        self.k = (p.wheel_rate_front, p.wheel_rate_front, p.wheel_rate_rear, p.wheel_rate_rear)
        self.c = (p.damper_front, p.damper_front, p.damper_rear, p.damper_rear)
        self.travel = p.bump_travel
        self.k_bump = p.bump_stop_rate
        self.arb = (p.arb_front, p.arb_rear)
        self.m_u = (p.unsprung_front, p.unsprung_front, p.unsprung_rear, p.unsprung_rear)
        self.radius = (p.tyre_radius_front, p.tyre_radius_front, p.tyre_radius_rear, p.tyre_radius_rear)
        self.rc = (p.roll_centre_front, p.roll_centre_rear)
        self.anti_dive = p.anti_dive
        self.anti_squat = p.anti_squat
        self.i_pitch = p.pitch_inertia
        self.i_roll = p.roll_inertia
        self.h_front0 = p.ride_height_front
        self.h_rear0 = p.ride_height_rear
        self.z = self.theta = self.phi = 0.0
        self.vz = self.vtheta = self.vphi = 0.0
        self.loads = [0.0, 0.0, 0.0, 0.0]
        self.deflection = [0.0, 0.0, 0.0, 0.0]
        self.ride_front = p.ride_height_front
        self.ride_rear = p.ride_height_rear
        self.set_mass(params, total_mass)

    def set_mass(self, params, total_mass):
        """Recompute mass distribution (fuel burn changes the total mass)."""
        p = params
        m_u_total = sum(self.m_u)
        self.m_s = total_mass - m_u_total
        h_total = p.cg_height * total_mass
        h_unsprung = sum(m * r for m, r in zip(self.m_u, self.radius))
        self.h_s = (h_total - h_unsprung) / self.m_s
        front_total = total_mass * p.front_weight_fraction
        self.m_s_front = front_total - 2.0 * p.unsprung_front
        self.m_s_rear = self.m_s - self.m_s_front
        self.h_ra = self.rc[0] + (self.rc[1] - self.rc[0]) * self.a / self.L
        g = GRAVITY
        self.static_sprung = (
            0.5 * self.m_s_front * g,
            0.5 * self.m_s_front * g,
            0.5 * self.m_s_rear * g,
            0.5 * self.m_s_rear * g,
        )

    def step(self, dt, ax, ay, aero_front, aero_rear, g_n):
        """Advance the body and return the four tyre normal loads (N, >= 0)."""
        x, y, k, c = self.x, self.y, self.k, self.c
        z, th, ph = self.z, self.theta, self.phi
        vz, vth, vph = self.vz, self.vtheta, self.vphi
        tf, tr = self.track
        arb_f, arb_r = self.arb
        m_s = self.m_s
        arm = self.h_s - self.h_ra
        # Generalised spring/damper loads from the current state.
        fz = mth = mph = 0.0
        kz = cz = kth = cth = kph = cph = 0.0
        for i in range(4):
            xi = x[i]
            yi = y[i]
            d = z - xi * th + yi * ph
            dv = vz - xi * vth + yi * vph
            ki = k[i]
            ci = c[i]
            f = -ki * d - ci * dv
            comp = -d - self.travel
            kb = 0.0
            if comp > 0.0:
                f += self.k_bump * comp
                kb = self.k_bump
            kk = ki + kb
            fz += f
            mth -= xi * f
            mph += yi * f
            kz += kk
            cz += ci
            kth += kk * xi * xi
            cth += ci * xi * xi
            kph += kk * yi * yi
            cph += ci * yi * yi
        mph -= (arb_f + arb_r) * ph
        kph += arb_f + arb_r
        chi = self.anti_dive if ax < 0.0 else self.anti_squat
        fz += -(aero_front + aero_rear) - m_s * (g_n - GRAVITY)
        mth += aero_front * self.a - aero_rear * self.b - (1.0 - chi) * m_s * ax * self.h_s
        mph += m_s * ay * arm + m_s * g_n * arm * ph
        # Backward Euler on own stiffness/damping per DOF.
        dt2 = dt * dt
        vz = (m_s * vz + dt * (fz + cz * vz)) / (m_s + dt * cz + dt2 * kz)
        vth = (self.i_pitch * vth + dt * (mth + cth * vth)) / (self.i_pitch + dt * cth + dt2 * kth)
        vph = (self.i_roll * vph + dt * (mph + cph * vph)) / (self.i_roll + dt * cph + dt2 * kph)
        z += dt * vz
        th += dt * vth
        ph += dt * vph
        self.z, self.theta, self.phi = z, th, ph
        self.vz, self.vtheta, self.vphi = vz, vth, vph
        # Tyre loads from the updated state.
        loads = self.loads
        defl = self.deflection
        g_ratio = g_n
        m_u = self.m_u
        r = self.radius
        lat_f = (self.m_s_front * self.rc[0] + 2.0 * m_u[0] * r[0]) * ay / tf
        lat_r = (self.m_s_rear * self.rc[1] + 2.0 * m_u[2] * r[2]) * ay / tr
        long_t = (chi * m_s * self.h_s + sum(m * rr for m, rr in zip(m_u, r))) * (-ax) / self.L
        for i in range(4):
            xi = x[i]
            yi = y[i]
            d = z - xi * th + yi * ph
            dv = vz - xi * vth + yi * vph
            f = -k[i] * d - c[i] * dv
            comp = -d - self.travel
            if comp > 0.0:
                f += self.k_bump * comp
            if i < 2:
                f += (-arb_f if yi > 0.0 else arb_f) * ph / tf
                lat = lat_f
                lng = 0.5 * long_t
            else:
                f += (-arb_r if yi > 0.0 else arb_r) * ph / tr
                lat = lat_r
                lng = -0.5 * long_t
            load = m_u[i] * g_ratio + self.static_sprung[i] + f + (lat if yi < 0.0 else -lat) + lng
            loads[i] = load if load > 0.0 else 0.0
            defl[i] = d
        self.ride_front = self.h_front0 + z - self.a * th
        self.ride_rear = self.h_rear0 + z + self.b * th
        return loads

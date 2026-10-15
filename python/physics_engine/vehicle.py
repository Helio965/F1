"""Four-wheel vehicle dynamics.

Degrees of freedom per car:
* planar rigid body: position (x, z), heading psi, body velocities u, v and
  yaw rate r (3 DOF dynamics);
* sprung body: heave, pitch and roll (:mod:`suspension`);
* four wheel spin speeds omega_i;
* four longitudinal and four lateral tyre carcass deflections (transient
  slip, relaxation lengths);
* thermal states (tread surface and core per tyre, disc per corner), wear,
  fuel, ERS store, steering actuator.

Integration (fixed step, default 1/120 s), per step:
1. steering actuator (rate-limited);
2. track projection, surface material and water film at each contact patch;
3. gravity in the body frame (grade, banking, vertical curvature);
4. aerodynamic forces from the current ride heights;
5. suspension step -> four normal loads (load transfer uses the previous
   step's specific forces: one-step lag of the algebraic loop);
6. per wheel: exact exponential update of the lateral deflection, implicit
   (backward Euler) solve of the coupled longitudinal deflection / wheel
   spin pair, Coulomb treatment of brake and rolling-resistance torques
   (allows wheel lock), combined-slip forces;
7. body: semi-implicit Euler for (u, v, r), exact rotation of the body-frame
   velocity by r dt, then position/heading update.
The derivations are in docs/PHYSICS_EQUATIONS.md.
"""

import math

from .aero import AeroModel
from .brakes import INITIAL_BRAKE_TEMPERATURE, pad_factor, update_disc_temperature
from .constants import GRAVITY
from .powertrain import Powertrain, differential_split
from .suspension import Suspension
from .track import SURFACE_ASPHALT, SURFACE_PROPERTIES, gravity_components
from .tyre import (
    INITIAL_TYRE_TEMPERATURE,
    REFERENCE_WIDTH,
    TyreModel,
    hot_pressure,
    update_thermal,
    wear_increment,
)
from .vehicles import COMPOUNDS

MAX_NORMALISED_SLIP = 6.0
SLOW_EVERY = 4  # thermal/wear/pressure/brake-temperature states update at 30 Hz
LOW_SPEED = 2.5  # m/s, below which carcass damping regularises standstill
LOW_SPEED_DAMPING = 0.25  # fraction of critical explicit damping per step


class Vehicle:
    def __init__(self, params, track, car_id=0, compound=None, fuel=None, wing=0.5, brake_bias=None):
        p = params
        self.p = p
        self.id = car_id
        self.track = track
        self.compound_id = compound if compound in COMPOUNDS and compound in p.compounds else p.default_compound
        self.tyre = TyreModel(COMPOUNDS[self.compound_id])
        fuel_kg = p.fuel_capacity * 0.6 if fuel is None else fuel
        self.powertrain = Powertrain(p, fuel_kg)
        self.aero = AeroModel(p, wing)
        self.wing = wing
        self.brake_bias = p.brake_bias if brake_bias is None else brake_bias
        self.mass = p.mass + self.powertrain.fuel
        self.suspension = Suspension(p, self.mass)
        self.fz = self.suspension.loads
        a = self.suspension.a
        b = self.suspension.b
        self.wx = (a, a, -b, -b)
        self.wy = (0.5 * p.track_front, -0.5 * p.track_front, 0.5 * p.track_rear, -0.5 * p.track_rear)
        self.radius = (p.tyre_radius_front, p.tyre_radius_front, p.tyre_radius_rear, p.tyre_radius_rear)
        self.tyre_width = (p.tyre_width_front, p.tyre_width_front, p.tyre_width_rear, p.tyre_width_rear)
        self.wheel_inertia = (
            p.wheel_inertia_front,
            p.wheel_inertia_front,
            p.wheel_inertia_rear,
            p.wheel_inertia_rear,
        )
        self.fz0_wheel = tuple(
            self.tyre.compound.fz_nominal * w / REFERENCE_WIDTH for w in self.tyre_width
        )
        self.sigma_a = p.relaxation_lateral
        self.sigma_k = p.relaxation_longitudinal
        # kinematic state
        self.x = self.y = self.z = 0.0
        self.psi = 0.0
        self.u = self.v = self.r = 0.0
        self.steer = 0.0
        self.omega = [0.0, 0.0, 0.0, 0.0]
        self.spin = [0.0, 0.0, 0.0, 0.0]
        self.u1 = [0.0, 0.0, 0.0, 0.0]
        self.v1 = [0.0, 0.0, 0.0, 0.0]
        self.k_secant = [0.0, 0.0, 0.0, 0.0]
        # tyre/brake states
        self.surface_t = [INITIAL_TYRE_TEMPERATURE] * 4
        self.core_t = [INITIAL_TYRE_TEMPERATURE] * 4
        self.wear = [0.0, 0.0, 0.0, 0.0]
        self.pressure = [p.cold_pressure] * 4
        self.brake_t = [INITIAL_BRAKE_TEMPERATURE] * 4
        # telemetry
        self.fx = [0.0, 0.0, 0.0, 0.0]
        self.fy = [0.0, 0.0, 0.0, 0.0]
        self.kappa = [0.0, 0.0, 0.0, 0.0]
        self.alpha = [0.0, 0.0, 0.0, 0.0]
        self.slip = [0.0, 0.0, 0.0, 0.0]
        self.locked = [False, False, False, False]
        self.surface = [SURFACE_ASPHALT] * 4
        self.film = [0.0, 0.0, 0.0, 0.0]
        self.grip = [1.0, 1.0, 1.0, 1.0]
        self.ax_spec = 0.0
        self.ay_spec = 0.0
        self.ax_world = 0.0
        self.drag = 0.0
        self.downforce = 0.0
        self.drs_open = False
        self.drs_allowed = False
        self.damage = 0.0
        self.wake_drag = 1.0
        self.wake_down = 1.0
        self.throttle = 0.0
        self.brake = 0.0
        self.abs_scale = [1.0, 1.0, 1.0, 1.0]
        self.track_index = 0
        self.s = 0.0
        self.n = 0.0
        self.dpsi = 0.0
        self.pitch_road = 0.0
        self.roll_road = 0.0
        self.mass_timer = 0.0
        self.distance = 0.0
        self.track_t = 0.0
        self.proj_fresh = False
        # slow-rate accumulators and factors
        self.slow_phase = car_id % SLOW_EVERY
        self.acc_slide = [0.0, 0.0, 0.0, 0.0]
        self.acc_brake = [0.0, 0.0, 0.0, 0.0]
        self.w_speed = [0.0, 0.0, 0.0, 0.0]
        self.w_rr = [1.0, 1.0, 1.0, 1.0]
        self.w_track_t = [25.0, 25.0, 25.0, 25.0]
        self.w_rough = [0.8, 0.8, 0.8, 0.8]
        self.slow_grip = [1.0, 1.0, 1.0, 1.0]
        self.pad = [1.0, 1.0, 1.0, 1.0]
        self._init_secants()
        self._refresh_factors()

    # ------------------------------------------------------------- utilities
    def _init_secants(self):
        static = self.mass * GRAVITY
        f = self.p.front_weight_fraction
        loads = (0.5 * f * static, 0.5 * f * static, 0.5 * (1 - f) * static, 0.5 * (1 - f) * static)
        t = self.tyre
        for i in range(4):
            mu = t.peak_mu(loads[i], self.tyre_width[i])
            self.k_secant[i] = mu * loads[i] * t.b_long * t.c_long / (t.kappa_peak * self.sigma_k)
            self.fz[i] = loads[i]
        self.suspension.loads[:] = list(loads)

    def place(self, s, lateral, speed=0.0):
        """Put the car on the track at (s, lateral), aligned with the centre line."""
        x, y, z, heading = self.track.world_point(s, lateral)
        self.x, self.y, self.z = x, y, z
        self.psi = heading
        self.u = speed
        self.v = self.r = 0.0
        self.steer = 0.0
        for i in range(4):
            self.omega[i] = speed / self.radius[i]
            self.u1[i] = self.v1[i] = 0.0
        sus = self.suspension
        sus.z = sus.theta = sus.phi = 0.0
        sus.vz = sus.vtheta = sus.vphi = 0.0
        self.ax_spec = self.ay_spec = 0.0
        self.track_index = self.track.index_at(s)
        idx, t, s2, n, _ = self.track.project(x, z, self.track_index)
        self.track_index, self.s, self.n = idx, s2, n
        pt = self.powertrain
        pt.gear = 1
        pt.shift_timer = 0.0

    @property
    def speed(self):
        """Signed ground speed (m/s), positive when moving forward."""
        mag = math.sqrt(self.u * self.u + self.v * self.v)
        return mag if self.u >= 0.0 else -mag

    def total_mass(self):
        return self.p.mass + self.powertrain.fuel

    # --------------------------------------------------------------- dynamics
    def step(self, dt, throttle, brake, steer_input, drs_request, env):
        p = self.p
        trk = self.track
        tyre = self.tyre
        compound = tyre.compound
        surface_state = trk.surface
        throttle = 0.0 if throttle < 0.0 else (1.0 if throttle > 1.0 else throttle)
        brake = 0.0 if brake < 0.0 else (1.0 if brake > 1.0 else brake)
        if steer_input > 1.0:
            steer_input = 1.0
        elif steer_input < -1.0:
            steer_input = -1.0
        self.throttle = throttle
        self.brake = brake

        # 1. steering actuator (ISO: positive angle turns left; input +1 = right)
        target = -steer_input * p.max_steer
        max_d = p.steer_rate * dt
        d = target - self.steer
        if d > max_d:
            d = max_d
        elif d < -max_d:
            d = -max_d
        self.steer += d
        delta = self.steer

        # mass bookkeeping (fuel burn) at 2 Hz
        self.mass_timer += dt
        if self.mass_timer >= 0.5:
            self.mass_timer = 0.0
            self.mass = self.total_mass()
            self.suspension.set_mass(p, self.mass)
        m = self.mass
        i_z = p.yaw_inertia + 0.25 * self.powertrain.fuel

        # 2. track location
        if self.proj_fresh:
            idx, t, s, n = self.track_index, self.track_t, self.s, self.n
            self.proj_fresh = False
        else:
            idx, t, s, n, _ = trk.project(self.x, self.z, self.track_index)
            self.track_index, self.track_t, self.s, self.n = idx, t, s, n
        grade = trk.sample(idx, t, trk.grade)
        bank = trk.sample(idx, t, trk.bank)
        vcurv = trk.sample(idx, t, trk.vcurv)
        self.y = trk.height(idx, t, n)
        psi_t = trk.heading_at(idx, t)
        dpsi = self.psi - psi_t
        cd = math.cos(dpsi)
        sd = math.sin(dpsi)
        self.dpsi = dpsi
        u = self.u
        v = self.v
        r = self.r

        # 3. gravity in the body frame
        g_t, g_l, g_n = gravity_components(grade, bank, vcurv, u)
        g_n -= u * r * math.sin(bank)  # banked-surface share of the centripetal load
        gx = g_t * cd + g_l * sd
        gy = -g_t * sd + g_l * cd
        # Attitude of the road plane under the car (for rendering): surface
        # height h = grade * t + tan(bank) * l in the track frame, read along
        # the car's forward (cos dpsi, sin dpsi) and left (-sin dpsi, cos dpsi).
        tan_bank = math.tan(bank)
        self.pitch_road = -math.atan(grade * cd + tan_bank * sd)
        self.roll_road = math.atan(tan_bank * cd - grade * sd)

        # 4. aerodynamics
        atm = env.atmosphere
        wind_x, wind_z = env.wind
        sin_psi = math.sin(self.psi)
        cos_psi = math.cos(self.psi)
        v_air = u - (wind_x * sin_psi + wind_z * cos_psi)
        q = 0.5 * atm.density * v_air * v_air
        self.drs_open = bool(drs_request and p.drs and self.drs_allowed and u > 22.0)
        sus = self.suspension
        cla_f, cla_r, cda = self.aero.coefficients(
            sus.ride_front, sus.ride_rear, self.drs_open, self.damage, self.wake_drag, self.wake_down
        )
        drag = q * cda if v_air >= 0.0 else -q * cda
        down_f = q * cla_f
        down_r = q * cla_r
        self.drag = drag
        self.downforce = down_f + down_r

        # 5. normal loads
        fz = sus.step(dt, self.ax_spec, self.ay_spec, down_f, down_r, g_n)

        # powertrain (rear-wheel drive)
        pt = self.powertrain
        omega = self.omega
        axle_omega = 0.5 * (omega[2] + omega[3])
        axle_torque = pt.axle_torque(throttle, axle_omega, dt)
        extra_inertia = 0.5 * pt.reflected_inertia()
        t_left, t_right = differential_split(
            axle_torque,
            omega[2],
            omega[3],
            self.wheel_inertia[2] + extra_inertia,
            dt,
            p.diff_preload,
            p.diff_ramp,
        )
        brake_front = brake * self.brake_bias * p.brake_torque_total * 0.5
        brake_rear = brake * (1.0 - self.brake_bias) * p.brake_torque_total * 0.5

        # Ackermann: the inner wheel steers more (small-angle approximation).
        delta_l = delta_r = delta
        if delta != 0.0:
            ack = p.ackermann * delta * delta * p.track_front / p.wheelbase
            if delta > 0.0:
                delta_l = delta + ack
            else:
                delta_r = delta - ack

        sigma_a = self.sigma_a
        sigma_k = self.sigma_k
        inv_sigma_a = 1.0 / sigma_a
        inv_sigma_k = 1.0 / sigma_k
        ds = trk.ds
        n_samples = trk.n
        length = trk.length
        air_t = atm.air_temperature
        rubber = surface_state.rubber
        c_tyre = compound
        mu_peak = c_tyre.mu_peak
        fz0_wheel = self.fz0_wheel
        load_sens = c_tyre.load_sensitivity
        roll_coef = c_tyre.rolling_resistance
        seg_temps = surface_state.track_temperature
        sum_fx = sum_fy = moment = 0.0
        brake_power_total = 0.0
        wx = self.wx
        wy = self.wy
        for i in range(4):
            xb = wx[i]
            yb = wy[i]
            delta_i = delta_l if i == 0 else (delta_r if i == 1 else 0.0)
            # contact location and surface
            along = xb * cd - yb * sd
            nw = n + xb * sd + yb * cd
            sw = s + along
            if sw < 0.0:
                sw += length
            elif sw >= length:
                sw -= length
            widx = int(sw / ds)
            if widx >= n_samples:
                widx = n_samples - 1
            stype = trk.surface_type(widx, nw)
            cell, seg = surface_state.cell(sw, nw, widx)
            half = trk.half_width[widx]
            film = surface_state.film(cell, nw, half)
            props = SURFACE_PROPERTIES[stype]
            wet = film / (film + 0.15) if film > 0.0 else 0.0
            surf_grip = trk.grip[widx] * props[0] * (1.0 + (props[1] - 1.0) * wet)
            if stype == SURFACE_ASPHALT:
                rub = rubber[cell]
                surf_grip *= 1.0 + 0.04 * rub * (1.0 - wet) - 0.08 * rub * wet
            self.surface[i] = stype
            self.film[i] = film

            # wheel-centre velocity in the wheel frame
            vxb = u - r * yb
            vyb = v + r * xb
            if delta_i != 0.0:
                c_d = math.cos(delta_i)
                s_d = math.sin(delta_i)
                vcx = vxb * c_d + vyb * s_d
                vcy = -vxb * s_d + vyb * c_d
            else:
                c_d = 1.0
                s_d = 0.0
                vcx = vxb
                vcy = vyb
            abs_vcx = vcx if vcx >= 0.0 else -vcx

            # lateral carcass deflection: exact solution of dv1/dt = Vcy - |Vcx|/sigma v1
            v1 = self.v1[i]
            rate = abs_vcx * inv_sigma_a
            if rate * dt > 1e-9:
                ss = vcy / rate
                v1 = ss + (v1 - ss) * math.exp(-rate * dt)
            else:
                v1 += vcy * dt

            # grip: load sensitivity x slow factors (temperature, wear, pressure,
            # damage) x surface x water film
            load = fz[i]
            if film > 0.005:
                water_f = tyre.water_factor(
                    film, 0.7 * trk.roughness[widx], abs_vcx, self.tyre_width[i], self.pressure[i]
                )[0]
            else:
                water_f = 1.0
            fz0 = fz0_wheel[i]
            lf = 1.0 - load_sens * (load - fz0) / fz0
            if lf < 0.55:
                lf = 0.55
            elif lf > 1.25:
                lf = 1.25
            mu = mu_peak * lf * self.slow_grip[i] * surf_grip * water_f
            self.grip[i] = mu

            # longitudinal deflection + wheel spin (implicit, linear in torque)
            radius = self.radius[i]
            inertia = self.wheel_inertia[i]
            if i >= 2:
                inertia += extra_inertia
                drive = t_left if i == 2 else t_right
                brake_torque = brake_rear
            else:
                drive = 0.0
                brake_torque = brake_front
            brake_torque *= self.pad[i] * self.abs_scale[i]
            friction_torque = brake_torque + roll_coef * props[2] * load * radius
            k = self.k_secant[i]
            w0 = omega[i]
            u1 = self.u1[i]
            ak = abs_vcx * inv_sigma_k
            denom = 1.0 + ak * dt + dt * dt * radius * radius * k / inertia
            u_free = (u1 + dt * (radius * w0 - vcx) + dt * dt * radius * drive / inertia) / denom
            w_free = w0 + dt * (drive - radius * k * u_free) / inertia
            u_t = dt * dt * radius / (inertia * denom)
            w_t = dt / inertia - dt * radius * k * u_t / inertia
            dw = w_t * friction_torque
            locked = False
            if w_free > dw:
                w_new = w_free - dw
                u_new = u_free - u_t * friction_torque
            elif w_free < -dw:
                w_new = w_free + dw
                u_new = u_free + u_t * friction_torque
            else:
                w_new = 0.0
                u_new = (u1 - dt * vcx) / (1.0 + ak * dt)
                locked = brake_torque > 0.0
            # bound the combined deflection (contact patch slides beyond saturation)
            kappa = u_new * inv_sigma_k
            alpha = math.atan(v1 * inv_sigma_a)
            sx = kappa * tyre.inv_kappa_peak
            sy = alpha * tyre.inv_alpha_peak
            smag = math.sqrt(sx * sx + sy * sy)
            if smag > MAX_NORMALISED_SLIP:
                scale = MAX_NORMALISED_SLIP / smag
                u_new *= scale
                v1 = sigma_a * math.tan(alpha * scale)
                kappa *= scale
                alpha *= scale
            fx, fy, smag = tyre.forces(kappa, alpha, load, mu)
            if u_new > 1e-7 or u_new < -1e-7:
                sec = fx / u_new
                self.k_secant[i] = sec if sec > 0.0 else 0.0
            else:
                self.k_secant[i] = mu * load * tyre.b_long * tyre.c_long / (tyre.kappa_peak * sigma_k)
            # off-road drag (grass/gravel) and low-speed carcass damping (body only)
            if props[3] > 0.0:
                fx -= props[3] * load * vcx
            if abs_vcx < LOW_SPEED and load > 0.0:
                # Carcass damping regularises standstill (micro-slip only) and is
                # bounded so it can never exceed the friction limit.
                c_low = LOW_SPEED_DAMPING * (load / GRAVITY) / dt * (1.0 - abs_vcx / LOW_SPEED)
                fy -= c_low * vcy
                slip_v = vcx - radius * w_new
                if -0.5 < slip_v < 0.5:
                    fx -= 0.5 * c_low * slip_v
                f2 = fx * fx + fy * fy
                limit = mu * load
                if f2 > limit * limit:
                    scale = limit / math.sqrt(f2)
                    fx *= scale
                    fy *= scale
            omega[i] = w_new
            self.u1[i] = u_new
            self.v1[i] = v1
            self.fx[i] = fx
            self.fy[i] = fy
            self.kappa[i] = kappa
            self.alpha[i] = alpha
            self.slip[i] = smag
            self.locked[i] = locked
            spin = self.spin[i] + w_new * dt
            if spin > 6.283185307179586 or spin < -6.283185307179586:
                spin = math.fmod(spin, 6.283185307179586)
            self.spin[i] = spin

            # body-frame forces and yaw moment
            fxb = fx * c_d - fy * s_d
            fyb = fx * s_d + fy * c_d
            sum_fx += fxb
            sum_fy += fyb
            moment += xb * fyb - yb * fxb

            # heat, wear, brakes, surface traffic
            slide_v_x = vcx - radius * w_new
            slide_power = abs(fx * slide_v_x) + abs(fy * vcy)
            self.acc_slide[i] += slide_power * dt
            power_b = brake_torque * (w_new if w_new >= 0.0 else -w_new)
            self.acc_brake[i] += power_b * dt
            brake_power_total += power_b
            self.w_speed[i] = abs_vcx
            self.w_rr[i] = props[2]
            self.w_track_t[i] = seg_temps[seg]
            self.w_rough[i] = trk.roughness[widx]
            if stype == SURFACE_ASPHALT or stype == 1:
                lane_w = 2.0 * half / surface_state.lanes
                sweep = self.tyre_width[i] * abs_vcx * dt / (lane_w * surface_state.seg_length)
                surface_state.traffic(cell, sweep, slide_power * dt)

        self.slow_phase += 1
        if self.slow_phase >= SLOW_EVERY:
            self.slow_phase = 0
            self._slow_update(dt * SLOW_EVERY, air_t)
        pt.harvest(brake_power_total, dt)
        if pt.auto:
            pt.auto_shift(0.5 * (omega[2] + omega[3]), brake > 0.2, self.slip[2] > 1.0 or self.slip[3] > 1.0)

        # 7. body integration
        fx_total = sum_fx - drag + m * gx
        fy_total = sum_fy + m * gy
        u += dt * fx_total / m
        v += dt * fy_total / m
        r += dt * moment / i_z
        rot = r * dt
        c_r = math.cos(rot)
        s_r = math.sin(rot)
        u, v = u * c_r + v * s_r, -u * s_r + v * c_r
        self.u, self.v, self.r = u, v, r
        self.psi += rot
        if self.psi > math.pi:
            self.psi -= 2.0 * math.pi
        elif self.psi < -math.pi:
            self.psi += 2.0 * math.pi
        sin_psi = math.sin(self.psi)
        cos_psi = math.cos(self.psi)
        vx_w = u * sin_psi + v * cos_psi
        vz_w = u * cos_psi - v * sin_psi
        self.x += vx_w * dt
        self.z += vz_w * dt
        self.distance += math.sqrt(vx_w * vx_w + vz_w * vz_w) * dt
        self.ax_spec = (sum_fx - drag) / m
        self.ay_spec = sum_fy / m
        self.ax_world = fx_total / m

    def _slow_update(self, dt_slow, air_t):
        """Thermal, wear, pressure and brake temperature at 30 Hz from accumulated energy."""
        p = self.p
        compound = self.tyre.compound
        for i in range(4):
            slide_energy = self.acc_slide[i]
            update_thermal(
                self,
                i,
                dt_slow,
                slide_energy / dt_slow,
                self.w_speed[i],
                self.fz[i],
                compound.rolling_resistance * self.w_rr[i],
                air_t,
                self.w_track_t[i],
                self.film[i],
                compound,
            )
            wear = self.wear[i] + wear_increment(
                compound, slide_energy, self.surface_t[i], self.w_rough[i], self.film[i] < 0.05
            )
            self.wear[i] = wear if wear < 1.0 else 1.0
            self.brake_t[i] = update_disc_temperature(
                self.brake_t[i],
                self.acc_brake[i] / dt_slow,
                self.w_speed[i],
                air_t,
                dt_slow,
                p.brake_heat_capacity,
                p.brake_cooling_base,
                p.brake_cooling_per_speed,
            )
            self.acc_slide[i] = 0.0
            self.acc_brake[i] = 0.0
        self._refresh_factors()

    def _refresh_factors(self):
        p = self.p
        tyre = self.tyre
        bt_lo, bt_hi = p.brake_temp_optimal
        damage_f = 1.0 - 0.12 * self.damage
        for i in range(4):
            pressure = hot_pressure(p.cold_pressure, self.core_t[i])
            self.pressure[i] = pressure
            self.slow_grip[i] = (
                tyre.temperature_factor(self.surface_t[i], self.core_t[i])
                * tyre.wear_factor(self.wear[i])
                * tyre.pressure_factor(pressure)
                * damage_f
            )
            self.pad[i] = pad_factor(self.brake_t[i], bt_lo, bt_hi)

    # ------------------------------------------------------------ telemetry
    def rpm(self):
        return self.powertrain.rpm

    def wheel_speeds(self):
        return [w * rr for w, rr in zip(self.omega, self.radius)]

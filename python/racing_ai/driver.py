"""Deterministic AI driver.

Layers (each is a small, testable function or class):

1. Perception (``Perception``): opponents' track positions are observed
   with a skill-dependent delay (reaction time); the AI never reads other
   cars' future inputs.
2. Strategy (``pace_mode``): push / normal / conserve from tyre wear and
   temperature, fuel and race situation; adjusts grip usage slightly.
3. Tactics: overtaking (side choice toward the inside of the next corner,
   commitment and abort rules), defence (one move toward the inside before
   a braking zone), and collision prediction (time to collision with the
   car ahead in the same lane, speed matching).
4. Speed planning: target = min over the preview window of
   sqrt(v_ref(j)^2 + 2 a_brake d_j) using the session's QSS profile ladder and
   the observed wet grip, scaled by the skill's grip usage and lap-to-lap
   consistency noise.
5. Control: Pure Pursuit steering toward a preview point on the desired path
   (racing line + tactical offset + precision noise) with yaw-rate damping;
   pedal control from the speed error with feed-forward braking capability;
   wheelspin and lock-up modulation by "feel" (driver skill, not an
   electronic aid).

Everything is seeded and deterministic.
"""

import math

from physics_engine.mathutil import SplitMix64, clamp

from .difficulty import skill as skill_for

TWO_PI = 2.0 * math.pi


class Perception:
    """Ring buffer of every car's (s, n, u) for delayed observation."""

    def __init__(self, cars, history_seconds=0.6, dt=1.0 / 120.0):
        self.size = int(history_seconds / dt) + 2
        self.buffer = [[(c.s, c.n, c.u) for c in cars] for _ in range(self.size)]
        self.head = 0
        self.dt = dt

    def record(self, cars):
        self.head = (self.head + 1) % self.size
        self.buffer[self.head] = [(c.s, c.n, c.u) for c in cars]

    def observe(self, delay):
        steps = min(self.size - 1, int(delay / self.dt))
        return self.buffer[(self.head - steps) % self.size]


def pace_mode(car, tracker, laps_total):
    """Strategy: returns (mode, grip_adjust)."""
    wear = max(car.wear)
    compound = car.tyre.compound
    effective = max(0.65 * ts + 0.35 * tc for ts, tc in zip(car.surface_t, car.core_t))
    hot = effective - (compound.temp_optimal + 0.5 * compound.temp_window)
    remaining = laps_total - tracker.completed
    if remaining <= 1:
        return "push", 0.01
    if wear > 0.65 or hot > 0.0:
        return "conserve", -0.045
    if car.powertrain.fuel < 0.6:
        return "conserve", -0.05
    return "normal", 0.0


class AIDriver:
    def __init__(self, index, sim, plan, level, seed):
        self.index = index
        self.sim = sim
        self.plan = plan
        self.skill = skill_for(level)
        self.rng = SplitMix64(seed * 7919 + index * 104729 + 17)
        rng = self.rng
        self.noise_phase = [rng.uniform(0.0, TWO_PI) for _ in range(3)]
        self.noise_freq = [0.11 + 0.05 * rng.random(), 0.23 + 0.07 * rng.random(), 0.41 + 0.1 * rng.random()]
        self.lap_factor = 1.0
        self.last_lap = -1
        self.decision_timer = 0.0
        self.target_speed = 0.0
        self.tactical = 0.0
        self.tactical_target = 0.0
        self.overtaking = None  # (target car index, side, started time)
        self.defended_zone = -1
        self.mode = "normal"
        self.grip_adjust = 0.0
        self.throttle = 0.0
        self.brake = 0.0
        self.stuck_time = 0.0
        self.follow_limit = float("inf")
        self.last_steer = 0.0
        self.local_wet = 1.0
        self.lat_lo = -1e9
        self.lat_hi = 1e9
        self.grid_lane = sim.cars[index].n

    # --------------------------------------------------------- helpers
    def _noise(self, t):
        a = self.skill.lateral_noise
        if a <= 0.0:
            return 0.0
        p = self.noise_phase
        f = self.noise_freq
        return a * (
            0.6 * math.sin(TWO_PI * f[0] * t + p[0])
            + 0.3 * math.sin(TWO_PI * f[1] * t + p[1])
            + 0.1 * math.sin(TWO_PI * f[2] * t + p[2])
        )

    def _local_wet(self):
        """Wet-grip factor observed in the car's own lane over the next ~60 m."""
        me = self.sim.cars[self.index]
        surface = self.sim.track.surface
        track = self.sim.track
        total = 0.0
        count = 0
        for d in (5.0, 20.0, 35.0, 50.0, 65.0):
            s = (me.s + d) % track.length
            idx = track.index_at(s)
            cell, _ = surface.cell(s, me.n, idx)
            total += surface.film(cell, me.n, track.half_width[idx])
            count += 1
        film = total / count
        if film <= 0.005:
            return 1.0
        factor, _ = self.plan.tyre.water_factor(
            film, 0.7 * track.roughness[me.track_index], max(me.u, 10.0), me.p.tyre_width_rear, me.pressure[2]
        )
        return factor

    def _grip_level(self, index, near=False):
        car = self.sim.cars[self.index]
        wet = self.plan.grip_estimate[index]
        if near and self.local_wet < wet:
            wet = self.local_wet
        # dirty air: the driver feels the downforce loss of following closely
        weight = car.mass * 9.80665
        aero_share = car.downforce / (car.downforce + weight) if car.downforce > 0.0 else 0.0
        wake = 1.0 - (1.0 - car.wake_down) * aero_share * 1.6
        # uncertainty margin grows with how much the water lowers grip
        usage = self.skill.grip_usage + self.grip_adjust - 0.25 * (1.0 - wet)
        # tyre "feel": temperature/wear/pressure state of the driver's own tyres
        g = car.slow_grip
        wet *= 0.125 * (g[0] + g[1] + g[2] + g[3]) + 0.5 * min(g)
        return clamp(wet * usage * self.lap_factor * wake, 0.2, 1.06) * (
            1.0 - 0.1 * car.damage
        )

    # -------------------------------------------------------- decisions
    def _decide(self, perception):
        sim = self.sim
        me = sim.cars[self.index]
        tracker = sim.trackers[self.index]
        track = sim.track
        length = track.length
        sk = self.skill
        if tracker.lap != self.last_lap:
            self.last_lap = tracker.lap
            self.lap_factor = 1.0 + sk.consistency * clamp(self.rng.gauss(), -2.5, 2.5)
        self.mode, self.grip_adjust = pace_mode(me, tracker, sim.laps)
        # --- speed target over the preview window (braking envelope)
        self.local_wet = self._local_wet() if sim.atmosphere.rain_rate_mm_h > 0.0 or me.film[0] > 0.0 else 1.0
        n = track.n
        ds = track.ds
        idx = me.track_index
        u = max(me.u, 0.0)
        grip_here = self._grip_level(idx, True)
        decel = self.plan.brake_capability(max(u, 10.0), grip_here) * 0.92
        horizon = u * u / (2.0 * max(decel, 3.0)) + 25.0 + sk.braking_margin
        stride = 3
        target = float("inf")
        steps = int(horizon / ds) + 1
        line_k = self.plan.line_k
        deviation = me.n - self.plan.offset(me.s)
        for k in range(0, steps, stride):
            j = (idx + k) % n
            v_j = self.plan.target_speed(j, self._grip_level(j, k * ds < 80.0))
            # off the racing line: the path followed is the line shifted by the
            # current deviation dn, with curvature kappa' = kappa / (1 - dn kappa)
            if k * ds < 80.0 and abs(deviation) > 0.5:
                k_line = line_k[j]
                denom = 1.0 - deviation * k_line
                if denom > 0.2:
                    k_path = abs(k_line / denom)
                    if k_path > abs(k_line) * 1.03 and k_path > 1.0 / 400.0:
                        v_j *= max(0.55, math.sqrt(abs(k_line) / k_path))
            d = k * ds - sk.braking_margin
            reach = math.sqrt(v_j * v_j + 2.0 * decel * max(d, 0.0))
            if reach < target:
                target = reach
        # --- opponents (delayed perception)
        observed = perception.observe(sk.reaction_time)
        my_s, my_n = me.s, me.n
        ahead = None
        ahead_gap = float("inf")
        behind_gap = float("inf")
        behind = None
        alongside = []
        for j, (s_o, n_o, u_o) in enumerate(observed):
            if j == self.index:
                continue
            gap = s_o - my_s
            if gap > 0.5 * length:
                gap -= length
            elif gap < -0.5 * length:
                gap += length
            if abs(gap) < me.p.length + 1.0:
                alongside.append((j, n_o))
            if 0.0 < gap < ahead_gap and abs(n_o - my_n) < me.p.width + 0.6:
                ahead_gap, ahead = gap, (j, s_o, n_o, u_o)
            if -25.0 < gap < 0.0 and -gap < behind_gap:
                behind_gap, behind = -gap, (j, s_o, n_o, u_o)
        half = track.half_width[idx]
        limit_n = half - 1.3
        self.follow_limit = float("inf")
        # peripheral awareness of cars alongside (short delay) -> lateral corridor
        near = perception.observe(min(sk.reaction_time, 0.12))
        lo, hi = -1e9, 1e9
        squeeze_speed = float("inf")
        width = me.p.width + 0.6
        for j, (s_o, n_o, u_o) in enumerate(near):
            if j == self.index:
                continue
            gap = s_o - my_s
            if gap > 0.5 * length:
                gap -= length
            elif gap < -0.5 * length:
                gap += length
            if abs(gap) < me.p.length + 1.0:
                if n_o >= my_n:
                    hi = min(hi, n_o - width)
                else:
                    lo = max(lo, n_o + width)
                if (lo > hi or lo > limit_n or hi < -limit_n) and gap > -me.p.length:
                    # no lateral room on track next to this car: back out of the overlap
                    squeeze_speed = min(squeeze_speed, max(0.0, u_o - 2.0))
        if lo > hi:
            lo = hi = my_n
        self.lat_lo, self.lat_hi = lo, hi
        if ahead is not None and ahead_gap < 70.0:
            j, s_o, n_o, u_o = ahead
            closing = u - u_o
            clear = ahead_gap - me.p.length
            ttc = clear / closing if closing > 0.1 else float("inf")
            wants_pass = closing > 0.5 or target > u_o + 3.0
            if (
                self.overtaking is None
                and me.distance > 250.0
                and wants_pass
                and closing > 1.0
                and clear < 30.0
                and self.rng.random() < sk.aggression
            ):
                side = self._choose_side(idx, n_o, limit_n, alongside)
                if side != 0:
                    self.overtaking = (j, side, sim.time)
            beside = self.overtaking is not None and self.overtaking[0] == j and abs(n_o - my_n) > me.p.width + 0.3
            if not beside:
                # follow: never closer than a speed-dependent gap
                safe = 4.0 + 0.18 * u
                if clear < safe or ttc < 1.2 + sk.reaction_time:
                    self.follow_limit = max(0.0, u_o + 0.6 * (clear - safe))
        if self.overtaking is not None:
            j, side, started = self.overtaking
            other = observed[j]
            gap = other[0] - my_s
            if gap > 0.5 * length:
                gap -= length
            elif gap < -0.5 * length:
                gap += length
            blocked = any(abs(n_o - side * limit_n) < me.p.width and k != j for k, n_o in alongside)
            if gap < -me.p.length - 4.0 or sim.time - started > 7.0 or blocked:
                self.overtaking = None
            else:
                absolute = clamp(other[1] + side * (me.p.width + 1.0), -limit_n, limit_n)
                self.tactical_target = absolute - self.plan.offset(my_s)
        if self.overtaking is None:
            self.tactical_target = 0.0
            # defence: one move toward the inside before a braking zone
            if sk.defends and behind is not None and behind[3] > u - 0.5:
                corner_dir = self._next_corner_direction(idx, 220.0)
                if corner_dir != 0 and self.defended_zone != self._zone_key(idx):
                    self.defended_zone = self._zone_key(idx)
                    self.tactical_target = corner_dir * (limit_n - 0.8) - self.plan.offset(my_s)
                elif self.defended_zone == self._zone_key(idx):
                    self.tactical_target = self.tactical
        self.target_speed = min(target, self.follow_limit, squeeze_speed)
        # start: hold the grid lane for the first metres
        if me.distance < 180.0 and self.overtaking is None:
            self.tactical_target = self.grid_lane - self.plan.offset(my_s)
        # off the asphalt: rejoin progressively and without racing
        if abs(my_n) > half + 0.5:
            self.overtaking = None
            self.tactical_target = 0.0
            self.target_speed = min(self.target_speed, max(12.0, 0.6 * u))

    def _zone_key(self, idx):
        return int(idx * self.sim.track.ds / 150.0)

    def _next_corner_direction(self, idx, distance):
        track = self.sim.track
        n = track.n
        best = 0.0
        for k in range(0, int(distance / track.ds), 4):
            c = track.curvature[(idx + k) % n]
            if abs(c) > abs(best):
                best = c
        if abs(best) < 1.0 / 250.0:
            return 0
        return 1 if best > 0.0 else -1

    def _choose_side(self, idx, other_n, limit_n, alongside):
        direction = self._next_corner_direction(idx, 250.0)
        me_w = self.sim.cars[self.index].p.width
        options = []
        for side in (1, -1):
            room = limit_n - side * other_n
            if room > me_w + 0.8 and not any(side * (n_o - other_n) > 0 for _, n_o in alongside):
                options.append(side)
        if not options:
            return 0
        if direction in options:
            return direction
        return options[0]

    # ---------------------------------------------------------- control
    def control(self, perception, dt):
        sim = self.sim
        me = sim.cars[self.index]
        track = sim.track
        sk = self.skill
        self.decision_timer -= dt
        if self.decision_timer <= 0.0:
            self.decision_timer += 1.0 / 30.0
            self._decide(perception)
        # tactical offset moves at a bounded lateral rate
        rate = 2.5 * dt
        self.tactical += clamp(self.tactical_target - self.tactical, -rate, rate)
        u = me.u
        speed = max(u, 1.0)
        # Pure Pursuit target point
        lookahead = clamp((7.0 + 0.42 * speed) * sk.lookahead_gain, 8.0, 48.0)
        s_t = me.s + lookahead
        half_here = track.half_width[me.track_index] - 1.1
        n_t = self.plan.offset(s_t) + self.tactical + self._noise(sim.time)
        n_t = clamp(clamp(n_t, self.lat_lo, self.lat_hi), -half_here, half_here)
        tx, _, tz, _ = track.world_point(s_t, n_t)
        dx = tx - me.x
        dz = tz - me.z
        sin_p = math.sin(me.psi)
        cos_p = math.cos(me.psi)
        xb = dx * sin_p + dz * cos_p  # forward
        yb = dx * cos_p - dz * sin_p  # left
        dist2 = xb * xb + yb * yb
        curvature = 2.0 * yb / dist2 if dist2 > 1e-6 else 0.0
        wheelbase = me.p.wheelbase
        delta = math.atan(wheelbase * curvature)
        # yaw-rate damping toward the pure-pursuit yaw rate
        delta += 0.35 * (curvature * speed - me.r) * wheelbase / speed
        steer = clamp(-delta / me.p.max_steer, -1.0, 1.0)
        # pedals: desired acceleration bounded by the friction ellipse, using the
        # grip the driver currently feels (mean peak mu of the four tyres)
        v_target = self.target_speed
        g = 9.80665
        mass = me.mass
        grip_g = me.grip
        mu_feel = 0.25 * (grip_g[0] + grip_g[1] + grip_g[2] + grip_g[3])
        mu_g = mu_feel * (g + me.downforce / mass)
        a_lat = abs(u * me.r)
        util = min(1.0, a_lat / max(mu_g, 1.0))
        ellipse = math.sqrt(max(0.0, 1.0 - util * util))
        a_avail = mu_g * ellipse
        a_brake_avail = mu_g * max(0.45, ellipse)
        a_des = 3.0 * (v_target - u)
        resist = me.drag + 0.015 * mass * g
        env = self.plan.envelopes[-2]
        if a_des >= 0.0:
            a_cmd = min(a_des, 0.75 * a_avail * (0.6 + 0.4 * sk.slip_management))
            force = mass * a_cmd + resist
            throttle = clamp(force / max(env.wheel_force_max(max(u, 1.0)), 1.0), 0.0, 1.0)
            brake = 0.0
        else:
            a_cmd = max(a_des, -a_brake_avail)
            force = -mass * a_cmd - resist
            throttle = 0.0
            brake = clamp(force * me.p.tyre_radius_front / me.p.brake_torque_total, 0.0, 1.0)
        # slip management by feel (driver skill, not an electronic aid)
        rear_slip = max(me.slip[2], me.slip[3])
        if throttle > 0.0 and rear_slip > 0.9:
            cut = clamp((rear_slip - 0.9) / 0.5, 0.0, 1.0) * sk.slip_management
            throttle *= 1.0 - 0.9 * cut
        # understeer felt through the steering: front axle beyond its peak -> lift
        front_slip = 0.5 * (me.slip[0] + me.slip[1])
        if front_slip > 1.05:
            ease = clamp((front_slip - 1.05) / 0.6, 0.0, 1.0)
            throttle *= 1.0 - ease
            if v_target < u + 2.0:
                brake = max(brake, 0.12 * ease * sk.slip_management)
        # oversteer: rear slip angle clearly beyond the front -> lift
        front_a = 0.5 * (abs(me.alpha[0]) + abs(me.alpha[1]))
        rear_a = 0.5 * (abs(me.alpha[2]) + abs(me.alpha[3]))
        if rear_a > front_a + 0.025 and rear_a > 0.5 * me.tyre.alpha_peak:
            throttle *= 1.0 - 0.8 * sk.slip_management
        kp = me.tyre.kappa_peak
        # rear locking on overrun (engine braking): a throttle blip frees the axle
        rear_lock = min(me.kappa[2], me.kappa[3])
        if brake < 0.05 and rear_lock < -1.2 * kp:
            throttle = max(throttle, 0.18 * sk.slip_management)
        lock = min(me.kappa)
        if brake > 0.0 and lock < -1.25 * kp:
            release = clamp((-lock - 1.25 * kp) / kp, 0.0, 1.0) * sk.slip_management
            brake *= 1.0 - 0.85 * release
        # smooth pedal application (human actuation)
        self.throttle += clamp(throttle - self.throttle, -8.0 * dt, 5.0 * dt)
        self.brake += clamp(brake - self.brake, -8.0 * dt, 9.0 * dt)
        # recovery when stuck (marshal reset, same rule as the player's reset)
        if abs(u) < 1.0 and self.target_speed > 5.0:
            self.stuck_time += dt
            if self.stuck_time > 4.0:
                sim.reset_car(self.index)
                self.stuck_time = 0.0
        else:
            self.stuck_time = 0.0
        drs = me.drs_allowed and abs(track.curvature[me.track_index]) < 1.0 / 600.0
        self.last_steer = steer
        return (self.throttle, self.brake, steer, drs)

"""Engine, clutch/launch, gearbox, differential, ERS and fuel.

* Engine: full-load torque table T(rpm) (piecewise linear), part throttle
  ``T = u T_full(rpm) - (1 - u) T_friction(rpm)`` so a closed throttle gives
  engine braking ``T_friction = c0 + c1 rpm``. Torque is cut at the rev limit.
* Clutch: below the launch speed the clutch slips; engine speed is held at
  ``idle + (launch - idle) u`` and the engine torque at that speed is
  transmitted (automatic launch clutch). When locked, the engine inertia is
  reflected to the driven wheels as ``I_e G^2``.
* Gearbox: overall ratio ``G = i_gear i_final``; positive torque is multiplied
  by the driveline efficiency, negative (overrun) torque is not. Every shift
  interrupts drive torque for ``shift_time``.
* Differential: torque-sensing limited slip with locking capacity
  ``T_cap = preload + ramp |T_in|`` (Salisbury-type approximation).
* ERS (where fitted): deploys up to ``ers_power`` at full throttle within a
  per-lap energy budget; harvests braking power up to the same power within
  the per-lap harvest budget (bookkeeping only; brake-by-wire keeps total
  braking torque unchanged).
* Fuel: mass flow = BSFC x positive brake power; fuel mass feeds total mass.
"""

import math

from .mathutil import clamp, interp_table

RPM_PER_RAD_S = 30.0 / math.pi


class Powertrain:
    __slots__ = (
        "p",
        "gear",
        "shift_timer",
        "rpm",
        "fuel",
        "ers_store",
        "ers_deployed",
        "ers_harvested",
        "clutch_locked",
        "engine_power",
        "ers_active",
        "limiter",
        "auto",
    )

    def __init__(self, params, fuel_kg):
        self.p = params
        self.gear = 1
        self.shift_timer = 0.0
        self.rpm = params.idle_rpm
        self.fuel = clamp(fuel_kg, 0.0, params.fuel_capacity)
        self.ers_store = params.ers_battery
        self.ers_deployed = 0.0
        self.ers_harvested = 0.0
        self.clutch_locked = False
        self.engine_power = 0.0
        self.ers_active = False
        self.limiter = False
        self.auto = True

    def overall_ratio(self, gear=None):
        g = self.gear if gear is None else gear
        p = self.p
        if g == 0:
            return 0.0
        if g < 0:
            return -p.gear_ratios[0] * 1.15 * p.final_drive
        return p.gear_ratios[g - 1] * p.final_drive

    def full_torque(self, rpm):
        return interp_table(self.p.torque_rpm, self.p.torque_nm, rpm)

    def friction_torque(self, rpm):
        return self.p.friction_torque_base + self.p.friction_torque_per_rpm * rpm

    def new_lap(self):
        self.ers_deployed = 0.0
        self.ers_harvested = 0.0

    def reflected_inertia(self):
        """Engine inertia seen at the driven axle (both wheels together)."""
        if not self.clutch_locked or self.gear == 0:
            return 0.0
        ratio = self.overall_ratio()
        return self.p.engine_inertia * ratio * ratio

    def axle_torque(self, throttle, axle_omega, dt):
        """Drive torque at the driven axle (N m) for this step and update engine state.

        ``axle_omega`` is the mean angular speed of the driven wheels (rad/s).
        """
        p = self.p
        self.ers_active = False
        self.limiter = False
        if self.shift_timer > 0.0:
            self.shift_timer -= dt
        ratio = self.overall_ratio()
        if ratio == 0.0 or self.fuel <= 0.0:
            self.clutch_locked = False
            self.rpm += (p.idle_rpm - self.rpm) * min(1.0, dt * 4.0)
            self.engine_power = 0.0
            return 0.0
        wheel_rpm = abs(axle_omega * ratio) * RPM_PER_RAD_S
        target = p.idle_rpm + (p.launch_rpm - p.idle_rpm) * throttle
        if wheel_rpm < target and wheel_rpm < p.launch_rpm:
            # Slipping automatic clutch: engine speed follows the launch target.
            self.clutch_locked = False
            self.rpm += (target - self.rpm) * min(1.0, dt * 12.0)
            if throttle <= 0.02:
                self.engine_power = 0.0
                return 0.0
        else:
            self.clutch_locked = True
            self.rpm = wheel_rpm
        rpm = self.rpm
        u = throttle
        if rpm >= p.rev_limit:
            u = 0.0
            self.limiter = True
        engine = u * self.full_torque(rpm) - (1.0 - u) * self.friction_torque(rpm) * p.engine_brake_factor
        if self.shift_timer > 0.0:
            engine = 0.0
        omega_e = rpm / RPM_PER_RAD_S
        self.engine_power = engine * omega_e if engine > 0.0 else 0.0
        if self.engine_power > 0.0:
            self.fuel = max(0.0, self.fuel - p.bsfc * self.engine_power * dt)
        torque = engine * ratio * (p.driveline_efficiency if engine > 0.0 else 1.0)
        # ERS deployment (at the axle, behaves like extra power at full throttle).
        # The rev limiter cuts the whole power unit, ERS included.
        if p.ers_power > 0.0 and throttle > 0.9 and self.shift_timer <= 0.0 and self.gear > 0 and not self.limiter:
            budget = min(p.ers_deploy_per_lap - self.ers_deployed, self.ers_store)
            w = abs(axle_omega)
            if budget > 0.0 and w > 5.0:
                power = min(p.ers_power, budget / dt)
                torque += power / w
                spent = power * dt
                self.ers_deployed += spent
                self.ers_store -= spent
                self.ers_active = True
        return torque

    def harvest(self, brake_power, dt):
        p = self.p
        if p.ers_power <= 0.0 or brake_power <= 0.0:
            return
        room = min(p.ers_harvest_per_lap - self.ers_harvested, p.ers_battery - self.ers_store)
        if room <= 0.0:
            return
        energy = min(p.ers_power, brake_power) * dt
        if energy > room:
            energy = room
        self.ers_harvested += energy
        self.ers_store += energy

    def auto_shift(self, axle_omega, braking, rear_sliding=False):
        """Upshift near the shift point, downshift to stay in the power band.

        Downshifts are withheld while the driven wheels are already sliding
        (downshift protection), as an automatic gearbox controller would.
        """
        p = self.p
        if self.shift_timer > 0.0 or self.gear <= 0:
            return
        top = len(p.gear_ratios)
        rpm_here = abs(axle_omega * self.overall_ratio()) * RPM_PER_RAD_S
        if self.gear < top and rpm_here >= p.upshift_rpm:
            self.gear += 1
            self.shift_timer = p.shift_time
            return
        if self.gear > 1 and not rear_sliding:
            lower = abs(axle_omega * self.overall_ratio(self.gear - 1)) * RPM_PER_RAD_S
            threshold = p.downshift_rpm * (1.08 if braking else 1.0)
            if rpm_here < threshold and lower < p.upshift_rpm * 0.94:
                self.gear -= 1
                self.shift_timer = p.shift_time

    def request_shift(self, direction):
        top = len(self.p.gear_ratios)
        if self.shift_timer > 0.0:
            return
        new = clamp(self.gear + direction, -1, top)
        if new != self.gear:
            self.gear = new
            self.shift_timer = self.p.shift_time


def differential_split(axle_torque, omega_left, omega_right, inertia, dt, preload, ramp):
    """Split drive torque between the driven wheels with a limited-slip lock.

    The lock moves torque from the faster to the slower wheel, at most the
    amount that would equalise their speeds within the step (relaxation 0.5)
    and never more than the clutch capacity.
    """
    half = 0.5 * axle_torque
    capacity = preload + ramp * abs(axle_torque)
    needed = 0.5 * inertia * (omega_left - omega_right) / dt * 0.5
    lock = clamp(needed, -capacity, capacity)
    return half - lock, half + lock

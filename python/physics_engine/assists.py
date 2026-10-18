"""Player input processing and optional driving assists.

These are *game* features applied to the player's commands before they reach
the vehicle model; the physics itself is unchanged and identical for every
car. AI drivers do not use them (they manage slip with their own control).

* Digital (keyboard/touch) inputs are ramped like a human foot/hand: throttle
  0->1 in ~0.25 s, brake in ~0.15 s, and the steering command moves at a
  bounded rate, reduced with speed so a key press does not demand full lock
  at 300 km/h. Analog (gamepad) inputs pass through.
* Traction control (assist): reduces throttle when the rear slip ratio
  exceeds the compound's peak.
* Anti-lock (assist): releases brake pressure per wheel when that wheel's slip
  ratio is beyond the peak.
* Reverse: holding the brake at a standstill engages reverse; the brake key
  then drives backwards until throttle is pressed.
"""

from .mathutil import clamp

ASSIST_LEVELS = ("full", "medium", "off")


class PlayerController:
    def __init__(self, assists="full"):
        self.assists = assists if assists in ASSIST_LEVELS else "full"
        self.throttle = 0.0
        self.brake = 0.0
        self.steer = 0.0
        self.reverse = False
        self.hold = 0.0

    def process(self, car, dt, throttle, brake, steer, drs, analog):
        throttle = clamp(throttle, 0.0, 1.0)
        brake = clamp(brake, 0.0, 1.0)
        steer = clamp(steer, -1.0, 1.0)
        speed = car.u
        # reverse gear handling
        pt = car.powertrain
        if not self.reverse:
            if abs(speed) < 0.4 and brake > 0.5 and throttle < 0.1:
                self.hold += dt
                if self.hold > 0.6:
                    self.reverse = True
                    pt.gear = -1
            else:
                self.hold = 0.0
        if self.reverse:
            if throttle > 0.1 or speed > 1.0:
                self.reverse = False
                self.hold = 0.0
                if pt.gear < 1:
                    pt.gear = 1
            else:
                throttle, brake = brake, 0.0
        if analog:
            self.throttle, self.brake, self.steer = throttle, brake, steer
        else:
            self.throttle += clamp(throttle - self.throttle, -8.0 * dt, 4.0 * dt)
            self.brake += clamp(brake - self.brake, -10.0 * dt, 7.0 * dt)
            limit = 1.0 / (1.0 + max(speed, 0.0) / 32.0)
            target = steer * max(limit, 0.22)
            rate = (3.0 if abs(target) > abs(self.steer) else 5.0) * dt
            self.steer += clamp(target - self.steer, -rate, rate)
        throttle_out = self.throttle
        brake_out = self.brake
        kp = car.tyre.kappa_peak
        if self.assists in ("full", "medium") and not self.reverse:
            rear = max(car.kappa[2], car.kappa[3])
            threshold = 1.15 * kp if self.assists == "full" else 1.6 * kp
            if rear > threshold:
                cut = clamp((rear - threshold) / kp, 0.0, 1.0)
                throttle_out *= 1.0 - 0.85 * cut
        if self.assists == "full" and not self.reverse and brake_out < 0.05:
            # engine-braking assist: blip when the driven axle starts to lock on overrun
            if min(car.kappa[2], car.kappa[3]) < -1.2 * kp:
                throttle_out = max(throttle_out, 0.15)
        scales = car.abs_scale
        if self.assists == "full":
            for i in range(4):
                k = car.kappa[i]
                if brake_out > 0.0 and k < -1.1 * kp:
                    scales[i] = clamp(scales[i] - 12.0 * dt, 0.35, 1.0)
                else:
                    scales[i] = clamp(scales[i] + 8.0 * dt, 0.35, 1.0)
        else:
            for i in range(4):
                scales[i] = 1.0
        return throttle_out, brake_out, self.steer, drs

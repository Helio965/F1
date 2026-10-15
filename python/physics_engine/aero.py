"""Parametric aerodynamic model.

Sign convention: drag acts along -x (body), downforce acts toward the road
(-z body) and is reported as a positive magnitude. Forces are

    F_drag      = 0.5 * rho * CdA * V^2
    F_down,axle = 0.5 * rho * ClA_axle * V^2

with ``V`` the longitudinal airspeed (car speed minus head-wind component).
ClA and CdA are modified by:

* wing level (0..1 setup): linear map onto ``wing_cla_range`` / ``wing_cda_range``;
* DRS (if fitted and open): rear ClA and CdA multiplied by their factors;
* ride height: approximate ground effect, ClA scales with
  ``1 + k_ge * (h_ref - h_mean) / h_ref`` and stalls progressively when the
  mean ride height falls below ``stall_height``;
* rake: front/rear ride-height difference moves load between axles (centre of
  pressure shift);
* wake (slipstream/"dirty air") from cars ahead: ``drag_factor`` and
  ``downforce_factor`` supplied by the simulation;
* damage: downforce loss proportional to the damage fraction.

The model cannot create unlimited grip: downforce adds normal load, and the
tyre friction coefficient falls with load (load sensitivity).
"""

from .mathutil import clamp, smoothstep


class AeroModel:
    __slots__ = (
        "cla_front",
        "cla_rear",
        "cda",
        "ground_effect",
        "h_ref",
        "stall_h",
        "stall_loss",
        "drs_available",
        "drs_rear",
        "drs_drag",
        "wing_cla",
        "wing_cda",
        "damage_loss",
    )

    def __init__(self, params, wing_level=0.5):
        self.cla_front = params.cla_front
        self.cla_rear = params.cla_rear
        self.cda = params.cda
        self.ground_effect = params.ground_effect
        self.h_ref = params.ride_height_ref
        self.stall_h = params.stall_height
        self.stall_loss = params.stall_loss
        self.drs_available = params.drs
        self.drs_rear = params.drs_cla_rear_factor
        self.drs_drag = params.drs_cda_factor
        self.damage_loss = params.damage_aero_loss
        self.wing_cla = 1.0
        self.wing_cda = 1.0
        self.set_wing(params, wing_level)

    def set_wing(self, params, level):
        level = clamp(level, 0.0, 1.0)
        lo, hi = params.wing_cla_range
        self.wing_cla = lo + (hi - lo) * level
        lo, hi = params.wing_cda_range
        self.wing_cda = lo + (hi - lo) * level

    def coefficients(self, h_front, h_rear, drs_open, damage, wake_drag, wake_down):
        """Return (ClA_front, ClA_rear, CdA) for the current state."""
        h_mean = 0.5 * (h_front + h_rear)
        ge = 1.0 + self.ground_effect * clamp((self.h_ref - h_mean) / self.h_ref, -0.8, 0.8)
        if h_mean < self.stall_h:
            ge *= 1.0 - self.stall_loss * smoothstep(self.stall_h, 0.0, h_mean)
        # Rake (rear higher than front) shifts the centre of pressure forward.
        rake = clamp((h_rear - h_front) / self.h_ref - 0.8, -1.0, 1.0)
        shift = 0.04 * rake
        damage_factor = 1.0 - self.damage_loss * damage
        common = ge * self.wing_cla * damage_factor * wake_down
        total = (self.cla_front + self.cla_rear) * common
        front_share = self.cla_front / (self.cla_front + self.cla_rear) + shift
        cla_f = total * front_share
        cla_r = total * (1.0 - front_share)
        cda = self.cda * self.wing_cda * wake_drag * (1.0 + 0.08 * damage)
        if drs_open and self.drs_available:
            cla_r *= self.drs_rear
            cda *= self.drs_drag
        return cla_f, cla_r, cda


def wake_factors(gap, lateral_offset, leader_width):
    """Slipstream (drag reduction) and dirty-air (downforce loss) behind a car.

    Approximate exponential wake: strongest directly behind the leading car,
    decaying with longitudinal gap (m) and lateral misalignment (m). Returns
    (drag_factor, downforce_factor), both <= 1.
    """
    if gap <= 0.5 or gap > 80.0:
        return 1.0, 1.0
    width = leader_width * (1.0 + gap / 40.0)
    lateral = smoothstep(width, 0.4 * width, abs(lateral_offset))
    longitudinal_drag = 0.32 * pow(2.718281828, -gap / 22.0)
    longitudinal_down = 0.28 * pow(2.718281828, -gap / 30.0)
    return 1.0 - longitudinal_drag * lateral, 1.0 - longitudinal_down * lateral

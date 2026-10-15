"""Tyre force, thermal, wear and water-interaction model.

Force model (docs/PHYSICS_EQUATIONS.md, section "Pneus"):

* Transient slip uses first-order carcass relaxation (Pacejka, *Tyre and
  Vehicle Dynamics*, single-contact-point transient model). The vehicle owns
  the deflection states; this module turns them into forces.
* Steady-state shape: normalised Magic Formula ``f(s) = sin(C atan(B s))``
  with ``B = tan(pi / (2 C))`` so the peak is exactly at ``s = 1``.
* Combined slip: normalised slip vector ``(kappa / kappa_peak,
  alpha / alpha_peak)``; its magnitude drives the shape function and its
  direction splits the force. Braking/traction and cornering therefore share
  one friction ellipse of semi-axes ``mu_x Fz`` and ``mu_y Fz``.
* Grip multipliers: load sensitivity, tread temperature, wear, inflation
  pressure, surface and water film (including approximate dynamic
  aquaplaning based on Horne & Dreher, NASA TN D-2056, 1963).
"""

import math

from .constants import KPA_PER_PSI, MPS_PER_KNOT
from .mathutil import smoothstep

KELVIN = 273.15
REFERENCE_WIDTH = 0.355  # m; compound fz_nominal refers to a tyre of this width
PRESSURE_REFERENCE_TEMP = 80.0  # degC: cold pressure is set on the blanket at this temperature
ATMOSPHERE_KPA = 101.325
INITIAL_TYRE_TEMPERATURE = 80.0  # degC (tyre blankets)

# Thermal network (per tyre). Calibrated so slicks settle near their window at
# race pace on the reference circuits; NOT fitted to telemetry.
SURFACE_HEAT_CAPACITY = 2500.0  # J/K
CORE_HEAT_CAPACITY = 14000.0  # J/K
SURFACE_CORE_CONDUCTANCE = 120.0  # W/K
SURFACE_CONVECTION = (15.0, 0.9)  # W/K = a + b * speed
CORE_CONVECTION = (5.0, 0.3)  # W/K = a + b * speed
ROAD_CONDUCTANCE = 60.0  # W/K between tread and road surface
WATER_COOLING = 220.0  # W/K extra surface cooling from a water film (saturating with depth)
# Carcass hysteresis heat relative to rolling-resistance power. The part above
# 1 stands for the cyclic carcass flex under cornering/traction loads that C_rr
# does not cover; it fades out as the core passes its working window because
# the rubber loss factor (tan delta) falls with temperature above the glass
# transition. The rolling-resistance power itself (energy really taken from
# the car) always heats the carcass. Calibrated, not fitted to telemetry.
ROLLING_HEAT_FACTOR = 3.0

# Horne & Dreher: V_p [knots] = 9 sqrt(p [psi]); converted to m/s and kPa.
HORNE_COEFFICIENT = 9.0 * MPS_PER_KNOT / math.sqrt(KPA_PER_PSI)  # ~1.763 m/s per sqrt(kPa)


def aquaplaning_speed(pressure_kpa):
    """Dynamic hydroplaning onset speed (m/s) for an inflation pressure (gauge kPa)."""
    return HORNE_COEFFICIENT * math.sqrt(max(pressure_kpa, 1.0))


def hot_pressure(cold_pressure_kpa, core_temperature_c):
    """Gauge pressure after heating, ideal gas at constant volume (absolute pressures)."""
    absolute = (cold_pressure_kpa + ATMOSPHERE_KPA) * (core_temperature_c + KELVIN) / (
        PRESSURE_REFERENCE_TEMP + KELVIN
    )
    return absolute - ATMOSPHERE_KPA


class TyreModel:
    """Pre-computed constants for one compound (shared by all wheels using it)."""

    __slots__ = (
        "compound",
        "b_lat",
        "c_lat",
        "b_long",
        "c_long",
        "kappa_peak",
        "alpha_peak",
        "inv_kappa_peak",
        "inv_alpha_peak",
    )

    def __init__(self, compound):
        self.compound = compound
        self.c_lat = compound.shape_lateral
        self.c_long = compound.shape_longitudinal
        self.b_lat = math.tan(math.pi / (2.0 * self.c_lat))
        self.b_long = math.tan(math.pi / (2.0 * self.c_long))
        self.kappa_peak = compound.kappa_peak
        self.alpha_peak = compound.alpha_peak
        self.inv_kappa_peak = 1.0 / compound.kappa_peak
        self.inv_alpha_peak = 1.0 / compound.alpha_peak

    def shape(self, s, weight_long):
        """Normalised Magic Formula; ``weight_long`` blends the two shape factors."""
        c = self.c_lat + (self.c_long - self.c_lat) * weight_long
        b = self.b_lat + (self.b_long - self.b_lat) * weight_long
        return math.sin(c * math.atan(b * s))

    def peak_mu(self, fz, width=REFERENCE_WIDTH):
        """Load-sensitive peak friction coefficient (dimensionless).

        The nominal load scales with tread width: a wider tyre spreads the
        same load over a larger contact patch, so it is less load sensitive.
        """
        c = self.compound
        fz0 = c.fz_nominal * width / REFERENCE_WIDTH
        dfz = (fz - fz0) / fz0
        factor = 1.0 - c.load_sensitivity * dfz
        if factor < 0.55:
            factor = 0.55
        elif factor > 1.25:
            factor = 1.25
        return c.mu_peak * factor

    def temperature_factor(self, surface_t, core_t):
        c = self.compound
        t_eff = 0.65 * surface_t + 0.35 * core_t
        x = (t_eff - c.temp_optimal) / c.temp_window
        factor = 1.0 - c.temp_grip_loss * x * x
        return 0.55 if factor < 0.55 else factor

    def wear_factor(self, wear):
        c = self.compound
        factor = 1.0 - c.wear_grip_loss * wear
        if wear > c.wear_cliff:
            factor -= 1.2 * (wear - c.wear_cliff)
        return 0.35 if factor < 0.35 else factor

    def pressure_factor(self, pressure_kpa):
        c = self.compound
        x = (pressure_kpa - c.pressure_optimal) / c.pressure_optimal
        factor = 1.0 - c.pressure_sensitivity * x * x
        return 0.7 if factor < 0.7 else factor

    def water_factor(self, film_mm, texture_drain_mm, speed, tyre_width, pressure_kpa):
        """Grip multiplier from a water film of depth ``film_mm`` at the contact.

        1. A damp/wet surface lowers friction toward ``wet_ratio`` even when the
           tread evacuates all water (thin-film lubrication).
        2. Water the tread grooves and the road macrotexture cannot evacuate
           remains as a residual film ``h_res = h - h_texture - Q / (w V)``.
        3. A residual film causes viscous loss and, above a speed scaled by the
           Horne & Dreher onset speed, near-complete dynamic aquaplaning.
        Returns (factor, residual_film_mm).
        """
        if film_mm <= 0.005:
            return 1.0, 0.0
        c = self.compound
        wet = smoothstep(0.02, 0.25, film_mm)
        film_factor = 1.0 - (1.0 - c.wet_ratio) * wet
        v = speed if speed > 1.0 else 1.0
        groove_mm = 1000.0 * c.drain_capacity / (tyre_width * v)
        residual = film_mm - texture_drain_mm - groove_mm
        if residual <= 0.0:
            return film_factor, 0.0
        r = residual / (residual + 1.0)
        onset = aquaplaning_speed(pressure_kpa)
        speed_term = smoothstep(0.6 * onset, 1.05 * onset, speed)
        aqua = 1.0 - r * (0.15 + 0.80 * speed_term)
        if aqua < 0.08:
            aqua = 0.08
        return film_factor * aqua, residual

    def forces(self, kappa, alpha, fz, mu):
        """Combined-slip forces in the wheel frame.

        ``kappa`` (slip ratio, + = traction), ``alpha`` (slip angle, rad,
        + = contact patch moving to the left of the wheel heading), ``fz``
        (N, >= 0) and ``mu`` the already-scaled peak friction coefficient.
        Returns (Fx, Fy, normalised slip magnitude); Fy opposes alpha.
        """
        if fz <= 0.0:
            return 0.0, 0.0, 0.0
        sx = kappa * self.inv_kappa_peak
        sy = alpha * self.inv_alpha_peak
        s2 = sx * sx + sy * sy
        if s2 < 1e-18:
            return 0.0, 0.0, 0.0
        s = math.sqrt(s2)
        weight_long = sx * sx / s2
        f = mu * fz * self.shape(s, weight_long) / s
        return f * sx, -f * sy, s


def update_thermal(state, index, dt, slide_power, speed, fz, rr, air_t, track_t, water_mm, compound):
    """Two-node (tread surface / carcass core) thermal model, implicit in losses.

    ``state`` holds lists ``surface_t``, ``core_t`` and ``wear``.
    Surface: C_s dT_s = (eta P_slide - h_s(T_s - T_air) - h_r(T_s - T_road)
                         - k_sc(T_s - T_c) - h_w(T_s - T_air)) dt
    Core:    C_c dT_c = (P_hyst + k_sc(T_s - T_c) - h_c(T_c - T_air)) dt
    with P_hyst = C_rr Fz V (1 + (ROLLING_HEAT_FACTOR - 1) f(T_c)), where
    f falls smoothly from 1 at T_opt to 0 at T_opt + window, plus a share of
    the sliding power conducted into the carcass.
    """
    ts = state.surface_t[index]
    tc = state.core_t[index]
    v = speed if speed > 0.0 else -speed
    h_s = SURFACE_CONVECTION[0] + SURFACE_CONVECTION[1] * v
    h_w = WATER_COOLING * (water_mm / (water_mm + 0.5)) if water_mm > 0.0 else 0.0
    h_c = CORE_CONVECTION[0] + CORE_CONVECTION[1] * v
    k = SURFACE_CORE_CONDUCTANCE
    heat_s = compound.heat_fraction * slide_power
    flex = 1.0 - smoothstep(compound.temp_optimal, compound.temp_optimal + compound.temp_window, tc)
    heat_c = rr * fz * v * (1.0 + (ROLLING_HEAT_FACTOR - 1.0) * flex) + (1.0 - compound.heat_fraction) * 0.25 * slide_power
    # Backward Euler on the linear loss terms (unconditionally stable).
    a_s = dt / SURFACE_HEAT_CAPACITY
    a_c = dt / CORE_HEAT_CAPACITY
    ts_new = (ts + a_s * (heat_s + (h_s + h_w) * air_t + ROAD_CONDUCTANCE * track_t + k * tc)) / (
        1.0 + a_s * (h_s + h_w + ROAD_CONDUCTANCE + k)
    )
    tc_new = (tc + a_c * (heat_c + h_c * air_t + k * ts_new)) / (1.0 + a_c * (h_c + k))
    state.surface_t[index] = ts_new
    state.core_t[index] = tc_new


def wear_increment(compound, slide_energy_j, surface_t, roughness_mm, dry):
    """Fractional tread loss for a sliding energy increment (J)."""
    over = surface_t - (compound.temp_optimal + compound.temp_window)
    thermal = 1.0 + (0.04 * over if over > 0.0 else 0.0)
    abrasion = 0.75 + 0.35 * roughness_mm
    multiplier = compound.dry_wear_multiplier if dry else 1.0
    return compound.wear_rate * 1e-6 * slide_energy_j * thermal * abrasion * multiplier

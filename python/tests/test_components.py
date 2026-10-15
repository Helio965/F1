"""Component-level physics tests (verification-heavy).

Each component of :mod:`physics_engine` is exercised in isolation against the
equation documented in its module docstring, an analytic hand calculation or a
self-consistency property (conservation, monotonicity, symmetry). A few
``validation`` tests compare component outputs with public reference values;
those bands are plausibility ranges, not calibration targets.

Tolerances: closed-form checks use relative tolerances of 1e-9..1e-12 (pure
floating-point round-off); steady states reached by time integration use
looser tolerances justified next to each assertion.
"""

import math
from dataclasses import replace

import pytest

from conftest import oval_document
from physics_engine.aero import AeroModel, wake_factors
from physics_engine.brakes import pad_factor, update_disc_temperature, wheel_brake_torques
from physics_engine.constants import GRAVITY, KPA_PER_PSI, MPS_PER_KNOT
from physics_engine.environment import (
    Atmosphere,
    air_density,
    pressure_at_altitude,
    saturation_vapour_pressure,
)
from physics_engine.powertrain import RPM_PER_RAD_S, Powertrain, differential_split
from physics_engine.simulation import Environment
from physics_engine.suspension import Suspension
from physics_engine.track import TrackModel
from physics_engine.tyre import (
    PRESSURE_REFERENCE_TEMP,
    REFERENCE_WIDTH,
    TyreModel,
    aquaplaning_speed,
    hot_pressure,
)
from physics_engine.vehicle import Vehicle
from physics_engine.vehicles import COMPOUNDS, VEHICLES
from track_analyzer import analyze

GE = VEHICLES["ground-effect-2022"]
DT = 1.0 / 120.0
FL, FR, RL, RR = 0, 1, 2, 3
DRY_COMPOUNDS = ("soft", "medium", "hard", "grooved", "crossply-slick")


def rel(a, b):
    return abs(a - b) / max(abs(b), 1e-300)


# --------------------------------------------------------------------------
# Atmosphere
# --------------------------------------------------------------------------


@pytest.mark.verification
def test_air_density_isa_sea_level():
    """ISA sea level: 15 degC, 101325 Pa, dry air -> 1.225 kg/m^3 (within 0.3 %)."""
    rho = air_density(15.0, 101325.0, 0.0)
    assert rel(rho, 1.225) < 0.003
    # The ideal-gas law with R_d = 287.05 J/(kg K) is what the module documents.
    assert rho == pytest.approx(101325.0 / (287.05 * 288.15), rel=1e-12)


@pytest.mark.verification
def test_isa_pressure_and_density_at_altitude():
    """ISA troposphere at 1000 m and 2000 m (U.S. Standard Atmosphere 1976 table).

    The tabulated values are p = 89874.6 Pa / rho = 1.1117 kg/m^3 at 1000 m and
    p = 79495.2 Pa / rho = 1.0066 kg/m^3 at 2000 m; the barometric formula with
    the module constants reproduces them within 0.05 % (constant rounding).
    """
    for h, p_ref, rho_ref in ((1000.0, 89874.6, 1.1117), (2000.0, 79495.2, 1.0066)):
        p = pressure_at_altitude(h)
        t_isa = 15.0 - 0.0065 * h
        assert rel(p, p_ref) < 5e-4
        assert rel(air_density(t_isa, p, 0.0), rho_ref) < 5e-4
    assert pressure_at_altitude(0.0) == pytest.approx(101325.0, rel=1e-12)


@pytest.mark.verification
def test_air_density_falls_with_altitude_temperature_and_humidity():
    """rho decreases with altitude (lower p), temperature (1/T) and humidity
    (water vapour, M = 18 g/mol, replaces dry air, M = 29 g/mol)."""
    altitudes = [0.0, 500.0, 1000.0, 2240.0, 3000.0]
    rhos = [air_density(15.0, pressure_at_altitude(h), 0.5) for h in altitudes]
    assert all(b < a for a, b in zip(rhos, rhos[1:]))

    temps = [-5.0, 10.0, 20.0, 30.0, 45.0]
    rhos = [air_density(t, 101325.0, 0.5) for t in temps]
    assert all(b < a for a, b in zip(rhos, rhos[1:]))

    humid = [air_density(30.0, 101325.0, rh) for rh in (0.0, 0.25, 0.5, 0.75, 1.0)]
    assert all(b < a for a, b in zip(humid, humid[1:]))

    # The Atmosphere object uses the same chain (altitude -> pressure -> density).
    low = Atmosphere("sun", altitude=0.0)
    high = Atmosphere("sun", altitude=2240.0)
    assert high.pressure < low.pressure
    assert high.density < low.density
    assert low.density == pytest.approx(
        air_density(low.air_temperature, 101325.0, low.relative_humidity), rel=1e-12
    )


@pytest.mark.validation
def test_saturation_vapour_pressure_matches_steam_tables():
    """Magnus-Tetens p_sat against IAPWS-95 / IAPWS-IF97 saturation pressures.

    Reference values (Wagner & Pruss, J. Phys. Chem. Ref. Data 31, 2002):
    10 degC 1228.1 Pa, 20 degC 2339.3 Pa, 30 degC 4247.0 Pa, 40 degC 7384.9 Pa.
    Alduchov & Eskridge (1996) report < 0.4 % error for these coefficients over
    -40..50 degC; the 0.5 % band is a plausibility range, not calibration.
    """
    for t, p_ref in ((10.0, 1228.1), (20.0, 2339.3), (30.0, 4247.0), (40.0, 7384.9)):
        assert rel(saturation_vapour_pressure(t), p_ref) < 0.005


@pytest.mark.validation
def test_humid_air_density_plausible():
    """Saturated air at 30 degC and 101325 Pa.

    The CIPM-2007 air-density equation (Picard et al., Metrologia 45, 2008)
    gives about 1.146 kg/m^3 for these conditions (dry air at 20 degC gives
    1.2041 kg/m^3). A 0.5 % band is a plausibility range, not calibration; the
    engine ignores the compressibility factor and the enhancement factor.
    """
    assert rel(air_density(30.0, 101325.0, 1.0), 1.146) < 0.005
    assert rel(air_density(20.0, 101325.0, 0.0), 1.2041) < 0.001


# --------------------------------------------------------------------------
# Tyre forces
# --------------------------------------------------------------------------


@pytest.mark.verification
@pytest.mark.parametrize("compound_id", sorted(COMPOUNDS))
def test_tyre_force_never_exceeds_friction_limit(compound_id):
    """|F| <= mu Fz for every combined slip state (friction ellipse bound)."""
    tyre = TyreModel(COMPOUNDS[compound_id])
    fz, mu = 4200.0, 1.6
    limit = mu * fz
    for i in range(-40, 41):
        kappa = i * 0.025  # -1 .. 1 (locked wheel to 100 % wheelspin)
        for j in range(-40, 41):
            alpha = j * 0.015  # about -34 .. 34 deg
            fx, fy, s = tyre.forces(kappa, alpha, fz, mu)
            assert math.hypot(fx, fy) <= limit * (1.0 + 1e-12)
            # Combined slip: the force is aligned with the normalised slip vector
            # (sx, -sy), so |F| / (mu Fz) is the shape function at s.
            if s > 0.0:
                sx = kappa / tyre.kappa_peak
                sy = alpha / tyre.alpha_peak
                assert fx * sy == pytest.approx(-fy * sx, abs=1e-6 * limit)
    # No load, no force.
    assert tyre.forces(0.1, 0.1, 0.0, mu) == (0.0, 0.0, 0.0)
    assert tyre.forces(0.1, 0.1, -50.0, mu) == (0.0, 0.0, 0.0)


@pytest.mark.verification
@pytest.mark.parametrize("compound_id", sorted(COMPOUNDS))
def test_normalised_magic_formula_peaks_at_unit_slip(compound_id):
    """B = tan(pi / 2C) puts the Magic Formula peak exactly at s = 1.

    Pure slip uses one (B, C) pair, so the scan maximum is at s = 1 to within
    the 0.001 grid step. In combined slip the module blends B and C linearly,
    which moves the analytic peak by < 2 % (a 3 % band is used); the peak value
    still equals mu Fz.
    """
    tyre = TyreModel(COMPOUNDS[compound_id])
    assert tyre.b_lat == pytest.approx(math.tan(math.pi / (2.0 * tyre.c_lat)), rel=1e-12)
    assert tyre.b_long == pytest.approx(math.tan(math.pi / (2.0 * tyre.c_long)), rel=1e-12)
    fz, mu = 4000.0, 1.7
    for direction, band in ((0.0, 0.0015), (math.pi / 2, 0.0015), (math.pi / 4, 0.03), (0.3, 0.03)):
        best_s, best_f = 0.0, 0.0
        for k in range(1, 4001):
            s = k * 0.001
            kappa = s * math.cos(direction) * tyre.kappa_peak
            alpha = s * math.sin(direction) * tyre.alpha_peak
            fx, fy, s_out = tyre.forces(kappa, alpha, fz, mu)
            assert s_out == pytest.approx(s, rel=1e-9)
            f = math.hypot(fx, fy)
            if f > best_f:
                best_s, best_f = s, f
        assert abs(best_s - 1.0) <= band
        assert best_f == pytest.approx(mu * fz, rel=1e-6)


@pytest.mark.verification
@pytest.mark.parametrize("compound_id", ["soft", "medium", "intermediate", "wet"])
def test_pure_lateral_peak_and_sign_conventions(compound_id):
    """Pure slip at the peak gives mu Fz; Fy opposes alpha, Fx follows kappa."""
    tyre = TyreModel(COMPOUNDS[compound_id])
    fz, mu = 3800.0, 1.65
    fx, fy, s = tyre.forces(0.0, tyre.alpha_peak, fz, mu)
    assert fx == 0.0
    assert fy == pytest.approx(-mu * fz, rel=1e-12)
    assert s == pytest.approx(1.0, rel=1e-12)
    fx, fy, s = tyre.forces(-tyre.kappa_peak, 0.0, fz, mu)
    assert fx == pytest.approx(-mu * fz, rel=1e-12)
    assert fy == 0.0
    for alpha in (-0.3, -0.05, -0.001, 0.001, 0.05, 0.3):
        _, fy, _ = tyre.forces(0.0, alpha, fz, mu)
        assert fy * alpha < 0.0
    for kappa in (-0.8, -0.05, -0.001, 0.001, 0.05, 0.8):
        fx, _, _ = tyre.forces(kappa, 0.02, fz, mu)
        assert fx * kappa > 0.0
    # Odd symmetry of the force components.
    fx1, fy1, _ = tyre.forces(0.07, 0.04, fz, mu)
    fx2, fy2, _ = tyre.forces(-0.07, -0.04, fz, mu)
    assert fx2 == pytest.approx(-fx1, rel=1e-12)
    assert fy2 == pytest.approx(-fy1, rel=1e-12)


@pytest.mark.verification
@pytest.mark.parametrize("compound_id", sorted(COMPOUNDS))
def test_longitudinal_slip_reduces_lateral_force(compound_id):
    """Friction ellipse: at fixed alpha, |Fy| falls strictly as |kappa| grows."""
    tyre = TyreModel(COMPOUNDS[compound_id])
    fz, mu = 4000.0, 1.7
    for alpha_scale in (0.25, 0.5, 1.0, 2.0):
        alpha = alpha_scale * tyre.alpha_peak
        _, fy0, _ = tyre.forces(0.0, alpha, fz, mu)
        for sign in (1.0, -1.0):
            prev = abs(fy0)
            for k in range(1, 301):
                kappa = sign * k * 0.01 * tyre.kappa_peak
                _, fy, _ = tyre.forces(kappa, alpha, fz, mu)
                assert abs(fy) < prev
                prev = abs(fy)


@pytest.mark.verification
@pytest.mark.parametrize("compound_id", sorted(COMPOUNDS))
def test_load_sensitivity(compound_id):
    """mu = mu_peak (1 - k (Fz - Fz0) / Fz0) with Fz0 proportional to tread width."""
    c = COMPOUNDS[compound_id]
    tyre = TyreModel(c)
    narrow, wide = 0.305, 0.405
    loads = [500.0 * i for i in range(1, 19)]  # 0.5 .. 9 kN, inside the clamp range
    for width in (narrow, wide):
        mus = [tyre.peak_mu(fz, width) for fz in loads]
        assert all(b < a for a, b in zip(mus, mus[1:]))
        # Force capacity still grows with load but less than proportionally.
        caps = [m * fz for m, fz in zip(mus, loads)]
        assert all(b > a for a, b in zip(caps, caps[1:]))
        assert tyre.peak_mu(8000.0, width) * 8000.0 < 2.0 * tyre.peak_mu(4000.0, width) * 4000.0
        fz0 = c.fz_nominal * width / REFERENCE_WIDTH
        assert tyre.peak_mu(fz0, width) == pytest.approx(c.mu_peak, rel=1e-12)
    # Default width is the reference width.
    assert tyre.peak_mu(c.fz_nominal) == pytest.approx(c.mu_peak, rel=1e-12)
    # Wider tyre: less sensitive. The slope ratio equals the width ratio.
    slope_n = (tyre.peak_mu(6000.0, narrow) - tyre.peak_mu(5000.0, narrow)) / 1000.0
    slope_w = (tyre.peak_mu(6000.0, wide) - tyre.peak_mu(5000.0, wide)) / 1000.0
    assert slope_n < slope_w < 0.0
    assert slope_w / slope_n == pytest.approx(narrow / wide, rel=1e-9)
    assert tyre.peak_mu(6000.0, wide) > tyre.peak_mu(6000.0, narrow)
    # Documented clamps.
    assert tyre.peak_mu(1e6, narrow) == pytest.approx(0.55 * c.mu_peak, rel=1e-12)


@pytest.mark.verification
@pytest.mark.parametrize("compound_id", sorted(COMPOUNDS))
def test_temperature_factor_peaks_at_optimum(compound_id):
    """f_T = 1 - loss x^2, x = (0.65 Ts + 0.35 Tc - T_opt) / window (floor 0.55)."""
    c = COMPOUNDS[compound_id]
    tyre = TyreModel(c)
    temps = [c.temp_optimal + 0.5 * k for k in range(-120, 121)]
    factors = [tyre.temperature_factor(t, t) for t in temps]
    best = temps[factors.index(max(factors))]
    assert best == pytest.approx(c.temp_optimal, abs=1e-9)
    assert tyre.temperature_factor(c.temp_optimal, c.temp_optimal) == pytest.approx(1.0, abs=1e-12)
    for sign in (1.0, -1.0):
        t = c.temp_optimal + sign * c.temp_window
        assert tyre.temperature_factor(t, t) == pytest.approx(1.0 - c.temp_grip_loss, rel=1e-12)
    # Symmetric about the optimum and weighted 65/35 surface/core.
    assert tyre.temperature_factor(c.temp_optimal + 17.0, c.temp_optimal + 17.0) == pytest.approx(
        tyre.temperature_factor(c.temp_optimal - 17.0, c.temp_optimal - 17.0), rel=1e-12
    )
    t_eff_equal = tyre.temperature_factor(c.temp_optimal + 0.35 * 20.0, c.temp_optimal - 0.65 * 20.0)
    assert t_eff_equal == pytest.approx(1.0, abs=1e-12)
    assert tyre.temperature_factor(-50.0, -50.0) == 0.55


@pytest.mark.verification
@pytest.mark.parametrize("compound_id", sorted(COMPOUNDS))
def test_pressure_factor_peaks_at_optimum(compound_id):
    """f_p = 1 - k ((p - p_opt) / p_opt)^2 with a 0.7 floor."""
    c = COMPOUNDS[compound_id]
    tyre = TyreModel(c)
    assert tyre.pressure_factor(c.pressure_optimal) == pytest.approx(1.0, abs=1e-12)
    lo = tyre.pressure_factor(0.8 * c.pressure_optimal)
    hi = tyre.pressure_factor(1.2 * c.pressure_optimal)
    assert lo == pytest.approx(hi, rel=1e-12)
    assert lo == pytest.approx(1.0 - c.pressure_sensitivity * 0.04, rel=1e-12)
    assert tyre.pressure_factor(10.0 * c.pressure_optimal) == 0.7


@pytest.mark.verification
@pytest.mark.parametrize("compound_id", sorted(COMPOUNDS))
def test_wear_factor_decreases_with_cliff(compound_id):
    """Linear grip loss with wear, steeper by 1.2 per unit wear past the cliff."""
    c = COMPOUNDS[compound_id]
    tyre = TyreModel(c)
    wears = [0.01 * k for k in range(101)]
    f = [tyre.wear_factor(w) for w in wears]
    assert f[0] == pytest.approx(1.0, abs=1e-12)
    assert all(b <= a for a, b in zip(f, f[1:]))
    h = 1e-4
    w_before = 0.5 * c.wear_cliff
    w_after = c.wear_cliff + 0.05
    slope_before = (tyre.wear_factor(w_before + h) - tyre.wear_factor(w_before - h)) / (2 * h)
    slope_after = (tyre.wear_factor(w_after + h) - tyre.wear_factor(w_after - h)) / (2 * h)
    assert slope_before == pytest.approx(-c.wear_grip_loss, rel=1e-6)
    assert slope_after == pytest.approx(-(c.wear_grip_loss + 1.2), rel=1e-6)
    # Continuous at the cliff, floored at 0.35.
    assert tyre.wear_factor(c.wear_cliff + 1e-9) == pytest.approx(tyre.wear_factor(c.wear_cliff), abs=1e-8)
    assert tyre.wear_factor(5.0) == 0.35


@pytest.mark.verification
def test_hot_pressure_is_ideal_gas_on_absolute_pressure():
    """p_abs,hot / p_abs,cold = T_hot / T_ref (kelvin) at constant volume.

    Hand check: 150 kPa gauge at 80 degC heated to 110 degC
    -> (150 + 101.325) * 383.15 / 353.15 - 101.325 = 171.350 kPa. Applying the
    ratio to gauge pressure would give 162.74 kPa instead.
    """
    assert hot_pressure(150.0, 110.0) == pytest.approx(171.350, abs=1e-3)
    assert hot_pressure(150.0, PRESSURE_REFERENCE_TEMP) == pytest.approx(150.0, abs=1e-12)
    for cold in (120.0, 140.0, 165.0):
        for core in (40.0, 80.0, 105.0, 130.0):
            ratio = (hot_pressure(cold, core) + 101.325) / (cold + 101.325)
            assert ratio == pytest.approx((core + 273.15) / (PRESSURE_REFERENCE_TEMP + 273.15), rel=1e-12)
    assert hot_pressure(150.0, 60.0) < 150.0 < hot_pressure(150.0, 100.0)


@pytest.mark.verification
def test_aquaplaning_speed_horne_dreher():
    """Horne & Dreher (NASA TN D-2056, 1963): V_p [kt] = 9 sqrt(p [psi]).

    Hand check at 30 psi: 9 * sqrt(30) = 49.295 kt = 25.360 m/s
    (1 kt = 0.514444 m/s); 30 psi = 206.843 kPa.
    """
    p_kpa = 30.0 * KPA_PER_PSI
    assert p_kpa == pytest.approx(206.843, abs=1e-3)
    assert aquaplaning_speed(p_kpa) == pytest.approx(25.360, abs=1e-3)
    for psi in (15.0, 22.0, 35.0):
        expected = 9.0 * math.sqrt(psi) * MPS_PER_KNOT
        assert aquaplaning_speed(psi * KPA_PER_PSI) == pytest.approx(expected, rel=1e-9)
    # Square-root law: four times the pressure doubles the onset speed.
    assert aquaplaning_speed(4.0 * 150.0) == pytest.approx(2.0 * aquaplaning_speed(150.0), rel=1e-12)


@pytest.mark.verification
@pytest.mark.parametrize("compound_id", ["medium", "intermediate", "wet"])
def test_water_factor_dry_and_monotonic_in_film(compound_id):
    """Dry -> exactly 1; deeper water -> never more grip."""
    tyre = TyreModel(COMPOUNDS[compound_id])
    assert tyre.water_factor(0.0, 0.3, 50.0, 0.305, 165.0) == (1.0, 0.0)
    assert tyre.water_factor(0.004, 0.3, 50.0, 0.305, 165.0) == (1.0, 0.0)
    for speed in (10.0, 30.0, 50.0, 70.0, 90.0):
        films = [0.01 * k for k in range(1, 601)]  # 0.01 .. 6 mm
        factors = [tyre.water_factor(h, 0.3, speed, 0.305, 165.0)[0] for h in films]
        assert all(b <= a + 1e-12 for a, b in zip(factors, factors[1:]))
        # Below the 0.02 mm damp threshold of the smoothstep the grip is intact.
        assert factors[0] == 1.0
        assert factors[-1] < 1.0
        assert all(f >= 0.08 * tyre.compound.wet_ratio - 1e-12 for f in factors)


@pytest.mark.verification
def test_wet_compound_retains_more_grip_than_slick():
    """Same film, speed, width and pressure: wet >= intermediate >= slick, with a
    strict wet > slick advantage once the surface is wet (film >= 0.25 mm)."""
    wet = TyreModel(COMPOUNDS["wet"])
    inter = TyreModel(COMPOUNDS["intermediate"])
    slick = TyreModel(COMPOUNDS["medium"])
    for film in (0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0):
        for speed in (15.0, 30.0, 50.0, 70.0, 90.0):
            args = (film, 0.3, speed, 0.305, 160.0)
            f_wet = wet.water_factor(*args)[0]
            f_int = inter.water_factor(*args)[0]
            f_slk = slick.water_factor(*args)[0]
            assert f_wet >= f_int >= f_slk
            if film >= 0.25:
                assert f_wet > f_slk


@pytest.mark.verification
def test_residual_film_grows_with_speed():
    """h_res = h - h_texture - Q / (w V): less time to evacuate water at speed."""
    tyre = TyreModel(COMPOUNDS["intermediate"])
    film, texture, width = 3.0, 0.3, 0.305
    speeds = [10.0 + 2.0 * k for k in range(46)]  # 10 .. 100 m/s
    residuals = [tyre.water_factor(film, texture, v, width, 160.0)[1] for v in speeds]
    assert all(b >= a for a, b in zip(residuals, residuals[1:]))
    positive = [r for r in residuals if r > 0.0]
    assert len(positive) > 10
    assert all(b > a for a, b in zip(positive, positive[1:]))
    for v, r in zip(speeds, residuals):
        expected = film - texture - 1000.0 * tyre.compound.drain_capacity / (width * v)
        assert r == pytest.approx(max(expected, 0.0), abs=1e-12)
    # A slick has no grooves: its residual film does not depend on speed.
    slick = TyreModel(COMPOUNDS["medium"])
    assert {slick.water_factor(film, texture, v, width, 160.0)[1] for v in speeds} == {film - texture}


@pytest.mark.verification
def test_aquaplaning_term_switches_on_above_onset_speed():
    """Residual-film loss is 0.15 r below 0.6 V_p and 0.95 r above 1.05 V_p,
    with r = h_res / (h_res + 1 mm) (documented smoothstep blend)."""
    slick = TyreModel(COMPOUNDS["medium"])
    film, texture, pressure = 2.0, 0.5, 165.0
    v_p = aquaplaning_speed(pressure)
    residual = film - texture
    r = residual / (residual + 1.0)
    wet_film_factor = slick.compound.wet_ratio  # film >= 0.25 mm: fully wet
    slow, res_slow = slick.water_factor(film, texture, 0.5 * v_p, 0.305, pressure)
    fast, res_fast = slick.water_factor(film, texture, 1.1 * v_p, 0.305, pressure)
    assert res_slow == res_fast == pytest.approx(residual, abs=1e-12)
    assert slow == pytest.approx(wet_film_factor * (1.0 - 0.15 * r), rel=1e-12)
    assert fast == pytest.approx(wet_film_factor * max(0.08, 1.0 - 0.95 * r), rel=1e-12)
    assert fast < slow


@pytest.mark.validation
def test_tyre_parameters_in_public_envelope():
    """Dry racing slicks: peak mu ~1.4-2.0, peak slip angle ~3-10 deg, peak slip
    ratio ~0.05-0.20 (Milliken & Milliken, Race Car Vehicle Dynamics, 1995, ch. 2;
    Pacejka, Tyre and Vehicle Dynamics, 3rd ed., 2012). Treaded wet tyres grip
    less than slicks in the dry. Plausibility ranges, not calibration."""
    for cid in DRY_COMPOUNDS:
        c = COMPOUNDS[cid]
        assert 1.4 <= c.mu_peak <= 2.0
        assert 3.0 <= math.degrees(c.alpha_peak) <= 10.0
        assert 0.05 <= c.kappa_peak <= 0.20
        # Post-peak fall-off: sliding force sin(C pi / 2) keeps 70..95 % of the peak.
        tyre = TyreModel(c)
        _, fy, _ = tyre.forces(0.0, 6.0 * c.alpha_peak, 4000.0, 1.0)
        assert 0.70 <= abs(fy) / 4000.0 <= 0.95
    for cid in ("intermediate", "wet"):
        assert COMPOUNDS[cid].mu_peak < min(COMPOUNDS[d].mu_peak for d in ("soft", "medium", "hard"))


# --------------------------------------------------------------------------
# Aerodynamics
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def straight_track():
    """Long stadium oval; s = 300 m lies on the first straight."""
    analysis = analyze(oval_document(), "quick")
    return TrackModel(analysis["physics_table"])


@pytest.mark.verification
def test_vehicle_aero_forces_follow_dynamic_pressure(straight_track):
    """F_drag = 0.5 rho CdA V^2 and F_down = 0.5 rho ClA V^2 inside Vehicle.step.

    With zero wind the airspeed is the car speed; the coefficients are those of
    the ride heights at the start of the step (fresh cars at both speeds share
    them), so doubling the speed must quadruple both forces exactly.
    """
    atm = Atmosphere("sun", overrides={"wind_speed": 0.0})
    env = Environment(atm)
    results = {}
    for speed in (40.0, 80.0):
        car = Vehicle(GE, straight_track, compound="medium", fuel=50.0)
        car.place(300.0, 0.0, speed)
        sus = car.suspension
        cla_f, cla_r, cda = car.aero.coefficients(sus.ride_front, sus.ride_rear, False, 0.0, 1.0, 1.0)
        car.step(DT, 0.0, 0.0, 0.0, False, env)
        q = 0.5 * atm.density * speed * speed
        assert car.drag == pytest.approx(q * cda, rel=1e-12)
        assert car.downforce == pytest.approx(q * (cla_f + cla_r), rel=1e-12)
        results[speed] = (car.drag, car.downforce)
    assert results[80.0][0] / results[40.0][0] == pytest.approx(4.0, rel=1e-12)
    assert results[80.0][1] / results[40.0][1] == pytest.approx(4.0, rel=1e-12)


@pytest.mark.verification
def test_drs_lowers_drag_and_rear_downforce_only():
    model = AeroModel(GE, 0.5)
    closed = model.coefficients(0.03, 0.07, False, 0.0, 1.0, 1.0)
    opened = model.coefficients(0.03, 0.07, True, 0.0, 1.0, 1.0)
    assert opened[0] == closed[0]
    assert opened[1] == pytest.approx(closed[1] * GE.drs_cla_rear_factor, rel=1e-12)
    assert opened[2] == pytest.approx(closed[2] * GE.drs_cda_factor, rel=1e-12)
    assert opened[1] < closed[1] and opened[2] < closed[2]
    # A car without DRS ignores the request.
    v10 = AeroModel(VEHICLES["v10-2005"], 0.5)
    assert v10.coefficients(0.03, 0.06, True, 0.0, 1.0, 1.0) == v10.coefficients(0.03, 0.06, False, 0.0, 1.0, 1.0)


@pytest.mark.verification
@pytest.mark.parametrize("vehicle_id", sorted(VEHICLES))
def test_ground_effect_rises_until_stall(vehicle_id):
    """ClA(h) / ClA(h_ref) = 1 + k_ge (h_ref - h) / h_ref above the stall height;
    below it the underbody stalls (smoothstep loss up to ``stall_loss`` at h = 0).

    The smoothstep loss has zero slope at h = stall_height, so the analytic
    maximum sits slightly below it (about 0.7 mm for the 2022 car); a 2 mm band
    is allowed for the argmax.
    """
    p = VEHICLES[vehicle_id]
    model = AeroModel(p, 0.5)

    def total(h):
        f, r, _ = model.coefficients(h, h, False, 0.0, 1.0, 1.0)
        return f + r

    ref = total(p.ride_height_ref)
    heights = [0.0001 * k for k in range(0, 1201)]  # 0 .. 120 mm
    values = [total(h) for h in heights]
    # The relative height term is clamped to +/-0.8, i.e. ClA is flat outside
    # 0.2 h_ref .. 1.8 h_ref. Inside that band and above the stall height ClA
    # rises strictly as the car gets closer to the ground; outside it never falls.
    h_lo = max(p.stall_height, 0.2 * p.ride_height_ref)
    h_hi = 1.8 * p.ride_height_ref
    above = [(h, v) for h, v in zip(heights, values) if h >= p.stall_height]
    for (h1, v1), (h2, v2) in zip(above, above[1:]):
        if h1 >= h_lo and h2 <= h_hi:
            assert v1 > v2
        else:
            assert v1 >= v2 - 1e-12
    for h, v in above:
        x = max(-0.8, min(0.8, (p.ride_height_ref - h) / p.ride_height_ref))
        assert v / ref == pytest.approx(1.0 + p.ground_effect * x, rel=1e-12)
    # Lowest height that reaches the maximum.
    h_best = heights[values.index(max(values))]
    assert p.stall_height - 0.002 <= h_best <= h_lo
    # Full stall at zero ride height loses ``stall_loss`` of the ground-effect level.
    assert total(0.0) < max(values)
    assert total(0.0) / ref == pytest.approx((1.0 + 0.8 * p.ground_effect) * (1.0 - p.stall_loss), rel=1e-12)


@pytest.mark.verification
def test_wing_level_is_monotonic():
    """Wing level maps linearly onto wing_cla_range / wing_cda_range."""
    levels = [0.1 * k for k in range(11)]
    coeffs = []
    for level in levels:
        model = AeroModel(GE, level)
        f, r, cd = model.coefficients(0.05, 0.05, False, 0.0, 1.0, 1.0)
        coeffs.append((f + r, cd))
    assert all(b[0] > a[0] and b[1] > a[1] for a, b in zip(coeffs, coeffs[1:]))
    lo = AeroModel(GE, 0.0)
    hi = AeroModel(GE, 1.0)
    assert (lo.wing_cla, lo.wing_cda) == pytest.approx((GE.wing_cla_range[0], GE.wing_cda_range[0]))
    assert (hi.wing_cla, hi.wing_cda) == pytest.approx((GE.wing_cla_range[1], GE.wing_cda_range[1]))
    clamped = AeroModel(GE, 1.7)
    assert clamped.wing_cla == pytest.approx(GE.wing_cla_range[1])


@pytest.mark.verification
def test_wake_factors():
    """Slipstream/dirty air: < 1 directly behind, exactly 1 far away or far aside."""
    width = GE.width
    drag, down = wake_factors(5.0, 0.0, width)
    assert drag < 1.0 and down < 1.0
    # Documented exponential decay at zero lateral offset.
    assert drag == pytest.approx(1.0 - 0.32 * math.exp(-5.0 / 22.0), rel=1e-9)
    assert down == pytest.approx(1.0 - 0.28 * math.exp(-5.0 / 30.0), rel=1e-9)
    gaps = [1.0 + k for k in range(79)]
    drags = [wake_factors(g, 0.0, width)[0] for g in gaps]
    assert all(b > a for a, b in zip(drags, drags[1:]))
    assert wake_factors(100.0, 0.0, width) == (1.0, 1.0)
    assert wake_factors(0.3, 0.0, width) == (1.0, 1.0)
    assert wake_factors(10.0, 10.0, width) == (1.0, 1.0)
    # Partial overlap gives a partial effect.
    half = wake_factors(10.0, 1.6, width)
    full = wake_factors(10.0, 0.0, width)
    assert full[0] < half[0] < 1.0


@pytest.mark.validation
@pytest.mark.parametrize("vehicle_id", sorted(VEHICLES))
def test_aero_efficiency_plausible(vehicle_id):
    """Open-wheel racing cars: L/D about 2-5 and downforce about 1.2-4 times the
    weight at 300 km/h (Katz, Race Car Aerodynamics, 1995; Toet, The
    Aeronautical Journal 117, 2013). Static ride heights, mid wing, ISA air.
    Plausibility range, not calibration."""
    p = VEHICLES[vehicle_id]
    model = AeroModel(p, 0.5)
    f, r, cd = model.coefficients(p.ride_height_front, p.ride_height_rear, False, 0.0, 1.0, 1.0)
    assert 2.0 <= (f + r) / cd <= 5.0
    v = 300.0 / 3.6
    downforce = 0.5 * 1.225 * (f + r) * v * v
    weight = (p.mass + 50.0) * GRAVITY
    assert 1.2 <= downforce / weight <= 4.0


# --------------------------------------------------------------------------
# Powertrain
# --------------------------------------------------------------------------


def _axle_omega_for_rpm(pt, rpm):
    return rpm / RPM_PER_RAD_S / pt.overall_ratio()


@pytest.mark.verification
@pytest.mark.parametrize("vehicle_id", sorted(VEHICLES))
def test_rev_limiter_cuts_engine_torque(vehicle_id):
    """At/above the rev limit u is forced to 0: only engine braking remains.

    ERS is disabled here so only the documented engine law is tested.
    """
    p = replace(VEHICLES[vehicle_id], ers_power=0.0)
    gear = 4
    below = Powertrain(p, 50.0)
    below.gear = gear
    ratio = below.overall_ratio()
    rpm = p.rev_limit - 200.0
    t_below = below.axle_torque(1.0, _axle_omega_for_rpm(below, rpm), DT)
    assert below.clutch_locked and not below.limiter
    expected = below.full_torque(rpm) * ratio * p.driveline_efficiency
    assert t_below == pytest.approx(expected, rel=1e-9)
    assert t_below > 0.0

    above = Powertrain(p, 50.0)
    above.gear = gear
    rpm = p.rev_limit + 1.0
    t_above = above.axle_torque(1.0, _axle_omega_for_rpm(above, rpm), DT)
    assert above.limiter
    expected = -above.friction_torque(rpm) * p.engine_brake_factor * ratio
    assert t_above == pytest.approx(expected, rel=1e-9)
    assert t_above < 0.0


@pytest.mark.verification
def test_ers_does_not_deploy_on_the_rev_limiter():
    """The limiter cuts the whole power unit: with ERS fitted and full
    throttle above the rev limit only engine braking remains, and no ERS
    energy is spent."""
    p = GE
    pt = Powertrain(p, 50.0)
    pt.gear = len(p.gear_ratios)
    rpm = p.rev_limit + 100.0
    torque = pt.axle_torque(1.0, _axle_omega_for_rpm(pt, rpm), DT)
    assert pt.limiter and not pt.ers_active
    assert pt.ers_deployed == 0.0
    assert torque == pytest.approx(-pt.friction_torque(rpm) * p.engine_brake_factor * pt.overall_ratio(), rel=1e-9)


@pytest.mark.verification
def test_closed_throttle_engine_braking_scales_with_factor():
    """Closed throttle: T = -T_friction(rpm) * engine_brake_factor * G
    (no driveline efficiency on overrun)."""
    rpm = 8000.0
    torques = {}
    for factor in (0.45, 0.9):
        p = replace(GE, engine_brake_factor=factor)
        pt = Powertrain(p, 50.0)
        pt.gear = 3
        t = pt.axle_torque(0.0, _axle_omega_for_rpm(pt, rpm), DT)
        assert pt.clutch_locked
        friction = p.friction_torque_base + p.friction_torque_per_rpm * rpm
        assert t == pytest.approx(-friction * factor * pt.overall_ratio(), rel=1e-9)
        assert pt.engine_power == 0.0
        torques[factor] = t
    assert torques[0.9] / torques[0.45] == pytest.approx(2.0, rel=1e-12)


@pytest.mark.verification
@pytest.mark.parametrize("vehicle_id", sorted(VEHICLES))
def test_auto_upshift_at_upshift_rpm(vehicle_id):
    """Upshift exactly when the engine reaches upshift_rpm; the shift then cuts
    drive torque for shift_time; no upshift beyond top gear."""
    p = replace(VEHICLES[vehicle_id], ers_power=0.0)
    pt = Powertrain(p, 50.0)
    pt.gear = 3
    pt.auto_shift(_axle_omega_for_rpm(pt, p.upshift_rpm - 20.0), braking=False)
    assert pt.gear == 3 and pt.shift_timer == 0.0
    omega = _axle_omega_for_rpm(pt, p.upshift_rpm + 1.0)
    pt.auto_shift(omega, braking=False)
    assert pt.gear == 4
    assert pt.shift_timer == pytest.approx(p.shift_time)
    # Drive torque is interrupted while the shift is in progress.
    assert pt.axle_torque(1.0, omega, 0.25 * p.shift_time) == 0.0
    # No new shift is requested while the timer runs.
    pt.auto_shift(_axle_omega_for_rpm(pt, p.upshift_rpm + 50.0), braking=False)
    assert pt.gear == 4
    top = Powertrain(p, 50.0)
    top.gear = len(p.gear_ratios)
    top.auto_shift(_axle_omega_for_rpm(top, p.rev_limit), braking=False)
    assert top.gear == len(p.gear_ratios)


@pytest.mark.verification
def test_auto_downshift_below_downshift_rpm():
    """Downshift when rpm < downshift_rpm and the lower gear stays below
    0.94 upshift_rpm; withheld while the driven wheels slide."""
    p = GE
    pt = Powertrain(p, 50.0)
    pt.gear = 5
    omega = _axle_omega_for_rpm(pt, p.downshift_rpm - 300.0)
    lower_rpm = abs(omega * pt.overall_ratio(4)) * RPM_PER_RAD_S
    assert lower_rpm < 0.94 * p.upshift_rpm
    pt.auto_shift(omega, braking=False, rear_sliding=True)
    assert pt.gear == 5
    pt.auto_shift(omega, braking=False)
    assert pt.gear == 4
    stay = Powertrain(p, 50.0)
    stay.gear = 5
    stay.auto_shift(_axle_omega_for_rpm(stay, p.downshift_rpm + 300.0), braking=False)
    assert stay.gear == 5


@pytest.mark.verification
def test_differential_split_conserves_torque_and_bounds_lock():
    """T_L + T_R = T_in and |T_R - T_L| / 2 <= preload + ramp |T_in|; the lock
    moves torque from the faster to the slower wheel."""
    inertia, preload, ramp = 1.6, GE.diff_preload, GE.diff_ramp
    for torque in (-1500.0, -200.0, 0.0, 300.0, 2500.0, 6000.0):
        capacity = preload + ramp * abs(torque)
        for d_omega in (-20.0, -2.0, -0.05, 0.0, 0.05, 2.0, 20.0):
            wl, wr = 150.0 + d_omega, 150.0
            tl, tr = differential_split(torque, wl, wr, inertia, DT, preload, ramp)
            assert tl + tr == pytest.approx(torque, abs=1e-9)
            lock = 0.5 * (tr - tl)
            assert abs(lock) <= capacity + 1e-9
            if d_omega > 0.0:
                assert tl < tr  # left wheel faster -> it receives less torque
            elif d_omega < 0.0:
                assert tl > tr
            else:
                assert tl == tr == pytest.approx(0.5 * torque)
            needed = 0.25 * inertia * d_omega / DT
            assert lock == pytest.approx(max(-capacity, min(capacity, needed)), abs=1e-9)
    # A large speed difference saturates the clutch exactly at its capacity.
    tl, tr = differential_split(2000.0, 200.0, 100.0, inertia, DT, preload, ramp)
    assert 0.5 * (tr - tl) == pytest.approx(preload + ramp * 2000.0, rel=1e-12)


# --------------------------------------------------------------------------
# Brakes
# --------------------------------------------------------------------------


@pytest.mark.verification
@pytest.mark.parametrize("vehicle_id", sorted(VEHICLES))
def test_pad_factor_window(vehicle_id):
    """Full friction inside the window, cold bite below it, fade above it."""
    lo, hi = VEHICLES[vehicle_id].brake_temp_optimal
    temps = [5.0 * k for k in range(0, 401)]  # 0 .. 2000 degC
    f = [pad_factor(t, lo, hi) for t in temps]
    for t, value in zip(temps, f):
        if lo <= t <= hi:
            assert value == 1.0
        else:
            assert 0.5 <= value < 1.0
    cold = [v for t, v in zip(temps, f) if t < lo]
    hot = [v for t, v in zip(temps, f) if t > hi]
    assert all(b > a for a, b in zip(cold, cold[1:]))
    assert all(b <= a for a, b in zip(hot, hot[1:]))
    assert pad_factor(0.0, lo, hi) == pytest.approx(0.55)
    assert pad_factor(-40.0, lo, hi) == pytest.approx(0.55)
    # Continuous at both edges of the window.
    assert pad_factor(lo - 1e-6, lo, hi) == pytest.approx(1.0, abs=1e-6)
    assert pad_factor(hi + 1e-6, lo, hi) == pytest.approx(1.0, abs=1e-6)
    assert pad_factor(hi + 400.0, lo, hi) == pytest.approx(0.5)
    assert pad_factor(hi + 2000.0, lo, hi) == 0.5


@pytest.mark.verification
def test_brake_torque_distribution():
    front, rear = wheel_brake_torques(0.8, 0.57, 15000.0)
    assert front == pytest.approx(0.8 * 0.57 * 15000.0 / 2.0)
    assert rear == pytest.approx(0.8 * 0.43 * 15000.0 / 2.0)
    assert 2.0 * (front + rear) == pytest.approx(0.8 * 15000.0)


@pytest.mark.verification
def test_disc_heats_under_power_and_cools_toward_air():
    """C dT/dt = P - (h0 + h1 V)(T - T_air), backward Euler.

    * Under constant power the disc heats monotonically to T_air + P / h.
    * Without power it cools monotonically to T_air, never undershooting, and
      with a small step it follows exp(-t h / C) (time constant C / h): 1 %
      tolerance covers the first-order error of backward Euler at dt = tau/1000.
    """
    p = GE
    cap, h0, h1 = p.brake_heat_capacity, p.brake_cooling_base, p.brake_cooling_per_speed
    air, speed, power = 20.0, 60.0, 50e3
    h = h0 + h1 * speed
    t = 250.0
    history = [t]
    # 300 s = 20 time constants (C / h = 15 s): residual excess ~ e^-20.
    for _ in range(6000):
        t = update_disc_temperature(t, power, speed, air, 0.05, cap, h0, h1)
        history.append(t)
    assert all(b > a for a, b in zip(history, history[1:]))
    assert t == pytest.approx(air + power / h, rel=1e-6)

    t0 = 900.0
    tau = cap / h
    dt = tau / 1000.0
    t = t0
    cooling = [t]
    for _ in range(1000):
        t = update_disc_temperature(t, 0.0, speed, air, dt, cap, h0, h1)
        cooling.append(t)
    assert all(air < b < a for a, b in zip(cooling, cooling[1:]))
    expected = air + (t0 - air) * math.exp(-1.0)
    assert (t - air) == pytest.approx(expected - air, rel=0.01)
    # A huge step lands on the air temperature without overshoot (L-stable).
    t_big = update_disc_temperature(t0, 0.0, speed, air, 1e6, cap, h0, h1)
    assert air < t_big < air + 0.01 * (t0 - air)
    # Faster air flow cools faster.
    slow = update_disc_temperature(t0, 0.0, 10.0, air, 1.0, cap, h0, h1)
    fast = update_disc_temperature(t0, 0.0, 80.0, air, 1.0, cap, h0, h1)
    assert fast < slow < t0


# --------------------------------------------------------------------------
# Suspension
# --------------------------------------------------------------------------


def _settle(sus, ax, ay, aero_f, aero_r, g_n, seconds=5.0):
    loads = None
    for _ in range(int(seconds / DT)):
        loads = sus.step(DT, ax, ay, aero_f, aero_r, g_n)
    return list(loads)


TOTAL_FUEL = 50.0


@pytest.mark.verification
@pytest.mark.parametrize("vehicle_id", sorted(VEHICLES))
def test_static_loads_sum_to_weight(vehicle_id):
    """No inputs: four loads sum to m g and the front share is the static one;
    the body stays in equilibrium (no drift)."""
    p = VEHICLES[vehicle_id]
    m = p.mass + TOTAL_FUEL
    sus = Suspension(p, m)
    first = list(sus.step(DT, 0.0, 0.0, 0.0, 0.0, GRAVITY))
    assert sum(first) == pytest.approx(m * GRAVITY, rel=1e-12)
    assert (first[FL] + first[FR]) == pytest.approx(m * GRAVITY * p.front_weight_fraction, rel=1e-12)
    assert first[FL] == pytest.approx(first[FR], rel=1e-12)
    assert first[RL] == pytest.approx(first[RR], rel=1e-12)
    later = _settle(sus, 0.0, 0.0, 0.0, 0.0, GRAVITY, seconds=2.0)
    assert later == pytest.approx(first, rel=1e-12)
    assert sus.ride_front == pytest.approx(p.ride_height_front, abs=1e-12)
    assert sus.ride_rear == pytest.approx(p.ride_height_rear, abs=1e-12)
    # Effective gravity (e.g. a compression) scales the total load.
    dip = Suspension(p, m)
    loads = _settle(dip, 0.0, 0.0, 0.0, 0.0, 1.3 * GRAVITY)
    assert sum(loads) == pytest.approx(m * 1.3 * GRAVITY, rel=1e-6)


@pytest.mark.verification
@pytest.mark.parametrize("vehicle_id", sorted(VEHICLES))
@pytest.mark.parametrize("ay", [12.0, -9.0])
def test_steady_lateral_load_transfer(vehicle_id, ay):
    """Steady cornering: total lateral load transfer vs m a_y h.

    Derivation (moments about the longitudinal axis at ground level, steady
    state, phi = roll angle, arm = h_s - h_ra, outside = right for a_y > 0):

        sum_j dF_j t_j = (K_springs + K_arb) phi + a_y sum_j (m_sj h_rcj + 2 m_uj r_j)
        (K_springs + K_arb) phi = m_s a_y arm + m_s g arm phi        (roll balance)

    and m h = m_s h_s + sum m_u r (definition of h_s), hence

        sum_j dF_j t_j = m a_y h + m_s g arm phi + a_y (sum_j m_sj h_rcj - m_s h_ra)

    where dF_j = (F_out - F_in) / 2 on axle j. The second term is the lateral
    shift of the rolled sprung mass; the third appears because h_ra is taken
    at the whole-car CG while the link transfer uses the per-axle sprung
    masses. Both are tiny (< 0.5 % of m a_y h for these cars); the exact
    relation is asserted to 1e-6 after the 5 s settle (roll damping ratio about
    0.7, so the transient has decayed by many orders of magnitude) and the
    classic m a_y h to 0.5 %. The elastic split per axle follows the roll
    stiffness: dF_f t_f = (k_f t_f^2 / 2 + K_arb,f) phi + link transfer.
    No downforce is applied, so |a_y| is kept at or below 1.2 g where all four
    tyres stay loaded (the derivation assumes no wheel lift).
    """
    p = VEHICLES[vehicle_id]
    m = p.mass + TOTAL_FUEL
    sus = Suspension(p, m)
    loads = _settle(sus, 0.0, ay, 0.0, 0.0, GRAVITY)
    phi = sus.phi
    tf, tr = p.track_front, p.track_rear
    outside, inside = ((FR, RR), (FL, RL)) if ay > 0 else ((FL, RL), (FR, RR))
    assert min(loads) > 0.0
    df = 0.5 * (loads[outside[0]] - loads[inside[0]])
    dr = 0.5 * (loads[outside[1]] - loads[inside[1]])
    assert df > 0.0 and dr > 0.0
    assert sum(loads) == pytest.approx(m * GRAVITY, rel=1e-9)
    moment = df * tf + dr * tr

    arm = sus.h_s - sus.h_ra
    k_springs = 0.5 * p.wheel_rate_front * tf ** 2 + 0.5 * p.wheel_rate_rear * tr ** 2
    k_arb = p.arb_front + p.arb_rear
    phi_ss = sus.m_s * ay * arm / (k_springs + k_arb - sus.m_s * GRAVITY * arm)
    assert phi == pytest.approx(phi_ss, rel=1e-6)
    assert phi * ay > 0.0  # body rolls toward the outside

    axis_term = ay * (
        sus.m_s_front * p.roll_centre_front + sus.m_s_rear * p.roll_centre_rear - sus.m_s * sus.h_ra
    )
    exact = m * ay * p.cg_height + sus.m_s * GRAVITY * arm * phi + axis_term
    assert abs(moment) == pytest.approx(abs(exact), rel=1e-6)
    assert abs(moment) == pytest.approx(abs(m * ay * p.cg_height), rel=0.005)

    link_f = (sus.m_s_front * p.roll_centre_front + 2.0 * p.unsprung_front * p.tyre_radius_front) * abs(ay) / tf
    elastic_f = (0.5 * p.wheel_rate_front * tf ** 2 + p.arb_front) * abs(phi) / tf
    assert df == pytest.approx(elastic_f + link_f, rel=1e-6)


@pytest.mark.verification
@pytest.mark.parametrize("vehicle_id", sorted(VEHICLES))
@pytest.mark.parametrize("ax", [-40.0, -15.0, 10.0])
def test_steady_longitudinal_load_transfer(vehicle_id, ax):
    """Steady braking/acceleration: front axle gains m (-a_x) h / L.

    Spring path: pitch balance gives F_springs,front = -(1 - chi) m_s a_x h_s / L;
    link path (anti-dive/anti-squat chi and unsprung masses) adds
    (chi m_s h_s + sum m_u r) (-a_x) / L. The sum is (m_s h_s + sum m_u r)(-a_x)/L
    = m (-a_x) h / L independently of chi. Tolerance 1e-6 relative after a 5 s
    settle (pitch damping ratio about 0.7).
    """
    p = VEHICLES[vehicle_id]
    m = p.mass + TOTAL_FUEL
    sus = Suspension(p, m)
    loads = _settle(sus, ax, 0.0, 0.0, 0.0, GRAVITY)
    static_front = m * GRAVITY * p.front_weight_fraction
    front_gain = loads[FL] + loads[FR] - static_front
    assert front_gain == pytest.approx(m * (-ax) * p.cg_height / p.wheelbase, rel=1e-6)
    assert sum(loads) == pytest.approx(m * GRAVITY, rel=1e-9)
    assert loads[FL] == pytest.approx(loads[FR], rel=1e-9)
    if ax < 0.0:
        assert sus.theta > 0.0  # nose dives under braking
    else:
        assert sus.theta < 0.0


@pytest.mark.verification
@pytest.mark.parametrize("vehicle_id", sorted(VEHICLES))
def test_steady_aero_load_reaches_tyres(vehicle_id):
    """Steady downforce F_f (at the front axle) and F_r (rear axle) end up
    entirely on the corresponding tyres; the body only sinks."""
    p = VEHICLES[vehicle_id]
    m = p.mass + TOTAL_FUEL
    # 4 kN / 5 kN keep every car inside its 30 mm bump travel (turbo rear:
    # 5000 / (2 * 110000) = 22.7 mm), so the ride-height check is linear.
    aero_f, aero_r = 4000.0, 5000.0
    sus = Suspension(p, m)
    loads = _settle(sus, 0.0, 0.0, aero_f, aero_r, GRAVITY)
    static_front = m * GRAVITY * p.front_weight_fraction
    static_rear = m * GRAVITY - static_front
    assert sum(loads) == pytest.approx(m * GRAVITY + aero_f + aero_r, rel=1e-6)
    assert loads[FL] + loads[FR] - static_front == pytest.approx(aero_f, rel=1e-5)
    assert loads[RL] + loads[RR] - static_rear == pytest.approx(aero_r, rel=1e-5)
    assert sus.ride_front < p.ride_height_front
    assert sus.ride_rear < p.ride_height_rear
    # Spring deflection matches the wheel rate (no bump-stop contact here).
    assert sus.ride_front == pytest.approx(p.ride_height_front - aero_f / (2.0 * p.wheel_rate_front), rel=1e-4)
    assert sus.ride_rear == pytest.approx(p.ride_height_rear - aero_r / (2.0 * p.wheel_rate_rear), rel=1e-4)


@pytest.mark.verification
def test_wheel_lift_keeps_vertical_equilibrium():
    """With no downforce, 2.5 g of steady lateral specific force lifts the
    inside front wheel of the 2022 car. In steady state the tyres must still
    carry exactly the weight (vertical equilibrium, 0.1 % tolerance): tyre
    contact is one-sided, so the lifted corner's would-be tensile load goes to
    the other wheel of the same axle instead of being clipped away."""
    p = GE
    m = p.mass + TOTAL_FUEL
    sus = Suspension(p, m)
    loads = _settle(sus, 0.0, -25.0, 0.0, 0.0, GRAVITY)
    assert loads[FR] == 0.0  # precondition: inside front wheel lifted
    assert sum(loads) == pytest.approx(m * GRAVITY, rel=1e-3)

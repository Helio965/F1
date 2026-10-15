"""Vehicle and tyre parameter sets.

IMPORTANT - provenance: every preset below is a *generic* single-seater
parameter set whose values are order-of-magnitude estimates compiled from
broadly published ranges (regulation limits such as minimum mass or fuel
allowance, typical engine architecture of the era, tyre size class). They are
NOT official specifications of any team, car or season and must not be shown
as such. Each preset carries a ``provenance`` block that the UI displays.

Separation of concerns: nothing here refers to a circuit. Track properties live
in :mod:`physics_engine.track`; the same vehicle parameters run on any track.
"""

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class TyreCompound:
    id: str
    name: str
    mu_peak: float  # peak friction coefficient on reference dry asphalt at fz_nominal
    load_sensitivity: float  # mu = mu_peak * (1 - load_sensitivity * (Fz - Fz0) / Fz0)
    fz_nominal: float  # N
    kappa_peak: float  # slip ratio at peak longitudinal force
    alpha_peak: float  # slip angle at peak lateral force, rad
    shape_lateral: float  # Magic Formula C (lateral)
    shape_longitudinal: float  # Magic Formula C (longitudinal)
    temp_optimal: float  # degC, effective tread temperature for peak grip
    temp_window: float  # degC, half-width where grip falls by temp_grip_loss
    temp_grip_loss: float  # fractional grip loss at +/- temp_window
    wear_rate: float  # fraction of tread per MJ of sliding energy (reference)
    wear_grip_loss: float  # fractional grip loss at 100 % wear (before cliff)
    wear_cliff: float  # wear fraction where the "cliff" starts
    drain_capacity: float  # m^3/s of water the tread can channel at speed (per tyre)
    wet_ratio: float  # grip retained on a damp surface (thin film fully drained)
    dry_wear_multiplier: float  # extra wear when running on a dry surface (treaded tyres)
    heat_fraction: float  # share of sliding power that heats the tread surface
    rolling_resistance: float  # C_rr on reference asphalt
    pressure_optimal: float  # kPa (hot)
    pressure_sensitivity: float  # grip loss per (relative pressure deviation)^2
    colour: str = "#d8dfe6"


# Compounds: names describe roles, not a manufacturer's commercial range.
COMPOUNDS = {
    "soft": TyreCompound(
        id="soft", name="Slick macio", mu_peak=1.80, load_sensitivity=0.13, fz_nominal=3500.0,
        kappa_peak=0.10, alpha_peak=0.105, shape_lateral=1.35, shape_longitudinal=1.45,
        temp_optimal=100.0, temp_window=28.0, temp_grip_loss=0.10, wear_rate=0.0105,
        wear_grip_loss=0.18, wear_cliff=0.75, drain_capacity=0.0, wet_ratio=0.62,
        dry_wear_multiplier=1.0, heat_fraction=0.55, rolling_resistance=0.013,
        pressure_optimal=165.0, pressure_sensitivity=0.8, colour="#ef4a3c",
    ),
    "medium": TyreCompound(
        id="medium", name="Slick médio", mu_peak=1.75, load_sensitivity=0.13, fz_nominal=3500.0,
        kappa_peak=0.10, alpha_peak=0.11, shape_lateral=1.35, shape_longitudinal=1.45,
        temp_optimal=105.0, temp_window=30.0, temp_grip_loss=0.10, wear_rate=0.0072,
        wear_grip_loss=0.16, wear_cliff=0.8, drain_capacity=0.0, wet_ratio=0.62,
        dry_wear_multiplier=1.0, heat_fraction=0.52, rolling_resistance=0.013,
        pressure_optimal=165.0, pressure_sensitivity=0.8, colour="#f2d14b",
    ),
    "hard": TyreCompound(
        id="hard", name="Slick duro", mu_peak=1.70, load_sensitivity=0.13, fz_nominal=3500.0,
        kappa_peak=0.10, alpha_peak=0.115, shape_lateral=1.35, shape_longitudinal=1.45,
        temp_optimal=110.0, temp_window=32.0, temp_grip_loss=0.10, wear_rate=0.0050,
        wear_grip_loss=0.14, wear_cliff=0.85, drain_capacity=0.0, wet_ratio=0.60,
        dry_wear_multiplier=1.0, heat_fraction=0.50, rolling_resistance=0.013,
        pressure_optimal=165.0, pressure_sensitivity=0.8, colour="#eef1f2",
    ),
    "intermediate": TyreCompound(
        id="intermediate", name="Intermediário", mu_peak=1.52, load_sensitivity=0.12,
        fz_nominal=3500.0, kappa_peak=0.11, alpha_peak=0.12, shape_lateral=1.30,
        shape_longitudinal=1.40, temp_optimal=75.0, temp_window=28.0, temp_grip_loss=0.12,
        wear_rate=0.0080, wear_grip_loss=0.15, wear_cliff=0.8, drain_capacity=0.030,
        wet_ratio=0.86, dry_wear_multiplier=2.5, heat_fraction=0.55, rolling_resistance=0.015,
        pressure_optimal=160.0, pressure_sensitivity=0.7, colour="#47c46a",
    ),
    "wet": TyreCompound(
        id="wet", name="Chuva", mu_peak=1.42, load_sensitivity=0.12, fz_nominal=3500.0,
        kappa_peak=0.12, alpha_peak=0.13, shape_lateral=1.28, shape_longitudinal=1.38,
        temp_optimal=62.0, temp_window=26.0, temp_grip_loss=0.12, wear_rate=0.0085,
        wear_grip_loss=0.15, wear_cliff=0.8, drain_capacity=0.075, wet_ratio=0.90,
        dry_wear_multiplier=3.5, heat_fraction=0.55, rolling_resistance=0.017,
        pressure_optimal=155.0, pressure_sensitivity=0.7, colour="#3c8ee8",
    ),
    "grooved": TyreCompound(
        id="grooved", name="Seco com sulcos", mu_peak=1.60, load_sensitivity=0.14,
        fz_nominal=3000.0, kappa_peak=0.10, alpha_peak=0.10, shape_lateral=1.33,
        shape_longitudinal=1.43, temp_optimal=100.0, temp_window=28.0, temp_grip_loss=0.10,
        wear_rate=0.0070, wear_grip_loss=0.16, wear_cliff=0.8, drain_capacity=0.004,
        wet_ratio=0.66, dry_wear_multiplier=1.0, heat_fraction=0.52, rolling_resistance=0.013,
        pressure_optimal=150.0, pressure_sensitivity=0.8, colour="#f2d14b",
    ),
    "crossply-slick": TyreCompound(
        id="crossply-slick", name="Slick (época turbo)", mu_peak=1.55, load_sensitivity=0.15,
        fz_nominal=2800.0, kappa_peak=0.11, alpha_peak=0.12, shape_lateral=1.30,
        shape_longitudinal=1.40, temp_optimal=95.0, temp_window=30.0, temp_grip_loss=0.10,
        wear_rate=0.0080, wear_grip_loss=0.17, wear_cliff=0.8, drain_capacity=0.0,
        wet_ratio=0.60, dry_wear_multiplier=1.0, heat_fraction=0.52, rolling_resistance=0.014,
        pressure_optimal=140.0, pressure_sensitivity=0.8, colour="#e9e4d4",
    ),
}


@dataclass(frozen=True)
class VehicleParams:
    id: str
    name: str
    era: str
    provenance: dict
    # mass and geometry
    mass: float  # kg, with driver, without fuel
    fuel_capacity: float  # kg
    cg_height: float  # m
    front_weight_fraction: float  # static share on the front axle (no fuel)
    wheelbase: float  # m
    track_front: float  # m
    track_rear: float  # m
    yaw_inertia: float  # kg m^2 (with driver, no fuel)
    pitch_inertia: float  # kg m^2 (sprung)
    roll_inertia: float  # kg m^2 (sprung)
    unsprung_front: float  # kg per corner
    unsprung_rear: float  # kg per corner
    length: float  # m, collision footprint
    width: float  # m, collision footprint
    # suspension (rates measured at the wheel)
    wheel_rate_front: float  # N/m
    wheel_rate_rear: float  # N/m
    damper_front: float  # N s/m
    damper_rear: float  # N s/m
    arb_front: float  # N m/rad
    arb_rear: float  # N m/rad
    roll_centre_front: float  # m
    roll_centre_rear: float  # m
    anti_dive: float  # fraction of longitudinal transfer taken by the links (braking)
    anti_squat: float  # fraction (acceleration)
    ride_height_front: float  # m, static
    ride_height_rear: float  # m, static
    bump_travel: float  # m, compression before the bump stop
    bump_stop_rate: float  # N/m additional rate beyond travel
    # steering
    max_steer: float  # rad, road-wheel angle
    steer_rate: float  # rad/s, road-wheel angle rate limit
    ackermann: float  # 0 = parallel, 1 = full Ackermann
    # aerodynamics (Cl*A and Cd*A in m^2 at reference ride heights)
    cla_front: float
    cla_rear: float
    cda: float
    ground_effect: float  # relative change of ClA per relative ride-height change
    ride_height_ref: float  # m, mean aero reference height
    stall_height: float  # m, below this the underbody stalls
    stall_loss: float  # fractional downforce loss in full stall
    wing_cla_range: tuple  # (min, max) multiplier of ClA for wing level 0..1
    wing_cda_range: tuple  # (min, max) multiplier of CdA for wing level 0..1
    drs: bool
    drs_cla_rear_factor: float
    drs_cda_factor: float
    # tyres and wheels
    tyre_radius_front: float
    tyre_radius_rear: float
    tyre_width_front: float
    tyre_width_rear: float
    wheel_inertia_front: float  # kg m^2 (wheel, tyre, brake disc)
    wheel_inertia_rear: float
    relaxation_lateral: float  # m
    relaxation_longitudinal: float  # m
    compounds: tuple
    default_compound: str
    cold_pressure: float  # kPa
    # powertrain
    torque_rpm: tuple
    torque_nm: tuple
    idle_rpm: float
    launch_rpm: float
    rev_limit: float
    engine_inertia: float  # kg m^2
    friction_torque_base: float  # N m
    friction_torque_per_rpm: float  # N m per rpm
    bsfc: float  # kg/J (brake-specific fuel consumption)
    gear_ratios: tuple
    final_drive: float
    driveline_efficiency: float
    shift_time: float  # s with zero drive torque per shift
    upshift_rpm: float
    downshift_rpm: float
    diff_preload: float  # N m
    diff_ramp: float  # locking torque per input torque
    engine_brake_factor: float  # share of engine friction torque transmitted on overrun (engine-braking map)
    ers_power: float  # W at the axle (0 = none)
    ers_deploy_per_lap: float  # J
    ers_harvest_per_lap: float  # J
    ers_battery: float  # J usable store
    # brakes
    brake_torque_total: float  # N m at full pedal, all four wheels
    brake_bias: float  # default front share
    brake_heat_capacity: float  # J/K per corner
    brake_cooling_base: float  # W/K
    brake_cooling_per_speed: float  # W/K per m/s
    brake_temp_optimal: tuple  # (low, high) degC of full pad friction
    # damage
    damage_aero_loss: float  # fractional downforce loss at 100 % damage
    notes: str = ""
    extra: dict = field(default_factory=dict)


_ESTIMATE_NOTE = (
    "Valores de ordem de grandeza estimados a partir de faixas amplamente divulgadas "
    "(limites de regulamento, arquitetura típica de motor, classe de pneus). Não são "
    "especificações oficiais de nenhum carro, equipe ou temporada."
)


def _geometric_gears(first, last, count):
    ratio = (last / first) ** (1.0 / (count - 1))
    return tuple(round(first * ratio ** i, 4) for i in range(count))


GROUND_EFFECT_2022 = VehicleParams(
    id="ground-effect-2022",
    name="Monoposto genérico de efeito-solo",
    era="2022–2025 (referência)",
    provenance={
        "status": "estimate",
        "summary": _ESTIMATE_NOTE,
        "public_reference_points": [
            "Massa mínima regulamentar com piloto da ordem de 800 kg (FIA, regulamento técnico 2022–2025)",
            "Combustível máximo de corrida da ordem de 110 kg (FIA, regulamento esportivo)",
            "Rodas de 18 polegadas desde 2022",
            "Motor V6 1,6 L turbo híbrido; MGU-K limitado a 120 kW (FIA, 2014–2025)",
        ],
    },
    mass=800.0, fuel_capacity=110.0, cg_height=0.30, front_weight_fraction=0.455,
    wheelbase=3.6, track_front=1.60, track_rear=1.56,
    yaw_inertia=1150.0, pitch_inertia=950.0, roll_inertia=130.0,
    unsprung_front=34.0, unsprung_rear=38.0, length=5.6, width=2.0,
    wheel_rate_front=200000.0, wheel_rate_rear=170000.0,
    damper_front=5400.0, damper_rear=5600.0, arb_front=160000.0, arb_rear=110000.0,
    roll_centre_front=0.04, roll_centre_rear=0.07, anti_dive=0.35, anti_squat=0.25,
    ride_height_front=0.035, ride_height_rear=0.075, bump_travel=0.03, bump_stop_rate=1.6e6,
    max_steer=0.33, steer_rate=3.2, ackermann=0.3,
    cla_front=1.95, cla_rear=2.65, cda=1.25, ground_effect=0.45, ride_height_ref=0.05,
    stall_height=0.012, stall_loss=0.25, wing_cla_range=(0.82, 1.12),
    wing_cda_range=(0.86, 1.10), drs=True, drs_cla_rear_factor=0.78, drs_cda_factor=0.87,
    tyre_radius_front=0.36, tyre_radius_rear=0.36, tyre_width_front=0.305,
    tyre_width_rear=0.405, wheel_inertia_front=1.3, wheel_inertia_rear=1.6,
    relaxation_lateral=0.28, relaxation_longitudinal=0.12,
    compounds=("soft", "medium", "hard", "intermediate", "wet"), default_compound="medium",
    cold_pressure=150.0,
    torque_rpm=(3000.0, 4500.0, 6000.0, 8000.0, 10500.0, 11500.0, 12000.0, 12500.0, 13000.0),
    torque_nm=(300.0, 420.0, 520.0, 525.0, 509.0, 465.0, 446.0, 420.0, 380.0),
    idle_rpm=4000.0, launch_rpm=9000.0, rev_limit=12800.0, engine_inertia=0.06,
    friction_torque_base=25.0, friction_torque_per_rpm=0.004, bsfc=5.0e-8,
    gear_ratios=_geometric_gears(4.93, 1.55, 8), final_drive=3.0,
    driveline_efficiency=0.93, shift_time=0.01, upshift_rpm=12000.0, downshift_rpm=7600.0,
    diff_preload=80.0, diff_ramp=0.3, engine_brake_factor=0.45,
    ers_power=120000.0, ers_deploy_per_lap=4.0e6, ers_harvest_per_lap=2.0e6,
    ers_battery=4.0e6,
    brake_torque_total=15000.0, brake_bias=0.57, brake_heat_capacity=1500.0,
    brake_cooling_base=10.0, brake_cooling_per_speed=1.5, brake_temp_optimal=(350.0, 950.0),
    damage_aero_loss=0.35,
    notes="Carro de referência padrão do jogo.",
)

V10_2005 = replace(
    GROUND_EFFECT_2022,
    id="v10-2005",
    name="Monoposto genérico V10",
    era="2000–2005 (referência)",
    provenance={
        "status": "estimate",
        "summary": _ESTIMATE_NOTE,
        "public_reference_points": [
            "Motores V10 de 3,0 L aspirados de alta rotação (FIA, até 2005)",
            "Pneus de pista seca com sulcos longitudinais (1998–2008)",
            "Rodas de 13 polegadas",
        ],
    },
    mass=605.0, fuel_capacity=110.0, cg_height=0.28, front_weight_fraction=0.46,
    wheelbase=3.1, track_front=1.46, track_rear=1.41,
    yaw_inertia=900.0, pitch_inertia=720.0, roll_inertia=95.0,
    unsprung_front=28.0, unsprung_rear=32.0, length=4.6, width=1.8,
    wheel_rate_front=170000.0, wheel_rate_rear=150000.0,
    damper_front=4300.0, damper_rear=4600.0, arb_front=120000.0, arb_rear=80000.0,
    ride_height_front=0.03, ride_height_rear=0.06,
    cla_front=1.75, cla_rear=2.25, cda=1.15, ground_effect=0.18, ride_height_ref=0.045,
    stall_height=0.008, stall_loss=0.15, drs=False,
    tyre_radius_front=0.33, tyre_radius_rear=0.33, tyre_width_front=0.32,
    tyre_width_rear=0.36, wheel_inertia_front=1.0, wheel_inertia_rear=1.2,
    compounds=("grooved", "intermediate", "wet"), default_compound="grooved",
    cold_pressure=140.0,
    torque_rpm=(6000.0, 9000.0, 12000.0, 15000.0, 17000.0, 18500.0, 19200.0),
    torque_nm=(230.0, 290.0, 330.0, 365.0, 370.0, 345.0, 320.0),
    idle_rpm=5000.0, launch_rpm=11000.0, rev_limit=19300.0, engine_inertia=0.05,
    friction_torque_base=20.0, friction_torque_per_rpm=0.0035, bsfc=8.3e-8,
    gear_ratios=_geometric_gears(5.47, 1.785, 7), final_drive=4.0,
    shift_time=0.03, upshift_rpm=18900.0, downshift_rpm=13000.0, engine_brake_factor=0.8,
    ers_power=0.0, ers_deploy_per_lap=0.0, ers_harvest_per_lap=0.0, ers_battery=0.0,
    brake_torque_total=12000.0, brake_bias=0.58,
    notes="Era de motores aspirados de alta rotação, sem DRS e sem recuperação de energia.",
)

TURBO_1986 = replace(
    GROUND_EFFECT_2022,
    id="turbo-1986",
    name="Monoposto genérico turbo",
    era="1983–1988 (referência)",
    provenance={
        "status": "estimate",
        "summary": _ESTIMATE_NOTE,
        "public_reference_points": [
            "Motores turbo de 1,5 L (era turbo dos anos 1980)",
            "Fundo plano obrigatório a partir de 1983",
            "Câmbio manual em H",
        ],
    },
    mass=545.0, fuel_capacity=150.0, cg_height=0.32, front_weight_fraction=0.42,
    wheelbase=2.8, track_front=1.78, track_rear=1.64,
    yaw_inertia=760.0, pitch_inertia=600.0, roll_inertia=110.0,
    unsprung_front=30.0, unsprung_rear=36.0, length=4.4, width=2.1,
    wheel_rate_front=120000.0, wheel_rate_rear=110000.0,
    damper_front=3600.0, damper_rear=3900.0, arb_front=90000.0, arb_rear=60000.0,
    ride_height_front=0.04, ride_height_rear=0.07, max_steer=0.36,
    cla_front=1.05, cla_rear=1.55, cda=1.10, ground_effect=0.10, ride_height_ref=0.055,
    stall_height=0.01, stall_loss=0.1, drs=False,
    tyre_radius_front=0.31, tyre_radius_rear=0.34, tyre_width_front=0.30,
    tyre_width_rear=0.42, wheel_inertia_front=0.9, wheel_inertia_rear=1.3,
    compounds=("crossply-slick", "wet"), default_compound="crossply-slick",
    cold_pressure=130.0,
    torque_rpm=(5000.0, 6500.0, 8000.0, 9500.0, 11000.0, 12000.0, 12600.0),
    torque_nm=(170.0, 260.0, 450.0, 560.0, 560.0, 515.0, 470.0),
    idle_rpm=4500.0, launch_rpm=8500.0, rev_limit=12600.0, engine_inertia=0.07,
    friction_torque_base=22.0, friction_torque_per_rpm=0.004, bsfc=9.5e-8,
    gear_ratios=_geometric_gears(4.27, 1.39, 5), final_drive=3.4,
    shift_time=0.22, upshift_rpm=12200.0, downshift_rpm=8600.0, diff_preload=60.0,
    diff_ramp=0.25, engine_brake_factor=1.0,
    ers_power=0.0, ers_deploy_per_lap=0.0, ers_harvest_per_lap=0.0, ers_battery=0.0,
    brake_torque_total=10500.0, brake_bias=0.6,
    notes="Turbo com atraso de resposta representado pela curva de torque; sem DRS nem ERS.",
)

VEHICLES = {v.id: v for v in (GROUND_EFFECT_2022, V10_2005, TURBO_1986)}
DEFAULT_VEHICLE = GROUND_EFFECT_2022.id


def vehicle_summary(params):
    """JSON-friendly metadata for the UI."""
    peak_power = max(t * r * 0.10471975512 for t, r in zip(params.torque_nm, params.torque_rpm))
    return {
        "id": params.id,
        "name": params.name,
        "era": params.era,
        "provenance": params.provenance,
        "mass": params.mass,
        "peak_engine_power_kw": round(peak_power / 1000.0, 1),
        "ers_power_kw": round(params.ers_power / 1000.0, 1),
        "gears": len(params.gear_ratios),
        "drs": params.drs,
        "compounds": [
            {"id": c, "name": COMPOUNDS[c].name, "colour": COMPOUNDS[c].colour}
            for c in params.compounds
        ],
        "notes": params.notes,
        "geometry": {
            "wheelbase": params.wheelbase,
            "front": params.wheelbase * (1.0 - params.front_weight_fraction),
            "rear": params.wheelbase * params.front_weight_fraction,
            "radius_front": params.tyre_radius_front,
            "radius_rear": params.tyre_radius_rear,
            "length": params.length,
            "width": params.width,
        },
        "brake_bias": params.brake_bias,
        "fuel_capacity": params.fuel_capacity,
    }

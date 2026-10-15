"""Atmosphere and weather state that feed the physics.

Air density follows the ideal-gas law for moist air (dry air + water vapour
partial pressures). Saturation vapour pressure uses the Magnus-Tetens
approximation (Alduchov & Eskridge, 1996 coefficients). Pressure at
altitude uses the ISA troposphere barometric formula.
"""

import math

from .constants import (
    GRAVITY,
    ISA_LAPSE_RATE,
    ISA_SEA_LEVEL_PRESSURE,
    R_DRY_AIR,
    R_WATER_VAPOUR,
)

KELVIN = 273.15


def saturation_vapour_pressure(temperature_c):
    """Magnus formula over water, Pa (valid roughly -40..50 degC)."""
    return 610.94 * math.exp(17.625 * temperature_c / (temperature_c + 243.04))


def pressure_at_altitude(altitude_m, sea_level_pressure=ISA_SEA_LEVEL_PRESSURE):
    """ISA barometric formula for the troposphere (Pa)."""
    t0 = 288.15
    exponent = GRAVITY / (R_DRY_AIR * ISA_LAPSE_RATE)
    return sea_level_pressure * (1.0 - ISA_LAPSE_RATE * altitude_m / t0) ** exponent


def air_density(temperature_c, pressure_pa, relative_humidity):
    """Density of moist air, kg/m^3.

    rho = p_d / (R_d T) + p_v / (R_v T), with p_v = RH * p_sat(T).
    """
    t_k = temperature_c + KELVIN
    p_v = max(0.0, min(1.0, relative_humidity)) * saturation_vapour_pressure(temperature_c)
    p_d = pressure_pa - p_v
    return p_d / (R_DRY_AIR * t_k) + p_v / (R_WATER_VAPOUR * t_k)


# Rain-rate classes follow common meteorological usage (e.g. the AMS Glossary:
# light < 2.5 mm/h, moderate 2.5-7.6 mm/h, heavy > 7.6 mm/h). The chosen
# values sit inside each class; they are scenario presets, not measurements.
WEATHER_PRESETS = {
    "sun": {
        "label": "Seco",
        "air_temperature": 24.0,
        "track_temperature": 36.0,
        "relative_humidity": 0.45,
        "rain_rate_mm_h": 0.0,
        "cloud_cover": 0.1,
        "initial_wetness": 0.0,
        "wind_speed": 2.0,
        "wind_direction": 0.6,
    },
    "night": {
        "label": "Seco (noite)",
        "air_temperature": 19.0,
        "track_temperature": 22.0,
        "relative_humidity": 0.6,
        "rain_rate_mm_h": 0.0,
        "cloud_cover": 0.3,
        "initial_wetness": 0.0,
        "wind_speed": 1.5,
        "wind_direction": 2.1,
    },
    "light-rain": {
        "label": "Chuva leve",
        "air_temperature": 17.0,
        "track_temperature": 19.0,
        "relative_humidity": 0.9,
        "rain_rate_mm_h": 2.0,
        "cloud_cover": 0.95,
        "initial_wetness": 1.0,
        "wind_speed": 3.0,
        "wind_direction": 1.2,
    },
    "rain": {
        "label": "Chuva intensa",
        "air_temperature": 15.0,
        "track_temperature": 16.0,
        "relative_humidity": 0.97,
        "rain_rate_mm_h": 15.0,
        "cloud_cover": 1.0,
        "initial_wetness": 1.0,
        "wind_speed": 4.0,
        "wind_direction": 1.2,
    },
    "drying": {
        "label": "Pista secando",
        "air_temperature": 20.0,
        "track_temperature": 24.0,
        "relative_humidity": 0.75,
        "rain_rate_mm_h": 0.0,
        "cloud_cover": 0.5,
        "initial_wetness": 0.6,
        "wind_speed": 3.0,
        "wind_direction": 0.4,
    },
}


class Atmosphere:
    """Mutable ambient state shared by every car in a session."""

    __slots__ = (
        "air_temperature",
        "track_temperature",
        "relative_humidity",
        "pressure",
        "altitude",
        "rain_rate_mm_h",
        "cloud_cover",
        "wind_speed",
        "wind_direction",
        "density",
        "preset",
    )

    def __init__(self, preset="sun", altitude=0.0, overrides=None):
        data = dict(WEATHER_PRESETS.get(preset, WEATHER_PRESETS["sun"]))
        if overrides:
            for key, value in overrides.items():
                if key in data:
                    data[key] = float(value)
        self.preset = preset if preset in WEATHER_PRESETS else "sun"
        self.air_temperature = data["air_temperature"]
        self.track_temperature = data["track_temperature"]
        self.relative_humidity = data["relative_humidity"]
        self.rain_rate_mm_h = data["rain_rate_mm_h"]
        self.cloud_cover = data["cloud_cover"]
        self.wind_speed = data["wind_speed"]
        self.wind_direction = data["wind_direction"]
        self.altitude = float(altitude)
        self.pressure = pressure_at_altitude(self.altitude)
        self.density = 1.225
        self.refresh()

    def refresh(self):
        self.density = air_density(self.air_temperature, self.pressure, self.relative_humidity)

    def wind_vector(self):
        """Wind velocity (x, z) in m/s; direction is the heading the air moves toward."""
        return (
            self.wind_speed * math.sin(self.wind_direction),
            self.wind_speed * math.cos(self.wind_direction),
        )

    def initial_wetness(self):
        return WEATHER_PRESETS[self.preset]["initial_wetness"]

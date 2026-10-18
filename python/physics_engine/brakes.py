"""Brake system: torque distribution, pad friction vs temperature, disc heat.

Per-wheel torque at pedal fraction ``b`` with front bias ``beta``:
    T_front_wheel = b * beta * T_total / 2 * f_pad(T_disc)
    T_rear_wheel  = b * (1 - beta) * T_total / 2 * f_pad(T_disc)
Disc temperature (lumped):
    C dT/dt = P_brake - (h0 + h1 V)(T - T_air)
where P_brake = T_brake |omega| (zero while the wheel is locked: the energy is
then dissipated by the sliding tyre instead).
"""

INITIAL_BRAKE_TEMPERATURE = 250.0  # degC, after a formation lap


def pad_factor(temperature, optimal_low, optimal_high):
    """Relative pad friction: cold carbon bites poorly, overheated pads fade."""
    if temperature < optimal_low:
        return 0.55 + 0.45 * max(temperature, 0.0) / optimal_low
    if temperature > optimal_high:
        return max(0.5, 1.0 - 0.5 * (temperature - optimal_high) / 400.0)
    return 1.0


def wheel_brake_torques(pedal, bias, total_torque):
    front = pedal * bias * total_torque * 0.5
    rear = pedal * (1.0 - bias) * total_torque * 0.5
    return front, rear


def update_disc_temperature(temperature, power, speed, air_t, dt, capacity, h0, h1):
    h = h0 + h1 * abs(speed)
    a = dt / capacity
    return (temperature + a * (power + h * air_t)) / (1.0 + a * h)

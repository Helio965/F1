"""APEX universal vehicle physics engine.

Pure Python (standard library only) so the exact same source runs under
CPython for tests/benchmarks and under Pyodide (CPython compiled to
WebAssembly) inside the browser Web Worker. All quantities use SI units unless
a name says otherwise (temperatures in degrees Celsius, tyre pressure in kPa,
water film depth in millimetres).

Conventions (see docs/PHYSICS_EQUATIONS.md):
- World frame: x and z horizontal, y up (Three.js convention).
- Heading psi: forward unit vector is (sin psi, cos psi) in (x, z).
- Body frame (ISO 8855): x forward, y left, z up. Yaw rate r > 0 turns left,
  and d(psi)/dt = r. Positive road-wheel steer angle turns left.
- Driver steering input: +1 means "turn right" (keyboard/gamepad convention);
  it is converted to a negative road-wheel angle.
- Track lateral coordinate n > 0 is to the left of the travel direction.
"""

ENGINE_VERSION = "1.0.0"

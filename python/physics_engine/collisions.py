"""Rigid-body contacts in the ground plane: car-car and car-barrier.

Cars are oriented rectangles (length x width). Car-car contact uses the
separating-axis test (4 axes); the axis of least penetration is the contact
normal and the deepest vertex the contact point. Barriers are the lateral
offset curves of the track model, tested at the four footprint corners.

Impulse with restitution e and Coulomb friction mu_c (2-D, rotation about
the vertical axis):
    j_n = -(1 + e) v_rel.N / (1/m_a + 1/m_b + (rho_a x N)^2/I_a + (rho_b x N)^2/I_b)
    j_t = clamp(-v_rel.T / k_t, -mu_c j_n, mu_c j_n)
Penetration is removed by moving the bodies along N (mass-weighted, 80 %).
World convention: positions (x, z), yaw rate r about +y, so the velocity of
a point at offset rho is (r rho_z, -r rho_x) and the torque of an impulse J
is rho_z J_x - rho_x J_z.
Damage grows with the normal impulse expressed as a velocity change.
"""

import math

RESTITUTION_CAR = 0.25
RESTITUTION_WALL = 0.3
FRICTION_CAR = 0.4
FRICTION_WALL = 0.5
DAMAGE_THRESHOLD = 3.0  # m/s of normal velocity change before damage accrues
DAMAGE_PER_MPS = 0.018  # fraction of damage per m/s above the threshold


def _corners(car):
    half_l = 0.5 * car.p.length
    half_w = 0.5 * car.p.width
    s = math.sin(car.psi)
    c = math.cos(car.psi)
    fx, fz = s, c  # forward
    lx, lz = c, -s  # left
    out = []
    for a, b in ((half_l, half_w), (half_l, -half_w), (-half_l, -half_w), (-half_l, half_w)):
        out.append((car.x + fx * a + lx * b, car.z + fz * a + lz * b))
    return out


def _world_velocity(car):
    s = math.sin(car.psi)
    c = math.cos(car.psi)
    return car.u * s + car.v * c, car.u * c - car.v * s


def _set_world_velocity(car, vx, vz):
    s = math.sin(car.psi)
    c = math.cos(car.psi)
    car.u = vx * s + vz * c
    car.v = vx * c - vz * s


def _project(points, ax, az):
    lo = hi = points[0][0] * ax + points[0][1] * az
    for px, pz in points[1:]:
        d = px * ax + pz * az
        if d < lo:
            lo = d
        elif d > hi:
            hi = d
    return lo, hi


def car_car(a, b):
    """Resolve a contact between two cars. Returns impulse magnitude or 0."""
    dx = b.x - a.x
    dz = b.z - a.z
    reach = 0.5 * (math.hypot(a.p.length, a.p.width) + math.hypot(b.p.length, b.p.width))
    if dx * dx + dz * dz > reach * reach:
        return 0.0
    pa = _corners(a)
    pb = _corners(b)
    best_depth = float("inf")
    normal = None
    for car in (a, b):
        s = math.sin(car.psi)
        c = math.cos(car.psi)
        for ax, az in ((s, c), (c, -s)):
            a_lo, a_hi = _project(pa, ax, az)
            b_lo, b_hi = _project(pb, ax, az)
            overlap = min(a_hi, b_hi) - max(a_lo, b_lo)
            if overlap <= 0.0:
                return 0.0
            if overlap < best_depth:
                best_depth = overlap
                # normal from a toward b
                if (dx * ax + dz * az) < 0.0:
                    ax, az = -ax, -az
                normal = (ax, az)
    nx, nz = normal
    # contact point: deepest vertex of b along -N, or of a along +N
    vb = min(pb, key=lambda p: p[0] * nx + p[1] * nz)
    va = max(pa, key=lambda p: p[0] * nx + p[1] * nz)
    px = 0.5 * (vb[0] + va[0])
    pz = 0.5 * (vb[1] + va[1])
    return _impulse(a, b, nx, nz, px, pz, best_depth, RESTITUTION_CAR, FRICTION_CAR)


def _impulse(a, b, nx, nz, px, pz, depth, restitution, friction):
    ma = a.mass
    ia = a.p.yaw_inertia
    rax, raz = px - a.x, pz - a.z
    vax, vaz = _world_velocity(a)
    va_px = vax + a.r * raz
    va_pz = vaz - a.r * rax
    if b is not None:
        mb = b.mass
        ib = b.p.yaw_inertia
        rbx, rbz = px - b.x, pz - b.z
        vbx, vbz = _world_velocity(b)
        vb_px = vbx + b.r * rbz
        vb_pz = vbz - b.r * rbx
    else:
        mb = ib = float("inf")
        rbx = rbz = 0.0
        vbx = vbz = vb_px = vb_pz = 0.0
    rel_x = vb_px - va_px
    rel_z = vb_pz - va_pz
    vn = rel_x * nx + rel_z * nz
    # positional correction (always)
    inv_a = 1.0 / ma
    inv_b = 0.0 if b is None else 1.0 / mb
    share_a = inv_a / (inv_a + inv_b)
    corr = 0.8 * depth
    a.x -= nx * corr * share_a
    a.z -= nz * corr * share_a
    if b is not None:
        b.x += nx * corr * (1.0 - share_a)
        b.z += nz * corr * (1.0 - share_a)
    if vn >= 0.0:
        return 0.0
    cross_a = raz * nx - rax * nz
    cross_b = rbz * nx - rbx * nz
    k_n = inv_a + inv_b + cross_a * cross_a / ia + (0.0 if b is None else cross_b * cross_b / ib)
    jn = -(1.0 + restitution) * vn / k_n
    tx, tz = -nz, nx
    vt = rel_x * tx + rel_z * tz
    cross_at = raz * tx - rax * tz
    cross_bt = rbz * tx - rbx * tz
    k_t = inv_a + inv_b + cross_at * cross_at / ia + (0.0 if b is None else cross_bt * cross_bt / ib)
    jt = -vt / k_t
    limit = friction * jn
    if jt > limit:
        jt = limit
    elif jt < -limit:
        jt = -limit
    jx = nx * jn + tx * jt
    jz = nz * jn + tz * jt
    # apply: b receives +J, a receives -J
    _set_world_velocity(a, vax - jx * inv_a, vaz - jz * inv_a)
    a.r -= (raz * jx - rax * jz) / ia
    dv_a = jn * inv_a
    _damage(a, dv_a)
    if b is not None:
        _set_world_velocity(b, vbx + jx * inv_b, vbz + jz * inv_b)
        b.r += (rbz * jx - rbx * jz) / ib
        _damage(b, jn * inv_b)
    return jn


def _damage(car, dv):
    if dv > DAMAGE_THRESHOLD:
        car.damage = min(1.0, car.damage + DAMAGE_PER_MPS * (dv - DAMAGE_THRESHOLD))


def car_barrier(car):
    """Resolve contacts of the footprint corners with the barriers. Returns impulse sum."""
    track = car.track
    reach = 0.5 * math.hypot(car.p.length, car.p.width) + 0.5
    i0 = car.track_index
    n_s = track.n
    span = int(reach / track.ds) + 1
    near_left = min(track.barrier_left[(i0 + d) % n_s] for d in range(-span, span + 1))
    near_right = min(track.barrier_right[(i0 + d) % n_s] for d in range(-span, span + 1))
    if car.n + reach < near_left and -car.n + reach < near_right:
        return 0.0
    total = 0.0
    for px, pz in _corners(car):
        idx, t, s, n, _ = track.project(px, pz, car.track_index)
        left = track.sample(idx, t, track.barrier_left)
        right = track.sample(idx, t, track.barrier_right)
        b = idx + 1 if idx + 1 < track.n else 0
        tx = track.txs[idx] + (track.txs[b] - track.txs[idx]) * t
        tz = track.tzs[idx] + (track.tzs[b] - track.tzs[idx]) * t
        norm = math.hypot(tx, tz) or 1.0
        lx, lz = tz / norm, -tx / norm
        if n > left:
            # wall on the left: push toward the right (normal from car to wall is +L)
            total += _impulse(car, None, lx, lz, px, pz, n - left, RESTITUTION_WALL, FRICTION_WALL)
        elif -n > right:
            total += _impulse(car, None, -lx, -lz, px, pz, -n - right, RESTITUTION_WALL, FRICTION_WALL)
    return total

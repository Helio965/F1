import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

type Detail = "low" | "medium" | "high";
type Ring = [z: number, halfWidth: number, halfHeight: number, centerY: number];

/** Closed, smoothly changing body sections. All cars point down their local +Z axis. */
function bodyGeometry(rings: Ring[]): THREE.BufferGeometry {
  const section = [
    [-1, -0.45],
    [-0.95, -0.85],
    [-0.65, -1],
    [0.65, -1],
    [0.95, -0.85],
    [1, -0.45],
    [1, 0.25],
    [0.75, 0.8],
    [0.35, 1],
    [-0.35, 1],
    [-0.75, 0.8],
    [-1, 0.25],
  ];
  const positions: number[] = [];
  const uvs: number[] = [];
  const indices: number[] = [];
  for (let r = 0; r < rings.length; r++) {
    const [z, w, h, y] = rings[r];
    section.forEach(([px, py], i) => {
      positions.push(px * w, y + py * h, z);
      uvs.push(i / section.length, r / Math.max(1, rings.length - 1));
    });
  }
  const n = section.length;
  for (let r = 0; r < rings.length - 1; r++) {
    for (let i = 0; i < n; i++) {
      const a = r * n + i,
        b = r * n + ((i + 1) % n),
        c = (r + 1) * n + i,
        d = (r + 1) * n + ((i + 1) % n);
      indices.push(a, b, c, b, d, c);
    }
  }
  for (let i = 1; i < n - 1; i++) {
    indices.push(0, i + 1, i);
    const a = (rings.length - 1) * n;
    indices.push(a, a + i, a + i + 1);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.Float32BufferAttribute(positions, 3),
  );
  geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  return geometry;
}

/** An original open-wheel racer built entirely from geometry, without downloaded assets. */
export function createFormulaCar(color: string, id: number): THREE.Group {
  const car = new THREE.Group();
  car.name = `Formula car ${id}`;
  const paint = new THREE.MeshStandardMaterial({
    color,
    metalness: 0.64,
    roughness: 0.26,
  });
  const carbon = new THREE.MeshStandardMaterial({
    color: "#111820",
    metalness: 0.4,
    roughness: 0.43,
  });
  const black = new THREE.MeshStandardMaterial({
    color: "#070b10",
    roughness: 0.76,
  });
  const silver = new THREE.MeshStandardMaterial({
    color: "#a8b3bd",
    metalness: 0.91,
    roughness: 0.3,
  });
  const darkMetal = new THREE.MeshStandardMaterial({
    color: "#303842",
    metalness: 0.8,
    roughness: 0.37,
  });
  const accent = new THREE.MeshStandardMaterial({
    color: "#d8f8e9",
    metalness: 0.35,
    roughness: 0.35,
  });
  const rubber = new THREE.MeshStandardMaterial({
    color: "#111316",
    roughness: 0.94,
  });
  const tireMark = new THREE.MeshStandardMaterial({
    color: "#d8ed71",
    roughness: 0.66,
  });
  const helmetPaint = new THREE.MeshStandardMaterial({
    color: "#f4db68",
    metalness: 0.4,
    roughness: 0.25,
  });
  const visor = new THREE.MeshStandardMaterial({
    color: "#09283b",
    metalness: 1,
    roughness: 0.11,
  });
  const red = new THREE.MeshStandardMaterial({
    color: "#f52339",
    emissive: "#f52339",
    emissiveIntensity: 1.7,
    roughness: 0.3,
  });
  const batches = new Map<THREE.Material, THREE.BufferGeometry[]>();
  const detailBatches = new Map<THREE.Material, THREE.BufferGeometry[]>();
  const detailMeshes: THREE.Object3D[] = [];
  function add(
    geometry: THREE.BufferGeometry,
    material: THREE.Material,
    position: [number, number, number] = [0, 0, 0],
    rotation: [number, number, number] = [0, 0, 0],
    detail = false,
  ) {
    const matrix = new THREE.Matrix4().compose(
      new THREE.Vector3(...position),
      new THREE.Quaternion().setFromEuler(new THREE.Euler(...rotation)),
      new THREE.Vector3(1, 1, 1),
    );
    geometry.applyMatrix4(matrix);
    const batch = detail ? detailBatches : batches;
    const list = batch.get(material) ?? [];
    list.push(geometry);
    batch.set(material, list);
  }
  function box(
    w: number,
    h: number,
    d: number,
    material: THREE.Material,
    p: [number, number, number],
    rot: [number, number, number] = [0, 0, 0],
    detail = false,
  ) {
    add(new THREE.BoxGeometry(w, h, d), material, p, rot, detail);
  }
  function rod(
    a: [number, number, number],
    b: [number, number, number],
    radius: number,
    material: THREE.Material,
    detail = false,
  ) {
    const from = new THREE.Vector3(...a),
      to = new THREE.Vector3(...b);
    const delta = to.clone().sub(from);
    const geometry = new THREE.CylinderGeometry(
      radius,
      radius,
      delta.length(),
      7,
    );
    geometry.applyQuaternion(
      new THREE.Quaternion().setFromUnitVectors(
        new THREE.Vector3(0, 1, 0),
        delta.normalize(),
      ),
    );
    geometry.translate(
      ...(from.add(to).multiplyScalar(0.5).toArray() as [
        number,
        number,
        number,
      ]),
    );
    add(geometry, material, [0, 0, 0], [0, 0, 0], detail);
  }
  function tube(
    points: [number, number, number][],
    radius: number,
    material: THREE.Material,
    detail = false,
  ) {
    add(
      new THREE.TubeGeometry(
        new THREE.CatmullRomCurve3(points.map((p) => new THREE.Vector3(...p))),
        24,
        radius,
        7,
        false,
      ),
      material,
      [0, 0, 0],
      [0, 0, 0],
      detail,
    );
  }

  // Floor, diffuser and sculpted main monocoque.
  add(
    bodyGeometry([
      [-1.92, 0.58, 0.025, 0.145],
      [-1.45, 0.81, 0.025, 0.12],
      [0.62, 0.79, 0.025, 0.12],
      [1.35, 0.39, 0.025, 0.15],
    ]),
    carbon,
  );
  add(
    bodyGeometry([
      [-1.78, 0.23, 0.14, 0.38],
      [-1.35, 0.4, 0.22, 0.43],
      [-0.4, 0.39, 0.25, 0.48],
      [0.5, 0.32, 0.2, 0.47],
      [1.05, 0.21, 0.14, 0.43],
      [1.98, 0.12, 0.075, 0.32],
      [2.12, 0.105, 0.04, 0.3],
    ]),
    paint,
  );
  add(
    bodyGeometry([
      [-1.62, 0.1, 0.19, 0.72],
      [-1.15, 0.25, 0.3, 0.77],
      [-0.65, 0.3, 0.29, 0.78],
      [-0.39, 0.22, 0.18, 0.73],
    ]),
    paint,
  );
  // Airbox above and behind the driver's helmet.
  add(
    bodyGeometry([
      [-1.1, 0.09, 0.16, 1.01],
      [-0.76, 0.14, 0.17, 1.09],
      [-0.53, 0.145, 0.14, 1.11],
    ]),
    carbon,
  );
  add(
    new THREE.CylinderGeometry(0.1, 0.1, 0.025, 20),
    black,
    [0, 1.11, -0.51],
    [Math.PI / 2, 0, 0],
  );
  box(0.065, 0.55, 0.72, paint, [0, 0.81, -1.28]);
  for (const side of [-1, 1]) {
    // Undercut sidepods, upper intake, highlight and cooling vents.
    add(
      bodyGeometry([
        [-1.43, 0.13, 0.09, 0.37],
        [-1.05, 0.29, 0.16, 0.46],
        [-0.3, 0.31, 0.18, 0.49],
        [0.54, 0.27, 0.17, 0.5],
        [0.68, 0.21, 0.12, 0.48],
      ]),
      paint,
      [side * 0.49, 0, 0],
    );
    box(0.39, 0.12, 0.045, black, [side * 0.5, 0.55, 0.705], [-0.09, 0, 0]);
    box(
      0.43,
      0.028,
      1.06,
      carbon,
      [side * 0.55, 0.672, -0.02],
      [0, side * 0.035, side * -0.05],
    );
    box(
      0.029,
      0.026,
      1.33,
      accent,
      [side * 0.775, 0.52, -0.19],
      [0, side * -0.08, 0],
    );
    for (let v = 0; v < 5; v++)
      box(
        0.26,
        0.009,
        0.034,
        darkMetal,
        [side * 0.52, 0.705, -0.24 - v * 0.105],
        [0, side * -0.2, 0],
        true,
      );
    box(
      0.055,
      0.17,
      0.65,
      carbon,
      [side * 0.78, 0.23, -1.39],
      [0, 0, side * -0.1],
    );
    box(0.035, 0.14, 0.58, carbon, [side * 0.33, 0.22, -1.58], [0.16, 0, 0]);
  }
  // Black cockpit well, padding and a visible driver.
  const cockpit = new THREE.SphereGeometry(1, 24, 12);
  cockpit.scale(0.276, 0.071, 0.445);
  add(cockpit, black, [0, 0.706, 0.015]);
  box(0.17, 0.22, 0.18, black, [0, 0.73, -0.28], [-0.22, 0, 0]);
  const helmet = new THREE.SphereGeometry(0.133, 20, 14);
  helmet.scale(0.9, 1.06, 1.02);
  add(helmet, helmetPaint, [0, 0.9, -0.12]);
  const face = new THREE.SphereGeometry(
    0.135,
    20,
    10,
    0,
    Math.PI,
    Math.PI * 0.34,
    Math.PI * 0.28,
  );
  face.scale(0.91, 1, 1.04);
  add(face, visor, [0, 0.906, -0.115], [0, Math.PI / 2, 0]);
  box(0.14, 0.033, 0.075, darkMetal, [0, 0.79, 0.22], [-0.44, 0, 0]);
  rod([-0.12, 0.78, 0.16], [-0.055, 0.82, 0.23], 0.026, black);
  rod([0.12, 0.78, 0.16], [0.055, 0.82, 0.23], 0.026, black);
  // The halo arches over the driver; front support drops towards the nose.
  tube(
    [
      [-0.29, 0.72, -0.32],
      [-0.3, 1.005, -0.15],
      [-0.25, 1.06, 0.3],
      [0, 1.075, 0.5],
      [0.25, 1.06, 0.3],
      [0.3, 1.005, -0.15],
      [0.29, 0.72, -0.32],
    ],
    0.036,
    carbon,
  );
  rod([0, 1.075, 0.5], [0, 0.68, 0.72], 0.033, carbon);
  tube(
    [
      [-0.3, 0.7, -0.35],
      [-0.29, 0.75, 0.05],
      [-0.23, 0.71, 0.52],
    ],
    0.03,
    paint,
  );
  tube(
    [
      [0.3, 0.7, -0.35],
      [0.29, 0.75, 0.05],
      [0.23, 0.71, 0.52],
    ],
    0.03,
    paint,
  );
  // A pale longitudinal stripe on top of the tapered nose.
  add(
    bodyGeometry([
      [0.82, 0.046, 0.009, 0.566],
      [1.2, 0.041, 0.009, 0.51],
      [1.8, 0.033, 0.009, 0.423],
      [2.04, 0.028, 0.006, 0.35],
    ]),
    accent,
  );
  // Broad front wing with multiple curved planes and rising endplates.
  for (let blade = 0; blade < 3; blade++) {
    const z = 2.18 - blade * 0.145;
    add(
      bodyGeometry([
        [z - 0.1, 1.02, 0.022, 0.19 + blade * 0.035],
        [z + 0.07, 1.1, 0.02, 0.16 + blade * 0.035],
      ]),
      carbon,
    );
    box(2.12, 0.019, 0.032, paint, [0, 0.207 + blade * 0.035, z + 0.065]);
  }
  for (const side of [-1, 1]) {
    box(
      0.045,
      0.23,
      0.56,
      paint,
      [side * 1.08, 0.24, 2.05],
      [0, 0, side * -0.08],
    );
    box(0.07, 0.07, 0.44, carbon, [side * 1.08, 0.12, 2.05]);
    rod([side * 0.12, 0.33, 1.98], [side * 0.23, 0.2, 2.1], 0.025, carbon);
    // Upper and lower wishbones, plus steering rods, at each axle.
    for (const z of [-1.39, 1.39]) {
      for (const y of [0.28, 0.48]) {
        rod([side * 0.27, y, z - 0.29], [side * 0.93, 0.36, z], 0.022, carbon);
        rod([side * 0.27, y, z + 0.26], [side * 0.93, 0.36, z], 0.022, carbon);
      }
      rod([side * 0.3, 0.68, z - 0.17], [side * 0.86, 0.39, z], 0.017, silver);
      rod(
        [side * 0.29, 0.32, z - 0.2],
        [side * 0.94, 0.34, z - 0.075],
        0.013,
        silver,
        true,
      );
      box(
        0.055,
        0.3,
        0.24,
        carbon,
        [side * 0.69, 0.38, z - 0.11],
        [0, 0, side * -0.2],
      );
    }
  }
  // Twin-pylon rear wing, DRS flap, diffuser channels and rain light.
  for (const side of [-1, 1]) {
    box(0.055, 0.65, 0.11, carbon, [side * 0.22, 0.72, -1.81], [-0.12, 0, 0]);
    box(0.045, 0.36, 0.55, paint, [side * 0.79, 1.05, -1.91], [-0.08, 0, 0]);
    box(0.058, 0.026, 0.5, accent, [side * 0.8, 1.245, -1.9]);
    for (let fin = 0; fin < 3; fin++)
      box(
        0.025,
        0.14,
        0.47,
        carbon,
        [side * (0.16 + fin * 0.18), 0.18, -1.72],
        [0.13, 0, 0],
      );
  }
  box(1.59, 0.075, 0.31, paint, [0, 1.075, -1.98], [-0.14, 0, 0]);
  box(1.58, 0.052, 0.19, carbon, [0, 1.2, -1.87], [-0.18, 0, 0]);
  box(1.47, 0.012, 0.027, accent, [0, 1.235, -1.78]);
  box(0.1, 0.09, 0.04, red, [0, 0.31, -1.99]);
  add(
    new THREE.CylinderGeometry(0.045, 0.05, 0.17, 12),
    darkMetal,
    [0, 0.69, -1.74],
    [Math.PI / 2, 0, 0],
  );
  // Simple original seven-segment racing number, positioned on the rear wing.
  const digitSegments = [
    [0, 1, 2, 3, 4, 5],
    [1, 2],
    [0, 1, 6, 4, 3],
    [0, 1, 6, 2, 3],
    [5, 6, 1, 2],
    [0, 5, 6, 2, 3],
    [0, 5, 6, 4, 2, 3],
    [0, 1, 2],
    [0, 1, 2, 3, 4, 5, 6],
    [0, 1, 2, 3, 5, 6],
  ];
  const digit = (Math.abs(id) + 1) % 10;
  const strokes: [number, number, number, number][] = [
    [0, 0.09, 0.09, 0.015],
    [0.045, 0.047, 0.014, 0.075],
    [0.045, -0.047, 0.014, 0.075],
    [0, -0.09, 0.09, 0.015],
    [-0.045, -0.047, 0.014, 0.075],
    [-0.045, 0.047, 0.014, 0.075],
    [0, 0, 0.09, 0.015],
  ];
  for (const segment of digitSegments[digit]) {
    const [x, y, w, h] = strokes[segment];
    box(w, h, 0.004, accent, [x, 1.085 + y, -2.145], [-0.14, 0, 0], true);
  }

  for (const [batch, detail] of [
    [batches, false],
    [detailBatches, true],
  ] as const) {
    for (const [material, geometries] of batch) {
      const geometry = mergeGeometries(geometries, false);
      geometries.forEach((g) => g.dispose());
      if (!geometry) continue;
      const mesh = new THREE.Mesh(geometry, material);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      car.add(mesh);
      if (detail) detailMeshes.push(mesh);
    }
  }

  // Wheels remain independent so the renderer can animate roll and steering.
  const wheels: THREE.Group[] = [];
  for (const z of [-1.39, 1.39]) {
    const width = z < 0 ? 0.43 : 0.34;
    const profile = [
      new THREE.Vector2(0.19, -width / 2),
      new THREE.Vector2(0.292, -width / 2),
      new THREE.Vector2(0.337, -width * 0.45),
      new THREE.Vector2(0.359, -width * 0.31),
      new THREE.Vector2(0.365, -width * 0.17),
      new THREE.Vector2(0.365, width * 0.17),
      new THREE.Vector2(0.359, width * 0.31),
      new THREE.Vector2(0.337, width * 0.45),
      new THREE.Vector2(0.292, width / 2),
      new THREE.Vector2(0.19, width / 2),
    ];
    const tireGeometry = new THREE.LatheGeometry(profile, 32);
    tireGeometry.rotateZ(Math.PI / 2);
    for (const side of [-1, 1]) {
      const wheel = new THREE.Group();
      wheel.position.set(side * 0.94, 0.365, z);
      wheel.userData.front = z > 0;
      wheel.userData.side = side;
      const tire = new THREE.Mesh(tireGeometry, rubber);
      tire.castShadow = true;
      tire.receiveShadow = true;
      wheel.add(tire);
      const rims: THREE.BufferGeometry[] = [];
      const tireDetails: THREE.BufferGeometry[] = [];
      const exteriorX = side * (width / 2 + 0.003);
      const disc = new THREE.CylinderGeometry(0.196, 0.196, width + 0.004, 24);
      disc.rotateZ(Math.PI / 2);
      const rim = new THREE.Mesh(disc, darkMetal);
      wheel.add(rim);
      for (const faceSide of [-1, 1]) {
        const ring = new THREE.TorusGeometry(0.203, 0.014, 6, 28);
        ring.rotateY(Math.PI / 2);
        ring.translate(faceSide * (width / 2 + 0.008), 0, 0);
        rims.push(ring);
      }
      for (let spoke = 0; spoke < 8; spoke++) {
        const angle = (spoke * Math.PI) / 4;
        const spokeGeometry = new THREE.BoxGeometry(0.014, 0.026, 0.153);
        spokeGeometry.rotateX(angle);
        spokeGeometry.translate(
          exteriorX,
          -Math.sin(angle) * 0.103,
          Math.cos(angle) * 0.103,
        );
        rims.push(spokeGeometry);
      }
      const hub = new THREE.CylinderGeometry(0.054, 0.054, 0.034, 12);
      hub.rotateZ(Math.PI / 2);
      hub.translate(exteriorX, 0, 0);
      rims.push(hub);
      const rimGeometry = mergeGeometries(rims, false)!;
      rims.forEach((g) => g.dispose());
      wheel.add(new THREE.Mesh(rimGeometry, silver));
      const sidewallRing = new THREE.TorusGeometry(0.285, 0.006, 4, 40);
      sidewallRing.rotateY(Math.PI / 2);
      sidewallRing.translate(side * (width / 2 + 0.004), 0, 0);
      tireDetails.push(sidewallRing);
      // Five short sidewall dashes suggest molded tire markings without a texture.
      for (let mark = 0; mark < 5; mark++) {
        const angle = 0.24 + mark * 0.115;
        const g = new THREE.BoxGeometry(0.004, 0.025, 0.008);
        g.rotateX(angle);
        g.translate(exteriorX, Math.cos(angle) * 0.3, Math.sin(angle) * 0.3);
        tireDetails.push(g);
      }
      const detailGeometry = mergeGeometries(tireDetails, false)!;
      tireDetails.forEach((g) => g.dispose());
      const marks = new THREE.Mesh(detailGeometry, tireMark);
      wheel.add(marks);
      detailMeshes.push(marks);
      const nut = new THREE.Mesh(
        new THREE.CylinderGeometry(0.023, 0.023, 0.04, 6),
        side > 0 ? red : paint,
      );
      nut.rotation.z = Math.PI / 2;
      nut.position.x = exteriorX;
      wheel.add(nut);
      car.add(wheel);
      wheels.push(wheel);
    }
  }
  car.userData.wheels = wheels;
  car.userData.detailMeshes = detailMeshes;
  car.userData.carId = id;
  return car;
}

export function setCarDetail(group: THREE.Group, quality: Detail): void {
  for (const mesh of (group.userData.detailMeshes ?? []) as THREE.Object3D[])
    mesh.visible = quality !== "low";
}

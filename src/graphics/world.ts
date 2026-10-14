import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { sampleCircuit } from "../circuits/geometry";
import type {
  CameraMode,
  CarState,
  Circuit,
  CircuitGeometry,
  Quality,
  Weather,
} from "../core/types";
import { createFormulaCar, setCarDetail } from "./car";

const PALETTE = [
  "#e84830",
  "#15bca4",
  "#f4bd4e",
  "#4084e5",
  "#e9e8dc",
  "#8e62ce",
  "#e77e25",
  "#367f7e",
  "#cb436a",
  "#b9c5c5",
];
const V = () => new THREE.Vector3();

function seeded(seed: number): () => number {
  return () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) | 0;
    return (seed >>> 0) / 4294967296;
  };
}

function texture(
  kind: "asphalt" | "grass" | "sign" | "checker",
): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = kind === "sign" ? 1024 : 256;
  canvas.height = kind === "sign" ? 128 : 256;
  const ctx = canvas.getContext("2d")!;
  const rand = seeded(97);
  if (kind === "asphalt" || kind === "grass") {
    ctx.fillStyle = kind === "asphalt" ? "#515457" : "#607953";
    ctx.fillRect(0, 0, 256, 256);
    for (let i = 0; i < 26000; i++) {
      const c = Math.floor((kind === "asphalt" ? 60 : 65) + rand() * 55);
      ctx.fillStyle =
        kind === "asphalt"
          ? `rgba(${c},${c + 2},${c + 3},.3)`
          : `rgba(${c - 25},${c + 10},${c - 33},.33)`;
      ctx.fillRect(
        rand() * 256,
        rand() * 256,
        rand() * 2 + 0.4,
        rand() * 2 + 0.4,
      );
    }
  } else if (kind === "sign") {
    ctx.fillStyle = "#142f32";
    ctx.fillRect(0, 0, 1024, 128);
    ctx.fillStyle = "#a9f5d6";
    ctx.font = "italic 900 64px Arial";
    ctx.textAlign = "center";
    ctx.fillText("APEX  /  RACING LAB", 512, 88);
    ctx.fillStyle = "#d5a876";
    ctx.fillRect(0, 0, 1024, 6);
  } else {
    ctx.fillStyle = "#f7f0da";
    ctx.fillRect(0, 0, 256, 256);
    ctx.fillStyle = "#182020";
    for (let x = 0; x < 8; x++)
      for (let y = 0; y < 8; y++)
        if ((x + y) % 2) ctx.fillRect(x * 32, y * 32, 32, 32);
  }
  const map = new THREE.CanvasTexture(canvas);
  map.colorSpace = THREE.SRGBColorSpace;
  map.wrapS = map.wrapT = THREE.RepeatWrapping;
  map.anisotropy = 4;
  return map;
}

function ribbon(
  geometry: CircuitGeometry,
  inner: number,
  outer: number,
  lift: number,
  material: THREE.Material,
  every = 1,
): THREE.Mesh {
  const positions: number[] = [],
    uv: number[] = [],
    indices: number[] = [];
  const n = geometry.samples.length;
  for (let j = 0; j <= n; j++) {
    const s = geometry.samples[j % n];
    for (const offset of [inner, outer]) {
      positions.push(s.x + s.tz * offset, s.y + lift, s.z - s.tx * offset);
      uv.push(
        (offset - inner) / Math.max(outer - inner, 0.001),
        (j === n ? geometry.length : s.distance) / every,
      );
    }
    if (j < n) {
      const k = j * 2;
      indices.push(k, k + 2, k + 1, k + 1, k + 2, k + 3);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(indices);
  g.computeVertexNormals();
  const mesh = new THREE.Mesh(g, material);
  mesh.receiveShadow = true;
  return mesh;
}

function box(
  w: number,
  h: number,
  d: number,
  material: THREE.Material,
  x = 0,
  y = 0,
  z = 0,
): THREE.Mesh {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
  m.position.set(x, y, z);
  m.castShadow = true;
  m.receiveShadow = true;
  return m;
}

function disposeGroup(group: THREE.Object3D): void {
  const geometries = new Set<THREE.BufferGeometry>(),
    materials = new Set<THREE.Material>(),
    maps = new Set<THREE.Texture>();
  group.traverse((o) => {
    if (
      o instanceof THREE.Mesh ||
      o instanceof THREE.LineSegments ||
      o instanceof THREE.Points
    ) {
      geometries.add(o.geometry);
      for (const material of Array.isArray(o.material)
        ? o.material
        : [o.material]) {
        materials.add(material);
        for (const value of Object.values(material))
          if (value instanceof THREE.Texture) maps.add(value);
      }
    }
  });
  geometries.forEach((g) => g.dispose());
  materials.forEach((m) => m.dispose());
  maps.forEach((t) => t.dispose());
}

/** Procedural racing world: every asset is generated locally and requires no downloads. */
export class RacingWorld {
  readonly renderer: THREE.WebGLRenderer;
  private readonly container: HTMLElement;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(43, 1, 0.15, 3000);
  private readonly sun = new THREE.DirectionalLight("#fff0d3", 3.1);
  private readonly ambient = new THREE.HemisphereLight("#c5ddeb", "#4c5144", 2);
  private readonly circuitRoot = new THREE.Group();
  private readonly carsRoot = new THREE.Group();
  private readonly rain: THREE.Points;
  private readonly environment: THREE.WebGLRenderTarget;
  private readonly carModels: THREE.Group[] = [];
  private readonly target = V();
  private readonly desired = V();
  private readonly center = V();
  private geometry: CircuitGeometry = { samples: [], length: 0 };
  private radius = 180;
  private quality: Quality = "high";
  private scale = 1;
  private weather: Weather = "sun";
  private asphalt?: THREE.MeshStandardMaterial;
  private lampMaterial?: THREE.MeshStandardMaterial;
  private readonly nightLights = new THREE.Group();
  private trees?: THREE.InstancedMesh;
  private lastCamera: CameraMode = "orbit";
  private lastTime = 0;

  constructor(container: HTMLElement) {
    this.container = container;
    this.renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: false,
      powerPreference: "high-performance",
    });
    this.renderer.setClearColor("#b4ccce");
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.08;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    const reflectionRoom = new RoomEnvironment();
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.environment = pmrem.fromScene(reflectionRoom, 0.04);
    this.scene.environment = this.environment.texture;
    this.scene.environmentIntensity = 0.55;
    reflectionRoom.dispose();
    pmrem.dispose();
    this.renderer.domElement.setAttribute(
      "aria-label",
      "Simulação tridimensional do circuito",
    );
    container.appendChild(this.renderer.domElement);
    this.scene.background = new THREE.Color("#becfd0");
    this.scene.fog = new THREE.FogExp2("#becfd0", 0.00092);
    this.sun.position.set(-200, 310, 150);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    this.sun.shadow.bias = -0.00012;
    this.sun.shadow.normalBias = 0.25;
    this.sun.shadow.camera.near = 5;
    this.sun.shadow.camera.far = 1000;
    this.scene.add(
      this.ambient,
      this.sun,
      this.sun.target,
      this.circuitRoot,
      this.carsRoot,
    );
    const rainPositions = new Float32Array(2000 * 3),
      rand = seeded(3);
    for (let i = 0; i < rainPositions.length; i += 3) {
      rainPositions[i] = (rand() - 0.5) * 200;
      rainPositions[i + 1] = rand() * 90;
      rainPositions[i + 2] = (rand() - 0.5) * 200;
    }
    const rainGeometry = new THREE.BufferGeometry();
    rainGeometry.setAttribute(
      "position",
      new THREE.BufferAttribute(rainPositions, 3),
    );
    this.rain = new THREE.Points(
      rainGeometry,
      new THREE.PointsMaterial({
        color: "#c6d9e6",
        size: 0.2,
        transparent: true,
        opacity: 0.4,
        depthWrite: false,
      }),
    );
    this.rain.visible = false;
    this.rain.frustumCulled = false;
    this.scene.add(this.rain);
    for (let i = 0; i < 10; i++) {
      const car = createFormulaCar(PALETTE[i], i);
      this.carModels.push(car);
      this.carsRoot.add(car);
    }
    this.resize();
  }

  setCircuit(circuit: Circuit): void {
    this.nightLights.clear();
    disposeGroup(this.circuitRoot);
    this.circuitRoot.clear();
    this.geometry = sampleCircuit(circuit);
    if (!this.geometry.samples.length) return;
    const bounds = new THREE.Box3();
    this.geometry.samples.forEach((s) =>
      bounds.expandByPoint(new THREE.Vector3(s.x, s.y, s.z)),
    );
    bounds.getCenter(this.center);
    const size = bounds.getSize(V());
    this.radius = Math.max(size.x, size.z) * 0.57;
    const half = circuit.width / 2;
    const asphaltMap = texture("asphalt");
    this.asphalt = new THREE.MeshStandardMaterial({
      color: "#8c9191",
      map: asphaltMap,
      roughness: 0.92,
      metalness: 0.04,
      side: THREE.DoubleSide,
    });
    const gravel = new THREE.MeshStandardMaterial({
      color: "#b6ac89",
      roughness: 1,
      side: THREE.DoubleSide,
    });
    const white = new THREE.MeshStandardMaterial({
      color: "#eee5ce",
      roughness: 0.82,
      side: THREE.DoubleSide,
    });
    const red = new THREE.MeshStandardMaterial({
      color: "#ae4439",
      roughness: 0.85,
      side: THREE.DoubleSide,
    });
    const grassMap = texture("grass");
    grassMap.repeat.set(90, 90);
    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(2400, 2400),
      new THREE.MeshStandardMaterial({
        color: "#c0c8a0",
        map: grassMap,
        roughness: 1,
      }),
    );
    ground.rotation.x = -Math.PI / 2;
    ground.position.set(this.center.x, -0.11, this.center.z);
    ground.receiveShadow = true;
    this.circuitRoot.add(ground);
    // Broad grass banks connect the elevated ribbon continuously to the terrain.
    const bankPositions: number[] = [],
      bankUVs: number[] = [],
      bankIndices: number[] = [];
    for (const side of [-1, 1]) {
      const base = bankPositions.length / 3;
      for (let i = 0; i <= this.geometry.samples.length; i++) {
        const s = this.geometry.samples[i % this.geometry.samples.length];
        for (const edge of [0, 1]) {
          const offset = (half + (edge ? 19 : 1.6)) * side;
          bankPositions.push(
            s.x + s.tz * offset,
            edge ? -0.09 : s.y - 0.045,
            s.z - s.tx * offset,
          );
          bankUVs.push(edge * 3, s.distance / 8);
        }
        if (i < this.geometry.samples.length) {
          const k = base + i * 2;
          bankIndices.push(k, k + 1, k + 2, k + 1, k + 3, k + 2);
        }
      }
    }
    const bankGeometry = new THREE.BufferGeometry();
    bankGeometry.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(bankPositions, 3),
    );
    bankGeometry.setAttribute(
      "uv",
      new THREE.Float32BufferAttribute(bankUVs, 2),
    );
    bankGeometry.setIndex(bankIndices);
    bankGeometry.computeVertexNormals();
    const bankMap = texture("grass");
    const bank = new THREE.Mesh(
      bankGeometry,
      new THREE.MeshStandardMaterial({
        map: bankMap,
        color: "#c0c8a0",
        roughness: 1,
        side: THREE.DoubleSide,
      }),
    );
    bank.receiveShadow = true;
    this.circuitRoot.add(bank);
    this.circuitRoot.add(
      ribbon(this.geometry, -half - 1.7, half + 1.7, -0.035, gravel, 5),
    );
    this.circuitRoot.add(
      ribbon(this.geometry, -half, half, 0.005, this.asphalt, 8),
    );
    for (const sign of [-1, 1]) {
      this.circuitRoot.add(
        ribbon(
          this.geometry,
          sign * half - 0.11,
          sign * half + 0.11,
          0.02,
          white,
          4,
        ),
      );
      const curbPositions: number[] = [],
        curbColors: number[] = [],
        curbIndices: number[] = [];
      const colors = [new THREE.Color("#e8e0cf"), new THREE.Color("#af4035")];
      const samples = this.geometry.samples;
      for (let i = 0; i < samples.length; i++) {
        const a = samples[i],
          b = samples[(i + 1) % samples.length];
        const color = colors[Math.floor(a.distance / 3.1) % 2],
          k = curbPositions.length / 3;
        for (const s of [a, b])
          for (const o of [half + 0.18, half + 1.12]) {
            curbPositions.push(
              s.x + s.tz * o * sign,
              s.y + 0.045,
              s.z - s.tx * o * sign,
            );
            curbColors.push(color.r, color.g, color.b);
          }
        curbIndices.push(k, k + 2, k + 1, k + 1, k + 2, k + 3);
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute(
        "position",
        new THREE.Float32BufferAttribute(curbPositions, 3),
      );
      g.setAttribute("color", new THREE.Float32BufferAttribute(curbColors, 3));
      g.setIndex(curbIndices);
      g.computeVertexNormals();
      const curb = new THREE.Mesh(
        g,
        new THREE.MeshStandardMaterial({
          vertexColors: true,
          roughness: 0.8,
          side: THREE.DoubleSide,
        }),
      );
      curb.receiveShadow = true;
      this.circuitRoot.add(curb);
    }
    const rubber = new THREE.MeshStandardMaterial({
      color: "#292e2e",
      roughness: 0.93,
      transparent: true,
      opacity: 0.19,
      depthWrite: false,
      side: THREE.DoubleSide,
    });
    for (const side of [-2, 2])
      this.circuitRoot.add(
        ribbon(this.geometry, side - 0.42, side + 0.42, 0.027, rubber, 5),
      );
    this.buildFurniture(half, red, white);
    this.buildScenery(half);
    this.sun.target.position.copy(this.center);
    this.sun.position
      .copy(this.center)
      .add(new THREE.Vector3(-this.radius, this.radius * 1.8, this.radius));
    const cam = this.sun.shadow.camera;
    cam.left = cam.bottom = -this.radius * 1.2;
    cam.right = cam.top = this.radius * 1.2;
    cam.updateProjectionMatrix();
    this.setWeather(this.weather);
    this.setQuality(this.quality, this.scale);
  }

  private buildFurniture(
    half: number,
    red: THREE.Material,
    white: THREE.Material,
  ): void {
    const steel = new THREE.MeshStandardMaterial({
      color: "#bdc4bf",
      roughness: 0.55,
      metalness: 0.65,
    });
    const dark = new THREE.MeshStandardMaterial({
      color: "#263c3e",
      roughness: 0.7,
    });
    const dummy = new THREE.Object3D();
    const posts = new THREE.InstancedMesh(
      new THREE.CylinderGeometry(0.09, 0.13, 2.7, 5),
      steel,
      300,
    );
    let postCount = 0;
    for (const side of [-1, 1]) {
      const positions: number[] = [];
      const ss = this.geometry.samples;
      for (let i = 0; i < ss.length; i++) {
        const a = ss[i],
          b = ss[(i + 1) % ss.length],
          offset = (half + 6.5) * side;
        for (const height of [0.48, 0.86, 2.2])
          positions.push(
            a.x + a.tz * offset,
            a.y + height,
            a.z - a.tx * offset,
            b.x + b.tz * offset,
            b.y + height,
            b.z - b.tx * offset,
          );
        if (i % 7 === 0 && postCount < 300) {
          dummy.position.set(
            a.x + a.tz * offset,
            a.y + 1.2,
            a.z - a.tx * offset,
          );
          dummy.updateMatrix();
          posts.setMatrixAt(postCount++, dummy.matrix);
        }
      }
      const railGeometry = new THREE.BufferGeometry();
      railGeometry.setAttribute(
        "position",
        new THREE.Float32BufferAttribute(positions, 3),
      );
      this.circuitRoot.add(
        new THREE.LineSegments(
          railGeometry,
          new THREE.LineBasicMaterial({
            color: "#a4aca6",
            transparent: true,
            opacity: 0.85,
          }),
        ),
      );
    }
    posts.count = postCount;
    posts.castShadow = true;
    this.circuitRoot.add(posts);
    const start = this.geometry.samples[0],
      startGroup = new THREE.Group();
    startGroup.position.set(start.x, start.y, start.z);
    startGroup.rotation.y = Math.atan2(start.tx, start.tz);
    const line = box(
      half * 2 - 0.4,
      0.015,
      1.5,
      new THREE.MeshStandardMaterial({
        map: texture("checker"),
        roughness: 0.7,
      }),
      0,
      0.045,
      0,
    );
    startGroup.add(line);
    for (let i = 0; i < 12; i++) {
      const side = i % 2 ? 1 : -1;
      const z = 8 + Math.floor(i / 2) * 8;
      startGroup.add(
        box(2.8, 0.02, 0.13, white, side * 3, 0.04, z),
        box(0.12, 0.02, 1.7, white, side * 3 - 1.3, 0.04, z + 0.8),
      );
    }
    startGroup.add(
      box(0.5, 8, 0.5, steel, -half - 2, 4, -4),
      box(0.5, 8, 0.5, steel, half + 2, 4, -4),
    );
    startGroup.add(box(half * 2 + 4.5, 1.5, 0.65, dark, 0, 7.3, -4));
    const sign = new THREE.Mesh(
      new THREE.PlaneGeometry(half * 1.7, 1.1),
      new THREE.MeshStandardMaterial({
        map: texture("sign"),
        side: THREE.DoubleSide,
        roughness: 0.7,
      }),
    );
    sign.position.set(0, 7.3, -3.65);
    startGroup.add(sign);
    for (let i = 0; i < 5; i++)
      startGroup.add(box(0.23, 0.23, 0.12, red, (i - 2) * 0.6, 6.25, -3.6));
    this.circuitRoot.add(startGroup);
    this.buildLighting(half, steel, dark);
    this.buildGrandstand(start, half);
    const pit = new THREE.Group();
    pit.position.set(
      start.x - start.tz * (half + 21),
      start.y,
      start.z + start.tx * (half + 21),
    );
    pit.rotation.y = startGroup.rotation.y;
    const glass = new THREE.MeshStandardMaterial({
      color: "#526f77",
      metalness: 0.55,
      roughness: 0.2,
    });
    pit.add(
      box(17, 5, 85, white, 0, 2.5, -15),
      box(18, 0.5, 88, dark, 0, 5.2, -15),
      box(13, 3.7, 81, glass, -1, 7.2, -15),
      box(18, 0.35, 88, dark, 0, 9.3, -15),
    );
    for (let i = 0; i < 12; i++) {
      pit.add(box(0.1, 4.2, 5.2, dark, 8.6, 2.1, -51 + i * 6.5));
      pit.add(
        box(
          0.15,
          0.48,
          5.6,
          new THREE.MeshStandardMaterial({
            color: PALETTE[i % 10],
            roughness: 0.7,
          }),
          8.7,
          4.3,
          -51 + i * 6.5,
        ),
      );
    }
    this.circuitRoot.add(pit);
  }

  private buildLighting(
    half: number,
    steel: THREE.Material,
    dark: THREE.Material,
  ): void {
    this.lampMaterial = new THREE.MeshStandardMaterial({
      color: "#fff7d7",
      emissive: "#d5eaff",
      emissiveIntensity: 0.15,
      roughness: 0.35,
    });
    for (let i = 0; i < 20; i++) {
      const sample =
        this.geometry.samples[
          Math.floor((i / 20) * this.geometry.samples.length)
        ];
      const side = i % 2 ? -1 : 1;
      const tower = new THREE.Group();
      tower.position.set(
        sample.x + sample.tz * (half + 9) * side,
        sample.y,
        sample.z - sample.tx * (half + 9) * side,
      );
      tower.rotation.y = Math.atan2(sample.tx, sample.tz);
      tower.add(
        box(0.22, 14, 0.22, steel, 0, 7, 0),
        box(3.6, 0.16, 0.16, steel, -side * 1.6, 14, 0),
      );
      for (let bulb = 0; bulb < 3; bulb++) {
        tower.add(
          box(0.68, 0.22, 0.5, dark, -side * (bulb + 1), 13.9, 0),
          box(
            0.55,
            0.025,
            0.4,
            this.lampMaterial,
            -side * (bulb + 1),
            13.76,
            0,
          ),
        );
      }
      this.circuitRoot.add(tower);
      if (i % 5 === 0) {
        const light = new THREE.SpotLight("#d4e6ff", 1700, 65, 0.75, 0.65, 1.5);
        light.position.set(tower.position.x, sample.y + 13.5, tower.position.z);
        light.target.position.set(sample.x, sample.y, sample.z);
        this.nightLights.add(light, light.target);
      }
    }
    this.circuitRoot.add(this.nightLights);
  }

  private buildGrandstand(
    start: CircuitGeometry["samples"][number],
    half: number,
  ): void {
    const group = new THREE.Group();
    group.position.set(
      start.x + start.tz * (half + 17),
      start.y,
      start.z - start.tx * (half + 17),
    );
    group.rotation.y = Math.atan2(start.tx, start.tz);
    const concrete = new THREE.MeshStandardMaterial({
      color: "#989c90",
      roughness: 0.95,
    });
    const dark = new THREE.MeshStandardMaterial({
      color: "#334447",
      roughness: 0.6,
      metalness: 0.35,
    });
    for (let row = 0; row < 9; row++)
      group.add(
        box(
          1.8,
          0.6 + row * 0.62,
          80,
          concrete,
          row * 1.5,
          (0.6 + row * 0.62) / 2,
          0,
        ),
      );
    const rand = seeded(81),
      dummy = new THREE.Object3D();
    const seats = new THREE.InstancedMesh(
      new THREE.BoxGeometry(0.75, 0.45, 0.8),
      new THREE.MeshStandardMaterial({ color: "#f7efdd", roughness: 0.8 }),
      450,
    );
    const people = new THREE.InstancedMesh(
      new THREE.SphereGeometry(0.23, 5, 4),
      new THREE.MeshStandardMaterial({ color: "#565149", roughness: 1 }),
      380,
    );
    let peopleIndex = 0;
    for (let row = 0; row < 9; row++)
      for (let col = 0; col < 50; col++) {
        dummy.position.set(row * 1.5, 0.85 + row * 0.62, (col - 25) * 1.5);
        dummy.updateMatrix();
        seats.setMatrixAt(row * 50 + col, dummy.matrix);
        seats.setColorAt(
          row * 50 + col,
          new THREE.Color(row % 3 ? "#b9c1ae" : "#b86c4a"),
        );
        if (rand() > 0.19 && peopleIndex < 380) {
          dummy.position.y += 0.5;
          dummy.scale.set(0.85, 1.4, 0.85);
          dummy.updateMatrix();
          people.setMatrixAt(peopleIndex, dummy.matrix);
          people.setColorAt(
            peopleIndex++,
            new THREE.Color(PALETTE[Math.floor(rand() * PALETTE.length)]),
          );
          dummy.scale.setScalar(1);
        }
      }
    people.count = peopleIndex;
    group.add(seats, people);
    for (let i = 0; i < 6; i++)
      group.add(box(0.35, 12, 0.35, dark, 13.7, 6, (i - 2.5) * 15));
    const roof = box(20, 0.28, 88, dark, 6, 12, 0);
    roof.rotation.z = -0.045;
    group.add(roof);
    group.add(
      box(
        0.25,
        1.6,
        88,
        new THREE.MeshStandardMaterial({ color: "#b9d7cf", roughness: 0.5 }),
        -3.9,
        11.3,
        0,
      ),
    );
    this.circuitRoot.add(group);
  }

  private buildScenery(half: number): void {
    const rand = seeded(55),
      dummy = new THREE.Object3D();
    const crownGeo = new THREE.IcosahedronGeometry(1, 1);
    this.trees = new THREE.InstancedMesh(
      crownGeo,
      new THREE.MeshStandardMaterial({
        color: "#45684b",
        roughness: 1,
        flatShading: true,
      }),
      320,
    );
    const trunks = new THREE.InstancedMesh(
      new THREE.CylinderGeometry(0.22, 0.4, 1, 5),
      new THREE.MeshStandardMaterial({ color: "#665340", roughness: 1 }),
      320,
    );
    const accepted: { x: number; z: number }[] = [];
    let count = 0;
    for (let tries = 0; tries < 4500 && count < 320; tries++) {
      const x = this.center.x + (rand() - 0.5) * this.radius * 3.4,
        z = this.center.z + (rand() - 0.5) * this.radius * 3.4;
      let distance = Infinity;
      for (let i = 0; i < this.geometry.samples.length; i += 4) {
        const s = this.geometry.samples[i];
        distance = Math.min(distance, Math.hypot(s.x - x, s.z - z));
      }
      if (
        distance < half + 23 ||
        accepted.some((p) => Math.hypot(p.x - x, p.z - z) < 8)
      )
        continue;
      const h = 7 + rand() * 7;
      accepted.push({ x, z });
      dummy.position.set(x, h * 0.61, z);
      dummy.scale.set(h * 0.4, h * 0.54, h * 0.4);
      dummy.rotation.y = rand() * 6.28;
      dummy.updateMatrix();
      this.trees.setMatrixAt(count, dummy.matrix);
      this.trees.setColorAt(
        count,
        new THREE.Color().setHSL(
          0.27 + rand() * 0.06,
          0.16 + rand() * 0.13,
          0.22 + rand() * 0.12,
        ),
      );
      dummy.position.y = h * 0.2;
      dummy.scale.set(1, h * 0.4, 1);
      dummy.rotation.set(0, 0, 0);
      dummy.updateMatrix();
      trunks.setMatrixAt(count, dummy.matrix);
      count++;
    }
    this.trees.count = trunks.count = count;
    this.trees.userData.fullCount = count;
    this.trees.castShadow = true;
    trunks.castShadow = true;
    this.circuitRoot.add(this.trees, trunks);
    const hills = new THREE.InstancedMesh(
      new THREE.SphereGeometry(1, 16, 8),
      new THREE.MeshStandardMaterial({ color: "#819789", roughness: 1 }),
      12,
    );
    for (let i = 0; i < 12; i++) {
      const a = (i / 12) * Math.PI * 2;
      dummy.position.set(
        this.center.x + Math.cos(a) * (this.radius + 600),
        -24,
        this.center.z + Math.sin(a) * (this.radius + 600),
      );
      dummy.scale.set(
        180 + rand() * 200,
        50 + rand() * 110,
        160 + rand() * 100,
      );
      dummy.updateMatrix();
      hills.setMatrixAt(i, dummy.matrix);
    }
    this.circuitRoot.add(hills);
  }

  setQuality(quality: Quality, scale = 1): void {
    this.quality = quality;
    this.scale = scale;
    const ratio =
      Math.min(
        window.devicePixelRatio || 1,
        quality === "high" ? 2 : quality === "medium" ? 1.5 : 1,
      ) * scale;
    this.renderer.setPixelRatio(Math.max(0.4, ratio));
    this.renderer.shadowMap.enabled = quality !== "low";
    const shadow = quality === "high" ? 2048 : 1024;
    if (this.sun.shadow.mapSize.x !== shadow) {
      this.sun.shadow.map?.dispose();
      this.sun.shadow.map = null;
      this.sun.shadow.mapSize.set(shadow, shadow);
    }
    if (this.trees)
      this.trees.count = Math.floor(
        Number(this.trees.userData.fullCount) *
          (quality === "low" ? 0.45 : quality === "medium" ? 0.7 : 1),
      );
    this.carModels.forEach((car) => setCarDetail(car, quality));
    this.resize();
  }

  setWeather(weather: Weather): void {
    this.weather = weather;
    const night = weather === "night",
      rain = weather === "rain";
    const color = night ? "#101c2c" : rain ? "#8eabb4" : "#becfd0";
    this.scene.background = new THREE.Color(color);
    this.renderer.setClearColor(color);
    this.scene.fog = new THREE.FogExp2(
      color,
      night ? 0.002 : rain ? 0.0024 : 0.00092,
    );
    this.sun.color.set(night ? "#9fc5ef" : rain ? "#d4e2e7" : "#fff0d3");
    this.sun.intensity = night ? 1.2 : rain ? 1.5 : 3.1;
    this.ambient.intensity = night ? 0.75 : rain ? 1.5 : 2;
    this.renderer.toneMappingExposure = night ? 0.95 : 1.08;
    this.nightLights.visible = night || rain;
    if (this.lampMaterial)
      this.lampMaterial.emissiveIntensity = night ? 5 : rain ? 1.3 : 0.15;
    if (this.asphalt) {
      this.asphalt.roughness = rain ? 0.23 : 0.92;
      this.asphalt.metalness = rain ? 0.22 : 0.04;
      this.asphalt.color.set(rain ? "#68787c" : "#8c9191");
    }
    this.rain.visible = rain;
  }

  resize(): void {
    const width = Math.max(1, this.container.clientWidth),
      height = Math.max(1, this.container.clientHeight);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
  }

  render(
    cars: CarState[] | undefined,
    elapsed: number,
    mode: CameraMode,
  ): void {
    const dt = Math.min(0.1, Math.max(0.001, elapsed - this.lastTime));
    this.lastTime = elapsed;
    const samples = this.geometry.samples;
    if (!samples.length) {
      this.renderer.render(this.scene, this.camera);
      return;
    }
    for (let i = 0; i < this.carModels.length; i++) {
      const mesh = this.carModels[i],
        state = cars?.[i];
      mesh.visible = cars ? !!state : true;
      if (state) {
        mesh.position.set(state.x, state.y + 0.04, state.z);
        mesh.rotation.y = state.heading;
      } else if (!cars) {
        const d =
            (((elapsed * 38 + i * 19) % this.geometry.length) /
              this.geometry.length) *
            samples.length,
          index = Math.floor(d),
          fraction = d - index;
        const a = samples[index % samples.length],
          b = samples[(index + 1) % samples.length],
          offset = i % 2 ? 1.7 : -1.7;
        mesh.position.set(
          THREE.MathUtils.lerp(a.x, b.x, fraction) + a.tz * offset,
          THREE.MathUtils.lerp(a.y, b.y, fraction) + 0.04,
          THREE.MathUtils.lerp(a.z, b.z, fraction) - a.tx * offset,
        );
        mesh.rotation.y = Math.atan2(a.tx, a.tz);
      }
      if (mesh.visible && mesh.userData.wheels)
        for (const wheel of mesh.userData.wheels as THREE.Object3D[])
          wheel.rotation.x -= ((state?.speed ?? 38) * dt) / 0.36;
    }
    const player = this.carModels[0],
      angle = player.rotation.y,
      forward = new THREE.Vector3(Math.sin(angle), 0, Math.cos(angle));
    if (mode === "orbit") {
      if (this.container.closest(".editor-preview")) {
        const a = -0.78 + elapsed * 0.008;
        this.desired.set(
          this.center.x + Math.sin(a) * this.radius * 1.24,
          this.center.y + this.radius * 0.91,
          this.center.z + Math.cos(a) * this.radius * 1.24,
        );
        this.target.copy(this.center).add(new THREE.Vector3(0, -3, 0));
      } else {
        // A moving trackside crane shot keeps the car's aero and surrounding circuit legible.
        const side = new THREE.Vector3(forward.z, 0, -forward.x);
        this.desired
          .copy(player.position)
          .addScaledVector(forward, 12)
          .addScaledVector(side, 14)
          .add(new THREE.Vector3(0, 7.2, 0));
        this.target
          .copy(player.position)
          .addScaledVector(forward, -1.5)
          .addScaledVector(side, -3)
          .add(new THREE.Vector3(0, 0.6, 0));
      }
    } else if (mode === "broadcast") {
      this.desired
        .copy(player.position)
        .addScaledVector(forward, 25)
        .add(new THREE.Vector3(55, 28, 40));
      this.target.copy(player.position).addScaledVector(forward, 7);
    } else if (mode === "cockpit") {
      this.desired
        .copy(player.position)
        .addScaledVector(forward, -0.38)
        .add(new THREE.Vector3(0, 1.04, 0));
      this.target
        .copy(player.position)
        .addScaledVector(forward, 50)
        .add(new THREE.Vector3(0, 1.03, 0));
    } else if (mode === "rear") {
      this.desired
        .copy(player.position)
        .addScaledVector(forward, 7.8)
        .add(new THREE.Vector3(0, 2.8, 0));
      this.target
        .copy(player.position)
        .addScaledVector(forward, -8)
        .add(new THREE.Vector3(0, 0.5, 0));
    } else {
      this.desired
        .copy(player.position)
        .addScaledVector(forward, -9.5)
        .add(new THREE.Vector3(0, 3.8, 0));
      this.target
        .copy(player.position)
        .addScaledVector(forward, 8)
        .add(new THREE.Vector3(0, 0.55, 0));
    }
    if (
      mode !== this.lastCamera ||
      mode === "cockpit" ||
      this.camera.position.length() < 1
    )
      this.camera.position.copy(this.desired);
    else
      this.camera.position.lerp(
        this.desired,
        mode === "orbit" ? 0.04 : 1 - Math.exp(-dt * 6),
      );
    this.lastCamera = mode;
    this.camera.lookAt(this.target);
    if (this.rain.visible) {
      this.rain.position.copy(player.position);
      this.rain.position.y -= (elapsed * 37) % 50;
    }
    this.renderer.render(this.scene, this.camera);
  }

  async warmup(): Promise<void> {
    await this.renderer.compileAsync(this.scene, this.camera);
  }

  dispose(): void {
    disposeGroup(this.scene);
    this.environment.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}

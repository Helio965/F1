import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { sampleCircuit } from "../circuits/geometry";
import type { CameraMode, CarState, Circuit, Quality, Weather } from "../core/types";
import {
  layoutFromAnalysis,
  layoutFromGeometry,
  surfaceHeight,
  SURFACE_GRAVEL,
  SURFACE_RUNOFF_ASPHALT,
  type LayoutSample,
  type TrackAnalysis,
  type TrackLayout,
} from "../physics/analysis";
import type { SurfaceGrid } from "../physics/protocol";
import { createFormulaCar, fitCarToVehicle, setCarDetail, type CarFit } from "./car";

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
type Offset = number | ((s: LayoutSample) => number);
const offsetOf = (o: Offset, s: LayoutSample) => (typeof o === "number" ? o : o(s));

function seeded(seed: number): () => number {
  return () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) | 0;
    return (seed >>> 0) / 4294967296;
  };
}

function texture(kind: "asphalt" | "grass" | "sign" | "checker"): THREE.CanvasTexture {
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
      ctx.fillRect(rand() * 256, rand() * 256, rand() * 2 + 0.4, rand() * 2 + 0.4);
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
      for (let y = 0; y < 8; y++) if ((x + y) % 2) ctx.fillRect(x * 32, y * 32, 32, 32);
  }
  const map = new THREE.CanvasTexture(canvas);
  map.colorSpace = THREE.SRGBColorSpace;
  map.wrapS = map.wrapT = THREE.RepeatWrapping;
  map.anisotropy = 4;
  return map;
}

/**
 * A strip along the track between two lateral offsets (left positive), lying
 * on the banked surface. ``wetUv`` adds (lateral fraction, lap fraction)
 * coordinates used to sample the physics water grid.
 */
function ribbon(
  layout: TrackLayout,
  inner: Offset,
  outer: Offset,
  lift: number,
  material: THREE.Material,
  every = 1,
  options: { wetUv?: boolean; colors?: (s: LayoutSample) => THREE.Color | null } = {},
): THREE.Mesh {
  const positions: number[] = [],
    uv: number[] = [],
    wetUv: number[] = [],
    colors: number[] = [],
    indices: number[] = [];
  const n = layout.samples.length;
  for (let j = 0; j <= n; j++) {
    const s = layout.samples[j % n];
    const a = offsetOf(inner, s),
      b = offsetOf(outer, s);
    const color = options.colors?.(s);
    for (const offset of [a, b]) {
      positions.push(s.x + s.tz * offset, surfaceHeight(s, offset) + lift, s.z - s.tx * offset);
      uv.push((offset - a) / Math.max(b - a, 0.001), (j === n ? layout.length : s.distance) / every);
      if (options.wetUv)
        wetUv.push((offset + s.half) / (2 * s.half), (j === n ? layout.length : s.distance) / layout.length);
      if (color) colors.push(color.r, color.g, color.b);
    }
    if (j < n) {
      const k = j * 2;
      indices.push(k, k + 2, k + 1, k + 1, k + 2, k + 3);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
  if (options.wetUv) g.setAttribute("wetUv", new THREE.Float32BufferAttribute(wetUv, 2));
  if (colors.length) g.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  g.setIndex(indices);
  g.computeVertexNormals();
  const mesh = new THREE.Mesh(g, material);
  mesh.receiveShadow = true;
  return mesh;
}

function box(w: number, h: number, d: number, material: THREE.Material, x = 0, y = 0, z = 0): THREE.Mesh {
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
    if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments || o instanceof THREE.Points) {
      geometries.add(o.geometry);
      const owned: THREE.Material[] = Array.isArray(o.material) ? [...o.material] : [o.material];
      const full = o.userData.fullMaterials as THREE.Material[] | undefined;
      const low = o.userData.lowMaterials as THREE.Material[] | undefined;
      if (full) owned.push(...full);
      if (low) owned.push(...low);
      for (const material of owned) {
        materials.add(material);
        for (const value of Object.values(material)) if (value instanceof THREE.Texture) maps.add(value);
      }
    }
  });
  geometries.forEach((g) => g.dispose());
  materials.forEach((m) => m.dispose());
  maps.forEach((t) => t.dispose());
}

/** Shared uniforms connecting the physics water grid to the asphalt shaders. */
interface WetUniforms {
  wetMap: { value: THREE.DataTexture };
  wetEnabled: { value: number };
}

/**
 * Inject the water grid into a material: darker albedo and, for PBR
 * materials, lower roughness (reflective film); deep water becomes a mirror.
 * The appearance therefore follows the same data that sets tyre grip.
 */
function applyWetShader(material: THREE.Material, uniforms: WetUniforms, pbr: boolean): void {
  material.onBeforeCompile = (shader) => {
    shader.uniforms.wetMap = uniforms.wetMap;
    shader.uniforms.wetEnabled = uniforms.wetEnabled;
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute vec2 wetUv;\nvarying vec2 vWetUv;")
      .replace("#include <uv_vertex>", "#include <uv_vertex>\nvWetUv = wetUv;");
    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <common>",
        "#include <common>\nuniform sampler2D wetMap;\nuniform float wetEnabled;\nvarying vec2 vWetUv;",
      )
      .replace(
        "#include <map_fragment>",
        "#include <map_fragment>\nvec4 wetSample = texture2D(wetMap, vWetUv) * wetEnabled;\ndiffuseColor.rgb *= mix(1.0, 0.42, wetSample.r);",
      );
    if (pbr)
      shader.fragmentShader = shader.fragmentShader.replace(
        "#include <roughnessmap_fragment>",
        "#include <roughnessmap_fragment>\nroughnessFactor = mix(roughnessFactor, 0.16, wetSample.r);\nroughnessFactor = mix(roughnessFactor, 0.03, wetSample.g);",
      );
  };
  material.customProgramCacheKey = () => (pbr ? "apex-wet-pbr" : "apex-wet-lambert");
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
  private readonly spray: THREE.Points;
  private readonly environment: THREE.WebGLRenderTarget;
  private readonly carModels: THREE.Group[] = [];
  private readonly target = V();
  private readonly desired = V();
  private readonly center = V();
  private layout: TrackLayout = { samples: [], length: 0, analysed: false };
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
  private readonly wet: WetUniforms;
  private hasSurface = false;

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
    this.renderer.domElement.setAttribute("aria-label", "Simulação tridimensional do circuito");
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
    this.scene.add(this.ambient, this.sun, this.sun.target, this.circuitRoot, this.carsRoot);
    const rainPositions = new Float32Array(2000 * 3),
      rand = seeded(3);
    for (let i = 0; i < rainPositions.length; i += 3) {
      rainPositions[i] = (rand() - 0.5) * 200;
      rainPositions[i + 1] = rand() * 90;
      rainPositions[i + 2] = (rand() - 0.5) * 200;
    }
    const rainGeometry = new THREE.BufferGeometry();
    rainGeometry.setAttribute("position", new THREE.BufferAttribute(rainPositions, 3));
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
    // Spray behind cars on wet asphalt: intensity follows the physics water film.
    const sprayGeometry = new THREE.BufferGeometry();
    sprayGeometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(10 * 40 * 3), 3));
    this.spray = new THREE.Points(
      sprayGeometry,
      new THREE.PointsMaterial({
        color: "#dbe6ec",
        size: 0.55,
        transparent: true,
        opacity: 0.32,
        depthWrite: false,
      }),
    );
    this.spray.frustumCulled = false;
    this.spray.visible = false;
    this.scene.add(this.spray);
    const wetTexture = new THREE.DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1);
    wetTexture.needsUpdate = true;
    this.wet = { wetMap: { value: wetTexture }, wetEnabled: { value: 0 } };
    for (let i = 0; i < 10; i++) {
      const car = createFormulaCar(PALETTE[i], i);
      this.carModels.push(car);
      this.carsRoot.add(car);
    }
    this.resize();
  }

  /** Fit every car model to the simulated vehicle's axle geometry. */
  setVehicle(fit: CarFit): void {
    for (const car of this.carModels) fitCarToVehicle(car, fit);
  }

  /**
   * Build the circuit. With a Python analysis the mesh uses the physics table
   * (banking, kerbs, run-off materials, barrier offsets); without one, the
   * Three.js spline gives a visual-only preview.
   */
  setCircuit(circuit: Circuit, analysis?: TrackAnalysis): void {
    this.nightLights.clear();
    disposeGroup(this.circuitRoot);
    this.circuitRoot.clear();
    this.hasSurface = false;
    this.wet.wetEnabled.value = 0;
    this.layout = analysis?.physics_table
      ? layoutFromAnalysis(analysis.physics_table)
      : layoutFromGeometry(circuit, sampleCircuit(circuit));
    const layout = this.layout;
    if (!layout.samples.length) return;
    const bounds = new THREE.Box3();
    layout.samples.forEach((s) => bounds.expandByPoint(new THREE.Vector3(s.x, s.y, s.z)));
    bounds.getCenter(this.center);
    const size = bounds.getSize(V());
    this.radius = Math.max(size.x, size.z) * 0.57;
    const asphaltMap = texture("asphalt");
    this.asphalt = new THREE.MeshStandardMaterial({
      color: "#8c9191",
      map: asphaltMap,
      roughness: 0.92,
      metalness: 0.04,
      side: THREE.DoubleSide,
    });
    this.asphalt.userData.wet = true;
    applyWetShader(this.asphalt, this.wet, true);
    const white = new THREE.MeshStandardMaterial({ color: "#eee5ce", roughness: 0.82, side: THREE.DoubleSide });
    const red = new THREE.MeshStandardMaterial({ color: "#ae4439", roughness: 0.85, side: THREE.DoubleSide });
    const grassMap = texture("grass");
    grassMap.repeat.set(90, 90);
    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(2400, 2400),
      new THREE.MeshStandardMaterial({ color: "#c0c8a0", map: grassMap, roughness: 1 }),
    );
    ground.rotation.x = -Math.PI / 2;
    ground.position.set(this.center.x, Math.min(...layout.samples.map((s) => s.y)) - 0.11, this.center.z);
    ground.receiveShadow = true;
    this.circuitRoot.add(ground);
    // Run-off areas between the kerb and the barrier, coloured by material.
    const grassColor = new THREE.Color("#7c9a64"),
      gravelColor = new THREE.Color("#b8ab86"),
      runoffAsphalt = new THREE.Color("#6f7476");
    const materialColor = (m: number) =>
      m === SURFACE_GRAVEL ? gravelColor : m === SURFACE_RUNOFF_ASPHALT ? runoffAsphalt : grassColor;
    const runoff = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 1, side: THREE.DoubleSide });
    this.circuitRoot.add(
      ribbon(layout, (s) => s.half, (s) => s.barrierLeft + 0.6, -0.03, runoff, 5, {
        colors: (s) => materialColor(s.runoffLeft),
      }),
      ribbon(layout, (s) => -s.barrierRight - 0.6, (s) => -s.half, -0.03, runoff, 5, {
        colors: (s) => materialColor(s.runoffRight),
      }),
    );
    // Grass banks join the barrier line to the terrain.
    const bankMap = texture("grass");
    const bankMaterial = new THREE.MeshStandardMaterial({
      map: bankMap,
      color: "#c0c8a0",
      roughness: 1,
      side: THREE.DoubleSide,
    });
    const groundY = ground.position.y + 0.02;
    for (const side of [-1, 1]) {
      const positions: number[] = [],
        uvs: number[] = [],
        indices: number[] = [];
      for (let i = 0; i <= layout.samples.length; i++) {
        const s = layout.samples[i % layout.samples.length];
        const edge = side > 0 ? s.barrierLeft + 0.6 : -(s.barrierRight + 0.6);
        for (const outer of [0, 1]) {
          const offset = edge + side * outer * 16;
          positions.push(
            s.x + s.tz * offset,
            outer ? groundY : surfaceHeight(s, edge) - 0.04,
            s.z - s.tx * offset,
          );
          uvs.push(outer * 3, s.distance / 8);
        }
        if (i < layout.samples.length) {
          const k = i * 2;
          indices.push(k, k + 1, k + 2, k + 1, k + 3, k + 2);
        }
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
      g.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
      g.setIndex(indices);
      g.computeVertexNormals();
      const bank = new THREE.Mesh(g, bankMaterial);
      bank.receiveShadow = true;
      this.circuitRoot.add(bank);
    }
    this.circuitRoot.add(
      ribbon(layout, (s) => -s.half, (s) => s.half, 0.005, this.asphalt, 8, { wetUv: true }),
    );
    for (const sign of [-1, 1]) {
      this.circuitRoot.add(
        ribbon(layout, (s) => sign * s.half - 0.11, (s) => sign * s.half + 0.11, 0.02, white, 4),
      );
      // Kerbs only where the analyser placed them (preview: everywhere).
      const curbPositions: number[] = [],
        curbColors: number[] = [],
        curbIndices: number[] = [];
      const colors = [new THREE.Color("#e8e0cf"), new THREE.Color("#af4035")];
      const samples = layout.samples;
      for (let i = 0; i < samples.length; i++) {
        const a = samples[i],
          b = samples[(i + 1) % samples.length];
        const width = sign > 0 ? a.kerbLeft : a.kerbRight;
        if (width <= 0) continue;
        const color = colors[Math.floor(a.distance / 3.1) % 2],
          k = curbPositions.length / 3;
        for (const s of [a, b])
          for (const o of [s.half + 0.18, s.half + 0.18 + width * 0.94]) {
            const offset = o * sign;
            curbPositions.push(s.x + s.tz * offset, surfaceHeight(s, offset) + 0.045, s.z - s.tx * offset);
            curbColors.push(color.r, color.g, color.b);
          }
        curbIndices.push(k, k + 2, k + 1, k + 1, k + 2, k + 3);
      }
      if (curbIndices.length) {
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.Float32BufferAttribute(curbPositions, 3));
        g.setAttribute("color", new THREE.Float32BufferAttribute(curbColors, 3));
        g.setIndex(curbIndices);
        g.computeVertexNormals();
        const curb = new THREE.Mesh(
          g,
          new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.8, side: THREE.DoubleSide }),
        );
        curb.receiveShadow = true;
        this.circuitRoot.add(curb);
      }
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
      this.circuitRoot.add(ribbon(layout, side - 0.42, side + 0.42, 0.027, rubber, 5));
    this.buildFurniture(red, white);
    this.buildScenery();
    this.sun.target.position.copy(this.center);
    this.sun.position.copy(this.center).add(new THREE.Vector3(-this.radius, this.radius * 1.8, this.radius));
    const cam = this.sun.shadow.camera;
    cam.left = cam.bottom = -this.radius * 1.2;
    cam.right = cam.top = this.radius * 1.2;
    cam.updateProjectionMatrix();
    this.setWeather(this.weather);
    this.setQuality(this.quality, this.scale);
  }

  /** Water film grid from the physics (segments x lanes, mm). */
  setSurface(grid: SurfaceGrid): void {
    if (!this.layout.analysed) return;
    const { segments, lanes, water } = grid;
    const data = new Uint8Array(segments * lanes * 4);
    for (let i = 0; i < segments * lanes; i++) {
      const h = water[i];
      data[i * 4] = Math.round(255 * Math.min(1, h / 0.35));
      data[i * 4 + 1] = Math.round(255 * Math.min(1, Math.max(0, (h - 0.9) / 2.0)));
      data[i * 4 + 3] = 255;
    }
    const current = this.wet.wetMap.value;
    if (current.image.width === lanes && current.image.height === segments) {
      (current.image.data as Uint8Array).set(data);
      current.needsUpdate = true;
    } else {
      const next = new THREE.DataTexture(data, lanes, segments);
      next.magFilter = THREE.LinearFilter;
      next.minFilter = THREE.LinearFilter;
      next.wrapT = THREE.RepeatWrapping;
      next.needsUpdate = true;
      this.wet.wetMap.value = next;
      current.dispose();
    }
    this.hasSurface = true;
    this.wet.wetEnabled.value = 1;
    this.setWeather(this.weather);
  }

  private sideOffset(s: LayoutSample, side: number, extra: number): number {
    return side > 0 ? s.barrierLeft + extra : -(s.barrierRight + extra);
  }

  private buildFurniture(red: THREE.Material, white: THREE.Material): void {
    const steel = new THREE.MeshStandardMaterial({ color: "#bdc4bf", roughness: 0.55, metalness: 0.65 });
    const dark = new THREE.MeshStandardMaterial({ color: "#263c3e", roughness: 0.7 });
    const dummy = new THREE.Object3D();
    const posts = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.09, 0.13, 2.7, 5), steel, 600);
    let postCount = 0;
    const ss = this.layout.samples;
    const postEvery = Math.max(3, Math.round(ss.length / 280));
    for (const side of [-1, 1]) {
      const positions: number[] = [];
      for (let i = 0; i < ss.length; i++) {
        const a = ss[i],
          b = ss[(i + 1) % ss.length];
        const oa = this.sideOffset(a, side, 0),
          ob = this.sideOffset(b, side, 0);
        for (const height of [0.48, 0.86, 2.2])
          positions.push(
            a.x + a.tz * oa,
            surfaceHeight(a, oa) + height,
            a.z - a.tx * oa,
            b.x + b.tz * ob,
            surfaceHeight(b, ob) + height,
            b.z - b.tx * ob,
          );
        if (i % postEvery === 0 && postCount < 600) {
          dummy.position.set(a.x + a.tz * oa, surfaceHeight(a, oa) + 1.2, a.z - a.tx * oa);
          dummy.updateMatrix();
          posts.setMatrixAt(postCount++, dummy.matrix);
        }
      }
      const railGeometry = new THREE.BufferGeometry();
      railGeometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
      this.circuitRoot.add(
        new THREE.LineSegments(
          railGeometry,
          new THREE.LineBasicMaterial({ color: "#a4aca6", transparent: true, opacity: 0.85 }),
        ),
      );
    }
    posts.count = postCount;
    posts.castShadow = true;
    this.circuitRoot.add(posts);
    const start = ss[0],
      half = start.half,
      startGroup = new THREE.Group();
    startGroup.position.set(start.x, start.y, start.z);
    startGroup.rotation.y = Math.atan2(start.tx, start.tz);
    const line = box(
      half * 2 - 0.4,
      0.015,
      1.5,
      new THREE.MeshStandardMaterial({ map: texture("checker"), roughness: 0.7 }),
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
    startGroup.add(box(0.5, 8, 0.5, steel, -half - 2, 4, -4), box(0.5, 8, 0.5, steel, half + 2, 4, -4));
    startGroup.add(box(half * 2 + 4.5, 1.5, 0.65, dark, 0, 7.3, -4));
    const sign = new THREE.Mesh(
      new THREE.PlaneGeometry(half * 1.7, 1.1),
      new THREE.MeshStandardMaterial({ map: texture("sign"), side: THREE.DoubleSide, roughness: 0.7 }),
    );
    sign.position.set(0, 7.3, -3.65);
    startGroup.add(sign);
    for (let i = 0; i < 5; i++) startGroup.add(box(0.23, 0.23, 0.12, red, (i - 2) * 0.6, 6.25, -3.6));
    this.circuitRoot.add(startGroup);
    this.buildLighting(steel, dark);
    this.buildGrandstand(start);
    const pit = new THREE.Group();
    const pitOffset = -(start.barrierRight + 14.5);
    pit.position.set(start.x + start.tz * pitOffset, start.y, start.z - start.tx * pitOffset);
    pit.rotation.y = startGroup.rotation.y;
    const glass = new THREE.MeshStandardMaterial({ color: "#526f77", metalness: 0.55, roughness: 0.2 });
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
          new THREE.MeshStandardMaterial({ color: PALETTE[i % 10], roughness: 0.7 }),
          8.7,
          4.3,
          -51 + i * 6.5,
        ),
      );
    }
    this.circuitRoot.add(pit);
  }

  private buildLighting(steel: THREE.Material, dark: THREE.Material): void {
    this.lampMaterial = new THREE.MeshStandardMaterial({
      color: "#fff7d7",
      emissive: "#d5eaff",
      emissiveIntensity: 0.15,
      roughness: 0.35,
    });
    const ss = this.layout.samples;
    for (let i = 0; i < 20; i++) {
      const sample = ss[Math.floor((i / 20) * ss.length)];
      const side = i % 2 ? -1 : 1;
      const offset = this.sideOffset(sample, side, 2.5);
      const tower = new THREE.Group();
      tower.position.set(sample.x + sample.tz * offset, sample.y, sample.z - sample.tx * offset);
      tower.rotation.y = Math.atan2(sample.tx, sample.tz);
      tower.add(box(0.22, 14, 0.22, steel, 0, 7, 0), box(3.6, 0.16, 0.16, steel, -side * 1.6, 14, 0));
      for (let bulb = 0; bulb < 3; bulb++) {
        tower.add(
          box(0.68, 0.22, 0.5, dark, -side * (bulb + 1), 13.9, 0),
          box(0.55, 0.025, 0.4, this.lampMaterial, -side * (bulb + 1), 13.76, 0),
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

  private buildGrandstand(start: LayoutSample): void {
    const group = new THREE.Group();
    const offset = start.barrierLeft + 10.5;
    group.position.set(start.x + start.tz * offset, start.y, start.z - start.tx * offset);
    group.rotation.y = Math.atan2(start.tx, start.tz);
    const concrete = new THREE.MeshStandardMaterial({ color: "#989c90", roughness: 0.95 });
    const dark = new THREE.MeshStandardMaterial({ color: "#334447", roughness: 0.6, metalness: 0.35 });
    for (let row = 0; row < 9; row++)
      group.add(box(1.8, 0.6 + row * 0.62, 80, concrete, row * 1.5, (0.6 + row * 0.62) / 2, 0));
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
        seats.setColorAt(row * 50 + col, new THREE.Color(row % 3 ? "#b9c1ae" : "#b86c4a"));
        if (rand() > 0.19 && peopleIndex < 380) {
          dummy.position.y += 0.5;
          dummy.scale.set(0.85, 1.4, 0.85);
          dummy.updateMatrix();
          people.setMatrixAt(peopleIndex, dummy.matrix);
          people.setColorAt(peopleIndex++, new THREE.Color(PALETTE[Math.floor(rand() * PALETTE.length)]));
          dummy.scale.setScalar(1);
        }
      }
    people.count = peopleIndex;
    group.add(seats, people);
    for (let i = 0; i < 6; i++) group.add(box(0.35, 12, 0.35, dark, 13.7, 6, (i - 2.5) * 15));
    const roof = box(20, 0.28, 88, dark, 6, 12, 0);
    roof.rotation.z = -0.045;
    group.add(roof);
    group.add(
      box(0.25, 1.6, 88, new THREE.MeshStandardMaterial({ color: "#b9d7cf", roughness: 0.5 }), -3.9, 11.3, 0),
    );
    this.circuitRoot.add(group);
  }

  private buildScenery(): void {
    const rand = seeded(55),
      dummy = new THREE.Object3D();
    const crownGeo = new THREE.IcosahedronGeometry(1, 1);
    this.trees = new THREE.InstancedMesh(
      crownGeo,
      new THREE.MeshStandardMaterial({ color: "#45684b", roughness: 1, flatShading: true }),
      320,
    );
    const trunks = new THREE.InstancedMesh(
      new THREE.CylinderGeometry(0.22, 0.4, 1, 5),
      new THREE.MeshStandardMaterial({ color: "#665340", roughness: 1 }),
      320,
    );
    const accepted: { x: number; z: number }[] = [];
    const ss = this.layout.samples;
    const reach = Math.max(...ss.map((s) => Math.max(s.barrierLeft, s.barrierRight))) + 16;
    let count = 0;
    for (let tries = 0; tries < 4500 && count < 320; tries++) {
      const x = this.center.x + (rand() - 0.5) * this.radius * 3.4,
        z = this.center.z + (rand() - 0.5) * this.radius * 3.4;
      let distance = Infinity;
      for (let i = 0; i < ss.length; i += 4) distance = Math.min(distance, Math.hypot(ss[i].x - x, ss[i].z - z));
      if (distance < reach || accepted.some((p) => Math.hypot(p.x - x, p.z - z) < 8)) continue;
      const h = 7 + rand() * 7;
      accepted.push({ x, z });
      dummy.position.set(x, h * 0.61, z);
      dummy.scale.set(h * 0.4, h * 0.54, h * 0.4);
      dummy.rotation.y = rand() * 6.28;
      dummy.updateMatrix();
      this.trees.setMatrixAt(count, dummy.matrix);
      this.trees.setColorAt(
        count,
        new THREE.Color().setHSL(0.27 + rand() * 0.06, 0.16 + rand() * 0.13, 0.22 + rand() * 0.12),
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
      dummy.scale.set(180 + rand() * 200, 50 + rand() * 110, 160 + rand() * 100);
      dummy.updateMatrix();
      hills.setMatrixAt(i, dummy.matrix);
    }
    this.circuitRoot.add(hills);
  }

  /** Low uses vertex-lit materials, retaining original geometry, maps and liveries. */
  private applyMaterialQuality(): void {
    this.scene.traverse((object) => {
      if (!(object instanceof THREE.Mesh)) return;
      const mesh = object as THREE.Mesh<THREE.BufferGeometry, THREE.Material | THREE.Material[]>;
      const data = mesh.userData as {
        fullMaterials?: THREE.Material[];
        lowMaterials?: THREE.Material[];
        multipleMaterials?: boolean;
      };
      const current: THREE.Material[] = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      if (!data.fullMaterials && current.some((material) => material instanceof THREE.MeshStandardMaterial)) {
        data.fullMaterials = current;
        data.multipleMaterials = Array.isArray(mesh.material);
        data.lowMaterials = current.map((material: THREE.Material): THREE.Material => {
          if (!(material instanceof THREE.MeshStandardMaterial)) return material;
          const low = new THREE.MeshLambertMaterial({
            color: material.color,
            map: material.map,
            side: material.side,
            vertexColors: material.vertexColors,
            transparent: material.transparent,
            opacity: material.opacity,
            depthWrite: material.depthWrite,
            emissive: material.emissive,
            emissiveIntensity: material.emissiveIntensity,
          });
          if (material.userData.wet) applyWetShader(low, this.wet, false);
          return low;
        });
      }
      const materials = this.quality === "low" ? data.lowMaterials : data.fullMaterials;
      if (materials) mesh.material = data.multipleMaterials ? materials : materials[0];
    });
  }

  setQuality(quality: Quality, scale = 1): void {
    this.quality = quality;
    this.scale = scale;
    const ratio =
      Math.min(window.devicePixelRatio || 1, quality === "high" ? 2 : quality === "medium" ? 1.5 : 1) * scale;
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
        Number(this.trees.userData.fullCount) * (quality === "low" ? 0.45 : quality === "medium" ? 0.7 : 1),
      );
    this.carModels.forEach((car) => setCarDetail(car, quality));
    this.applyMaterialQuality();
    this.resize();
  }

  setWeather(weather: Weather): void {
    this.weather = weather;
    const night = weather === "night",
      raining = weather === "rain" || weather === "light-rain",
      overcast = raining || weather === "drying";
    const color = night ? "#101c2c" : weather === "rain" ? "#8eabb4" : overcast ? "#a7bcc2" : "#becfd0";
    this.scene.background = new THREE.Color(color);
    this.renderer.setClearColor(color);
    this.scene.fog = new THREE.FogExp2(
      color,
      night ? 0.002 : weather === "rain" ? 0.0024 : weather === "light-rain" ? 0.0016 : 0.00092,
    );
    this.sun.color.set(night ? "#9fc5ef" : overcast ? "#d4e2e7" : "#fff0d3");
    this.sun.intensity = night ? 1.2 : weather === "rain" ? 1.5 : overcast ? 2.1 : 3.1;
    this.ambient.intensity = night ? 0.75 : overcast ? 1.6 : 2;
    this.renderer.toneMappingExposure = night ? 0.95 : 1.08;
    this.nightLights.visible = night || weather === "rain";
    if (this.lampMaterial) this.lampMaterial.emissiveIntensity = night ? 5 : weather === "rain" ? 1.3 : 0.15;
    if (this.asphalt) {
      // With a physics water grid the wet look is per cell (shader); before it
      // arrives, a uniform wet look is used only as a visual placeholder.
      const uniformWet = !this.hasSurface && (raining || weather === "drying");
      this.asphalt.roughness = uniformWet ? 0.25 : 0.92;
      this.asphalt.metalness = uniformWet ? 0.2 : 0.04;
      this.asphalt.color.set(uniformWet ? "#68787c" : "#8c9191");
    }
    this.rain.visible = raining;
    (this.rain.material as THREE.PointsMaterial).opacity = weather === "light-rain" ? 0.22 : 0.4;
  }

  resize(): void {
    const width = Math.max(1, this.container.clientWidth),
      height = Math.max(1, this.container.clientHeight);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
  }

  private placeCar(mesh: THREE.Group, state: CarState): void {
    const body = mesh.userData.body as THREE.Group | undefined;
    const susp = state.suspension;
    const heave = (susp[0] + susp[1] + susp[2] + susp[3]) / 4;
    mesh.position.set(state.x, state.y + heave, state.z);
    mesh.rotation.set(state.pitch, state.heading, state.roll);
    const wheels = mesh.userData.wheels as THREE.Group[];
    wheels.forEach((wheel) => {
      // Model wheel order: (left?, rear) pairs; physics order FL, FR, RL, RR.
      const front = !!wheel.userData.front,
        left = wheel.userData.side > 0;
      const i = (front ? 0 : 2) + (left ? 0 : 1);
      wheel.rotation.x = state.wheelSpin[i];
      wheel.rotation.y = front ? state.steerAngle : 0;
      wheel.position.y = Number(wheel.userData.baseY) - susp[i];
    });
    if (body) body.position.y = 0;
  }

  render(cars: CarState[] | undefined, elapsed: number, mode: CameraMode): void {
    const dt = Math.min(0.1, Math.max(0.001, elapsed - this.lastTime));
    this.lastTime = elapsed;
    const samples = this.layout.samples;
    if (!samples.length) {
      this.renderer.render(this.scene, this.camera);
      return;
    }
    for (let i = 0; i < this.carModels.length; i++) {
      const mesh = this.carModels[i],
        state = cars?.[i];
      mesh.visible = cars ? !!state : true;
      if (state) this.placeCar(mesh, state);
      else if (!cars) {
        // Showcase/benchmark motion along the centre line (visual only).
        const lap = this.layout.length,
          d = ((((elapsed * 38 + i * 19) % lap) + lap) % lap / lap) * samples.length,
          index = Math.floor(d),
          fraction = d - index;
        const a = samples[index % samples.length],
          b = samples[(index + 1) % samples.length],
          offset = i % 2 ? 1.7 : -1.7;
        mesh.position.set(
          THREE.MathUtils.lerp(a.x, b.x, fraction) + a.tz * offset,
          surfaceHeight(a, offset) + 0.04,
          THREE.MathUtils.lerp(a.z, b.z, fraction) - a.tx * offset,
        );
        mesh.rotation.set(0, Math.atan2(a.tx, a.tz), 0);
        if (mesh.userData.wheels)
          for (const wheel of mesh.userData.wheels as THREE.Object3D[]) wheel.rotation.x += (38 * dt) / 0.36;
      }
    }
    this.updateSpray(cars, elapsed);
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
      this.desired.copy(player.position).addScaledVector(forward, 25).add(new THREE.Vector3(55, 28, 40));
      this.target.copy(player.position).addScaledVector(forward, 7);
    } else if (mode === "cockpit") {
      this.desired.copy(player.position).addScaledVector(forward, -0.38).add(new THREE.Vector3(0, 1.04, 0));
      this.target.copy(player.position).addScaledVector(forward, 50).add(new THREE.Vector3(0, 1.03, 0));
    } else if (mode === "rear") {
      this.desired.copy(player.position).addScaledVector(forward, 7.8).add(new THREE.Vector3(0, 2.8, 0));
      this.target.copy(player.position).addScaledVector(forward, -8).add(new THREE.Vector3(0, 0.5, 0));
    } else {
      this.desired.copy(player.position).addScaledVector(forward, -9.5).add(new THREE.Vector3(0, 3.8, 0));
      this.target.copy(player.position).addScaledVector(forward, 8).add(new THREE.Vector3(0, 0.55, 0));
    }
    if (mode !== this.lastCamera || mode === "cockpit" || this.camera.position.length() < 1)
      this.camera.position.copy(this.desired);
    else this.camera.position.lerp(this.desired, mode === "orbit" ? 0.04 : 1 - Math.exp(-dt * 6));
    this.lastCamera = mode;
    this.camera.lookAt(this.target);
    if (this.rain.visible) {
      this.rain.position.copy(player.position);
      this.rain.position.y -= (elapsed * 37) % 50;
    }
    this.renderer.render(this.scene, this.camera);
  }

  /** Spray clouds behind fast cars on a wet surface (driven by the physics film). */
  private updateSpray(cars: CarState[] | undefined, elapsed: number): void {
    const attr = this.spray.geometry.getAttribute("position") as THREE.BufferAttribute;
    const array = attr.array as Float32Array;
    let any = false;
    const rand = seeded(Math.floor(elapsed * 30));
    for (let c = 0; c < 10; c++) {
      const state = cars?.[c];
      const strength = state ? Math.min(1, state.waterFilm / 0.4) * Math.min(1, Math.max(0, state.speed - 12) / 40) : 0;
      for (let p = 0; p < 40; p++) {
        const k = (c * 40 + p) * 3;
        if (!state || strength < 0.05 || p > 40 * strength) {
          array[k + 1] = -1000;
          continue;
        }
        any = true;
        const back = 2.5 + rand() * 9 * strength;
        const lateral = (rand() - 0.5) * (1.6 + back * 0.25);
        const s = Math.sin(state.heading),
          co = Math.cos(state.heading);
        array[k] = state.x - s * back + co * lateral;
        array[k + 1] = state.y + 0.2 + rand() * (0.6 + back * 0.12);
        array[k + 2] = state.z - co * back - s * lateral;
      }
    }
    attr.needsUpdate = true;
    this.spray.visible = any;
  }

  async warmup(): Promise<void> {
    await this.renderer.compileAsync(this.scene, this.camera);
    this.camera.position.set(0, 0, 0);
    this.lastTime = 0;
    for (const car of this.carModels) {
      for (const wheel of (car.userData.wheels || []) as THREE.Object3D[]) wheel.rotation.x = 0;
    }
  }

  dispose(): void {
    disposeGroup(this.scene);
    this.environment.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}

import "@fontsource/barlow-condensed/latin-600.css";
import "@fontsource/barlow-condensed/latin-800-italic.css";
import "@fontsource/dm-sans/latin-400.css";
import "@fontsource/dm-sans/latin-500.css";
import "@fontsource/dm-sans/latin-600.css";
import "./style.css";
import type {
  AssistLevel,
  CameraMode,
  CarState,
  Circuit,
  DriverInput,
  GraphicsSettings,
  Quality,
  RaceSettings,
  Weather,
} from "./core/types";
import { defaultCircuit, exportCircuit, importCircuit } from "./circuits/model";
import { sampleCircuit } from "./circuits/geometry";
import { mountCircuitEditor } from "./circuits/editor";
import { RacingWorld } from "./graphics/world";
import { inspectGpu, type GpuInfo } from "./graphics/gpu";
import { Benchmark, GpuTimer, type BenchmarkReport } from "./graphics/benchmark";
import { AdaptiveQuality } from "./graphics/quality";
import { storage } from "./storage/database";
import { PhysicsClient } from "./physics/client";
import { interpolateCar } from "./physics/contract";
import { analysisDocument, type TrackAnalysis } from "./physics/analysis";
import type { PhysicsEvent, SessionConfig } from "./physics/protocol";

const $ = <T extends Element = HTMLElement>(s: string) => document.querySelector<T>(s)!;
const escape = (s: unknown) =>
  String(s).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!,
  );
const icon = (name: string) => {
  const paths: Record<string, string> = {
    home: "M3 10 12 3l9 7v11H3Z M9 21v-8h6v8",
    track: "M7 4C0 5 3 12 5 15s8 7 13 4 3-8-2-9 1-8-9-6Z",
    chart: "M4 20V4 M4 20h17 M8 15l4-7 4 4 5-8",
    settings:
      "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2",
    plus: "M12 4v16 M4 12h16",
    arrow: "M5 12h14 M13 5l7 7-7 7",
    play: "m8 4 13 8-13 8Z",
    help: "M9 8a3 3 0 1 1 5 2c-2 1-2 2-2 4 M12 17v1 M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20",
    check: "m4 12 5 5L20 6",
    flag: "M5 22V3 M5 4c5-5 9 5 15 0v10c-6 5-10-5-15 0",
  };
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${
    paths[name]
      ?.split(" M")
      .map((p, i) => `<path d="${i ? "M" : ""}${p}"/>`)
      .join("") || ""
  }</svg>`;
};
const labels = { low: "Baixa", medium: "Média", high: "Alta" };
const weatherLabels: Record<Weather, string> = {
  sun: "Dia ensolarado",
  night: "Corrida noturna",
  "light-rain": "Chuva leve",
  rain: "Chuva intensa",
  drying: "Pista secando",
};
const assistLabels: Record<AssistLevel, string> = {
  full: "Completas (controle de tração, antitravamento)",
  medium: "Médias (controle de tração leve)",
  off: "Desligadas",
};
const COLORS = ["#ff512f", "#00d4e8", "#ffc940", "#906bff", "#47dd94", "#f978c1", "#d9e2eb", "#3d91ff", "#e39c45", "#b8e251"];
const DIFFICULTIES = ["Fácil", "Intermediário", "Médio", "Difícil", "Realista"];
interface VehicleSummary {
  id: string;
  name: string;
  era: string;
  provenance: { status: string; summary: string; public_reference_points: string[] };
  peak_engine_power_kw: number;
  ers_power_kw: number;
  mass: number;
  drs: boolean;
  compounds: { id: string; name: string; colour: string }[];
  geometry: {
    wheelbase: number;
    front: number;
    rear: number;
    radius_front: number;
    radius_rear: number;
  };
  brake_bias: number;
  fuel_capacity: number;
}
interface SessionInfo {
  vehicle: VehicleSummary;
  compound: string;
  cars: number;
  laps: number;
  difficulty: string;
  weather: string;
  air_density: number;
  setup_ms: number;
  brake_bias: number;
}
let settings: GraphicsSettings = {
  quality: "medium",
  scale: 1,
  adaptive: true,
  weather: "sun",
  camera: "chase",
  showFps: true,
};
let circuit = defaultCircuit(),
  savedCircuits: Circuit[] = [],
  reports: BenchmarkReport[] = [];
let page = "home",
  world: RacingWorld | undefined,
  gpu: GpuInfo | undefined,
  timer: GpuTimer | undefined;
let raceOptions: RaceSettings = {
  cars: 6,
  laps: 3,
  difficulty: 2,
  weather: "sun",
  vehicle: "ground-effect-2022",
  assists: "full",
  brakeBias: 0.57,
};
let physics: PhysicsClient | undefined;
let vehicles: VehicleSummary[] = [];
const analysisMemo = new Map<string, TrackAnalysis>();
let raceAnalysis: TrackAnalysis | undefined;
let raceInfo: SessionInfo | undefined;
let racing = false,
  paused = false,
  countdown = 0,
  finishedShown = false,
  brakeBias = 0.57,
  raceContacts = 0,
  editor: ReturnType<typeof mountCircuitEditor> | undefined;
let benchmarkRestored = true,
  benchmarkPreparing = false,
  benchmarkClock = 0;
let benchmark: Benchmark | undefined,
  benchmarkMeta: { quality: Quality; weather: Weather; target: number } | undefined;
let previous = performance.now(),
  elapsed = 0,
  hudElapsed = 0,
  fpsFrames = 0,
  fpsTime = 0,
  currentFps = 0;
const governor = new AdaptiveQuality(),
  keys = new Set<string>();
const bindings: Record<string, string> = {
  throttle: "KeyW",
  brake: "KeyS",
  left: "KeyA",
  right: "KeyD",
  drs: "KeyE",
};
const worldHost = document.createElement("div");
worldHost.id = "world";
let worldError = "";
let previewTimeout: ReturnType<typeof setTimeout>;
let analysisTimeout: ReturnType<typeof setTimeout>;
const app = $("#app");
app.innerHTML = `<aside class="sidebar"><a class="brand" href="#home" aria-label="APEX início"><span class="brand-mark">Λ</span> APEX<span class="brand-sub">RACING LAB</span></a><div class="workspace-label">SEU PRÓXIMO LIMITE</div><nav aria-label="Navegação principal">${[
  ["home", "home", "Visão geral"],
  ["editor", "plus", "Criar circuito"],
  ["circuits", "track", "Meus circuitos"],
  ["benchmark", "chart", "Benchmark"],
  ["settings", "settings", "Configurações"],
]
  .map(
    ([id, ic, label]) =>
      `<button data-nav="${id}">${icon(ic)}<span>${label}</span>${id === "editor" ? '<span class="nav-new">NOVO</span>' : ""}</button>`,
  )
  .join(
    "",
  )}</nav><div class="sidebar-bottom"><div class="build-badge" id="physics-badge"><span class="status-dot"></span> <span id="physics-status">MOTOR FÍSICO PYTHON · CARREGANDO</span></div><button data-nav="help" class="help-btn">${icon("help")} Ajuda e controles</button><div class="sidebar-foot">FEITO PARA QUEM<br>DESENHA O PRÓPRIO CAMINHO.<span>v1.0 / PYTHON + WEBGL 2</span></div></div></aside><div class="main"><header class="topbar"><div class="breadcrumb">WORKSPACE <span>/</span> <strong id="page-name">VISÃO GERAL</strong></div><div class="topbar-right"><span class="local-label"><span class="status-dot"></span> SALVO NESTE NAVEGADOR</span><button class="profile" data-nav="help" aria-label="Ajuda do piloto">HL</button></div></header><main id="content"></main><footer class="footer"><span>APEX RACING LAB <b>© 2026</b></span><span>CRIAR. TESTAR. ACELERAR. <i>↗</i></span></footer></div><div id="toast" role="status" aria-live="polite"></div><dialog id="modal"></dialog>`;
for (const button of document.querySelectorAll<HTMLElement>("[data-nav]"))
  button.onclick = () => navigate(button.dataset.nav!);
window.addEventListener("hashchange", () => {
  const id = location.hash.slice(1);
  if (["home", "editor", "circuits", "benchmark", "settings", "help"].includes(id) && id !== page) navigate(id);
});
function toast(message: string) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("visible");
  setTimeout(() => el.classList.remove("visible"), 5000);
}
function fail(error: unknown) {
  toast(error instanceof Error ? error.message : "Não foi possível concluir a operação.");
  console.error(error);
}
function modal(html: string) {
  const d = $<HTMLDialogElement>("#modal");
  d.innerHTML = `<button class="close-modal" aria-label="Fechar">×</button>${html}`;
  $(".close-modal").addEventListener("click", () => d.close());
  if (!d.open) d.showModal();
  return d;
}
function miniTrack(c: Circuit) {
  const { samples } = sampleCircuit(c);
  const xs = samples.map((p) => p.x),
    zs = samples.map((p) => p.z),
    minX = Math.min(...xs),
    minZ = Math.min(...zs),
    s = 160 / Math.max(Math.max(...xs) - minX, Math.max(...zs) - minZ);
  return `<svg class="mini-track" viewBox="0 0 200 160" aria-label="Traçado do circuito"><path d="M${samples
    .filter((_, i) => i % 8 === 0)
    .map((p) => `${20 + (p.x - minX) * s},${10 + (p.z - minZ) * s * 0.8}`)
    .join(" L")} Z"/></svg>`;
}

// ------------------------------------------------------------ physics engine
function updatePhysicsBadge() {
  const label = $("#physics-status");
  if (!label) return;
  const status = physics?.status;
  label.textContent = !status
    ? "MOTOR FÍSICO PYTHON · CARREGANDO"
    : status.state === "ready"
      ? "MOTOR FÍSICO PYTHON · PRONTO"
      : status.state === "error"
        ? "MOTOR FÍSICO · ERRO"
        : `MOTOR FÍSICO · ${status.message.toUpperCase()}`;
  $("#physics-badge").dataset.state = status?.state ?? "loading";
}
async function requirePhysics(): Promise<PhysicsClient> {
  if (!physics) throw new Error("O motor físico não pôde ser iniciado neste navegador.");
  await physics.whenReady();
  return physics;
}
const docKey = (c: Circuit) => JSON.stringify(analysisDocument(c));
async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
/**
 * Python analysis with a cache keyed by the physical document plus the
 * analyser/engine versions. Cached full analyses are re-registered in the
 * worker; a different version or document always triggers a new analysis.
 */
async function ensureAnalysis(c: Circuit, level: "quick" | "full"): Promise<TrackAnalysis> {
  const client = await requirePhysics();
  const key = docKey(c);
  const memo = analysisMemo.get(key);
  if (memo && (level === "quick" || memo.level === "full")) return memo;
  const version = `${client.status.engine}`;
  const storeKey = await sha256(`${key}|${version}`);
  if (level === "full") {
    const stored = await storage.analysis(storeKey).catch(() => undefined);
    if (stored && stored.level === "full" && stored.engine_version === client.status.engine) {
      await client.remember(stored);
      analysisMemo.set(key, stored);
      return stored;
    }
  }
  const result = await client.analyze<TrackAnalysis>(analysisDocument(c), level);
  if (!memo || level === "full") analysisMemo.set(key, result);
  if (level === "full" && result.valid) void storage.saveAnalysis(storeKey, result).catch(fail);
  return result;
}
const isHomologated = (c: Circuit, analysis?: TrackAnalysis) =>
  c.homologatedVersion === c.version && (!analysis || !c.homologatedHash || c.homologatedHash === analysis.hash);

function mountWorld() {
  const slot = $("#scene-mount");
  if (slot) {
    slot.append(worldHost);
    if (worldError)
      worldHost.innerHTML = `<div class="webgl-error">${icon("help")}<h3>WebGL 2 indisponível</h3><p>${escape(worldError)}</p><p>Ative a aceleração gráfica no Chrome e recarregue. O editor e a biblioteca continuam disponíveis.</p></div>`;
    requestAnimationFrame(() => world?.resize());
  }
}
function navigate(id: string) {
  if (benchmark?.active || benchmarkPreparing) {
    toast("Conclua ou cancele o benchmark antes de trocar de tela.");
    return;
  }
  if (racing && page === "race" && id !== "race") endRace();
  editor?.dispose();
  editor = undefined;
  clearTimeout(previewTimeout);
  clearTimeout(analysisTimeout);
  page = id;
  location.hash = id;
  document
    .querySelectorAll("[data-nav]")
    .forEach((el) => el.classList.toggle("active", (el as HTMLElement).dataset.nav === id));
  $("#page-name").textContent =
    (
      {
        home: "VISÃO GERAL",
        editor: "ESTÚDIO DE CIRCUITOS",
        circuits: "BIBLIOTECA",
        benchmark: "LABORATÓRIO DE DESEMPENHO",
        settings: "CONFIGURAÇÕES",
        help: "CENTRAL DO PILOTO",
        race: "NA PISTA",
      } as Record<string, string>
    )[id] || id;
  (
    ({
      home: renderHome,
      editor: renderEditor,
      circuits: renderCircuits,
      benchmark: renderBenchmark,
      settings: renderSettings,
      help: renderHelp,
      race: renderRace,
    }) as Record<string, () => void>
  )[id]?.();
  mountWorld();
  if (worldHost.isConnected) {
    world?.setCircuit(circuit, id === "race" ? raceAnalysis : analysisMemo.get(docKey(circuit)));
    if (id === "race" && physics?.surface) world?.setSurface(physics.surface);
    world?.setWeather(id === "race" ? raceOptions.weather : settings.weather);
  }
  governor.reset();
}
function renderHome() {
  $("#content").innerHTML =
    `<section class="welcome-row"><div><p class="eyebrow">O ASFALTO É SÓ O COMEÇO</p><h1>Bem-vindo ao seu laboratório.</h1></div><div class="session-tag">SESSÃO LOCAL <span>01</span></div></section><section class="hero"><div id="scene-mount"></div><div class="hero-shade"></div><div class="hero-copy"><span class="pill"><span></span> RACING SANDBOX / 3D</span><h2>SUA PISTA.<br>SEU <em>LIMITE.</em></h2><p>Do primeiro traço à última curva.<br>Crie circuitos, acerte sua máquina e assuma o volante.</p><div class="hero-actions"><button class="primary" id="quick-race">${icon("play")} Iniciar corrida</button><button class="hero-secondary" id="hero-editor">Criar circuito ${icon("arrow")}</button></div></div><div class="hero-number">01<span>/ A PRÓXIMA VOLTA É SUA</span></div><div class="scene-caption"><span class="live-dot"></span> CENA 3D EM TEMPO REAL <b>WEBGL 2</b></div><div class="hero-circuit"><span>CIRCUITO EM DESTAQUE</span><strong>${escape(circuit.name)}</strong><small>TRAÇADO ORIGINAL · ${(sampleCircuit(circuit).length / 1000).toFixed(2)} KM</small></div></section><section class="system-strip"><div class="system-gpu"><span class="chip-icon">▧</span><div><label>SEU HARDWARE</label><strong id="gpu-short">Detectando GPU…</strong></div></div><div><label>PERFIL GRÁFICO</label><strong><i class="green-dot"></i><span id="quality-label">${labels[settings.quality]}</span></strong></div><div><label>FÍSICA</label><strong>Python <span class="muted">/ WebAssembly</span></strong></div><button id="diagnostic-btn">Ver diagnóstico ${icon("arrow")}</button></section><section class="section-title"><h2>Da ideia à pista<span>.</span></h2><span>SEU FLUXO, SEM DESVIOS</span></section><div class="launch-grid"><button class="launch-card editor-card" id="card-editor"><div class="card-top"><span>01 / CONSTRUIR</span>${icon("arrow")}</div><div class="track-art">${miniTrack(circuit)}<span class="point-art p1"></span><span class="point-art p2"></span></div><h3>Uma pista. Sua assinatura.</h3><p>Desenhe curvas, ajuste elevação e inclinação<br>e receba a análise física automática.</p><div class="card-bottom">ABRIR EDITOR ${icon("plus")}</div></button><button class="launch-card benchmark-card" id="card-benchmark"><div class="card-top"><span>02 / CALIBRAR</span>${icon("arrow")}</div><div class="graph-art"><div class="graph-bars">${[30, 43, 36, 58, 49, 73, 65, 82, 76, 91, 84, 96, 86, 100, 91, 110, 100, 114, 105, 122].map((h) => `<i style="height:${h}px"></i>`).join("")}</div><span>MEDIR. AJUSTAR. EVOLUIR.</span></div><h3>Conheça sua máquina.</h3><p>Benchmark gráfico e do motor físico.<br>Encontre seu equilíbrio entre detalhe e fluidez.</p><div class="card-bottom">EXECUTAR BENCHMARK ${icon("chart")}</div></button><button class="launch-card race-card" id="card-race"><div class="card-top"><span>03 / COMPETIR</span>${icon("arrow")}</div><div class="race-art"><div class="speed-lines"></div><span>LIGHTS<br><em>OUT.</em></span><div class="start-lights"><i></i><i></i><i></i><i></i><i></i></div></div><h3>Cada milésimo importa.</h3><p>Até 10 carros com a mesma física,<br>cinco níveis de IA determinística.</p><div class="card-bottom">PREPARAR CORRIDA ${icon("flag")}</div></button></div><section class="bottom-note"><div>${icon("track")} <span>UM ESPAÇO PARA EXPERIMENTAR.<b> Física em Python. Circuitos originais. Possibilidades abertas.</b></span></div><button id="learn-more">Conheça o projeto ↗</button></section>`;
  $("#quick-race").onclick = () => void prepareRace();
  $("#hero-editor").onclick = () => navigate("editor");
  $("#card-editor").onclick = () => navigate("editor");
  $("#card-benchmark").onclick = () => navigate("benchmark");
  $("#card-race").onclick = () => void prepareRace();
  $("#diagnostic-btn").onclick = () => navigate("settings");
  $("#learn-more").onclick = () => navigate("help");
  updateGpuLabel();
}
function updateGpuLabel() {
  if ($("#gpu-short"))
    $("#gpu-short").textContent = gpu
      ? gpu.name.length > 50
        ? gpu.name.slice(0, 47) + "…"
        : gpu.name
      : worldError
        ? "Indisponível"
        : "Detectando…";
}
function heading(kicker: string, title: string, description: string) {
  return `<section class="page-heading"><p class="eyebrow">${kicker}</p><h1>${title}</h1><p>${description}</p></section>`;
}
function renderEditor() {
  $("#content").innerHTML =
    heading(
      "CADA CURVA COMEÇA COM UMA IDEIA",
      "Estúdio de circuitos.",
      "Desenhe um traçado fechado ou arraste os pontos. O analisador Python recalcula a física a cada alteração.",
    ) +
    `<div class="editor-layout"><section class="panel"><div id="editor-mount"></div></section><aside class="panel inspector"><div class="panel-heading"><span>PRÉVIA 3D</span><span class="status-dot"></span></div><div id="scene-mount" class="editor-preview"></div><div id="circuit-info"></div><p class="small muted" id="quick-analysis" aria-live="polite"></p><div class="stack"><button id="validate" class="primary">${icon("check")} Validar circuito</button><button id="save-track" class="secondary">Salvar versão local</button><button id="test-track" class="secondary">Testar ao volante</button><button id="export-track" class="text-btn">Exportar JSON ↗</button></div><div id="validation" aria-live="polite"></div></aside></div>`;
  editor = mountCircuitEditor($("#editor-mount"), circuit, (c) => {
    circuit = c;
    updateCircuitInfo();
    clearTimeout(previewTimeout);
    previewTimeout = setTimeout(() => world?.setCircuit(circuit), 180);
    $("#validation").innerHTML = "";
    scheduleQuickAnalysis();
  });
  updateCircuitInfo();
  scheduleQuickAnalysis();
  $("#validate").onclick = () => void showValidation();
  $("#save-track").onclick = () => saveCircuit();
  $("#test-track").onclick = () => void startRace(true);
  $("#export-track").onclick = () => download(`${circuit.name}.json`, exportCircuit(circuit));
}
/** Re-analyse after edits (debounced): invalidated data is never reused. */
function scheduleQuickAnalysis() {
  clearTimeout(analysisTimeout);
  const target = $("#quick-analysis");
  if (target) target.textContent = "Análise física: aguardando alterações…";
  analysisTimeout = setTimeout(async () => {
    const snapshot = circuit;
    try {
      const result = await ensureAnalysis(snapshot, "quick");
      if (snapshot !== circuit || !$("#quick-analysis")) return;
      const stats = result.stats as Record<string, number> | undefined;
      $("#quick-analysis").textContent = result.errors.length
        ? `Análise física: ${result.errors.length} erro(s) — ${result.errors[0].message}`
        : `Análise física: ${stats?.corners ?? 0} curvas · raio mínimo ${stats?.min_radius ?? "—"} m · ${result.warnings.length} aviso(s).`;
    } catch (e) {
      if ($("#quick-analysis")) $("#quick-analysis").textContent = `Análise física indisponível: ${(e as Error).message}`;
    }
  }, 450);
}
function updateCircuitInfo() {
  const geo = sampleCircuit(circuit);
  if ($("#circuit-info"))
    $("#circuit-info").innerHTML =
      `<h3>${escape(circuit.name)}</h3><div class="stat-pair"><span>${(geo.length / 1000).toFixed(2)}<small>KM DE TRAÇADO</small></span><span>${circuit.points.length}<small>PONTOS DE CONTROLE</small></span></div><p class="muted">Versão ${circuit.version} · ${circuit.homologatedVersion === circuit.version ? "Homologada" : "Aguardando homologação"}</p>`;
}
const fmtLap = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(2).padStart(5, "0")}`;
function analysisReport(result: TrackAnalysis): string {
  const stats = (result.stats ?? {}) as Record<string, number | string>;
  const ref = result.reference_vehicle ? result.profiles?.[result.reference_vehicle] : undefined;
  const items = [
    [`${((result.length || 0) / 1000).toFixed(2)} km`, "COMPRIMENTO"],
    [String(stats.corners ?? 0), "CURVAS"],
    [`${stats.min_radius ?? "—"} m`, "RAIO MÍNIMO"],
    [`${stats.elevation_range ?? 0} m`, "DESNÍVEL"],
    [ref ? fmtLap(ref.dry.lap_time) : "—", "VOLTA DE REFERÊNCIA (SECO)"],
    [ref ? fmtLap(ref.wet.lap_time) : "—", "VOLTA DE REFERÊNCIA (CHUVA)"],
    [ref ? `${ref.dry.top_speed_kmh.toFixed(0)} km/h` : "—", "VELOCIDADE MÁXIMA"],
    [String(result.braking_zones?.length ?? 0), "ZONAS DE FRENAGEM"],
  ];
  return `<div class="analysis-grid">${items.map(([v, l]) => `<div><strong>${escape(v)}</strong><span>${l}</span></div>`).join("")}</div>${
    result.hazards?.length
      ? `<p class="small"><b>Áreas de atenção:</b></p><ul class="small hazard-list">${result.hazards
          .slice(0, 6)
          .map((h) => `<li data-severity="${h.severity}">${(h.s / 1000).toFixed(2)} km · ${escape(h.message)}</li>`)
          .join("")}</ul>`
      : ""
  }${result.recommendations?.length ? `<p class="small muted">${result.recommendations.map(escape).join(" ")}</p>` : ""}<p class="small muted">Referência = perfil quase-estático do carro padrão (mesmos modelos de pneu, aerodinâmica e motor da simulação); não é velocidade obrigatória. Hash ${escape(result.hash.slice(0, 12))} · analisador ${escape(result.analyzer_version)} · ${result.timings ? Math.round(result.timings.total_ms) : "?"} ms.</p>`;
}
async function showValidation() {
  $("#validation").innerHTML = '<p class="muted">Analisando com o motor físico Python…</p>';
  try {
    const target = circuit;
    const result = await ensureAnalysis(target, "full");
    if (target !== circuit || !$("#validation")) return;
    $("#validation").innerHTML =
      `<div class="validation ${result.errors.length ? "invalid" : "valid"}"><strong>${result.errors.length ? "Revise o traçado" : "Geometria validada"}</strong>${result.errors.map((s) => `<p>× ${escape(s.message)}</p>`).join("")}${result.warnings.map((s) => `<p>△ ${escape(s.message)}</p>`).join("")}${result.errors.length ? "" : `${analysisReport(result)}<p>Grid, checkpoints, zebras, áreas de escape e barreiras gerados automaticamente. Pit lane ainda indisponível.</p><button class="primary" id="homologate">Confirmar homologação v${circuit.version}</button>`}</div>`;
    if ($("#homologate"))
      $("#homologate").onclick = () => {
        circuit = { ...circuit, homologatedVersion: circuit.version, homologatedHash: result.hash };
        editor?.setCircuit(circuit);
        updateCircuitInfo();
        toast("Versão homologada. Qualquer alteração estrutural exige nova validação.");
        $("#validation").innerHTML = '<p class="success">✓ Circuito homologado para corrida.</p>';
      };
  } catch (e) {
    $("#validation").innerHTML = `<div class="validation invalid"><strong>Análise indisponível</strong><p>${escape((e as Error).message)}</p></div>`;
  }
}
async function saveCircuit() {
  try {
    await storage.saveCircuit(circuit);
    savedCircuits = await storage.circuits();
    toast(`Versão ${circuit.version} salva neste navegador.`);
  } catch (e) {
    fail(e);
  }
}
function renderCircuits() {
  $("#content").innerHTML =
    heading(
      "SUA COLEÇÃO DE ASFALTO",
      "Meus circuitos.",
      "Versões salvas neste navegador. Exporte JSON para manter uma cópia em outro dispositivo.",
    ) +
    `<div class="toolbar"><button class="primary" id="new-track">${icon("plus")} Criar circuito</button><button class="secondary" id="import-btn">Importar JSON</button><input type="file" id="import-file" accept="application/json,.json" hidden></div><div class="circuit-grid">${[defaultCircuit(), ...savedCircuits].map((c, i) => `<button class="circuit-card panel" data-circuit="${i}">${miniTrack(c)}<span class="eyebrow">${i === 0 ? "CIRCUITO ORIGINAL" : "VERSÃO LOCAL"} / V${c.version}</span><h3>${escape(c.name)}</h3><p>${(sampleCircuit(c).length / 1000).toFixed(2)} km · ${c.homologatedVersion === c.version ? "Homologado" : "Validar antes de correr"}</p><span class="text-link">Selecionar circuito ↗</span></button>`).join("")}</div><section class="section-title"><h2>Arquivo histórico<span>.</span></h2><span>REFERÊNCIAS INFORMATIVAS</span></section><p class="muted">Sem geometrias licenciadas e verificadas, estes locais não estão disponíveis para corrida. Quando houver dados confiáveis, eles passarão pelo mesmo analisador Python das pistas criadas por você; o catálogo de 1980 ao presente permanece em desenvolvimento.</p><div class="historical-grid">${[
      ["Monza", "Itália", "Autódromo permanente", "https://www.formula1.com/en/racing/2025/italy/circuit"],
      ["Interlagos", "Brasil", "Autódromo permanente", "https://www.formula1.com/en/racing/2025/brazil/circuit"],
      ["Suzuka", "Japão", "Autódromo permanente", "https://www.formula1.com/en/racing/2025/japan/circuit"],
    ]
      .map(
        ([n, c, t, url]) =>
          `<div class="panel historical"><span class="eyebrow">${c}</span><h3>${n}</h3><p>${t}</p><span class="unavailable">GEOMETRIA INDISPONÍVEL</span><a href="${url}" target="_blank" rel="noopener noreferrer">Referência oficial ↗</a></div>`,
      )
      .join("")}</div>`;
  $("#new-track").onclick = () => {
    circuit = {
      ...defaultCircuit(),
      id: crypto.randomUUID(),
      name: "Meu novo circuito",
      version: 1,
    };
    navigate("editor");
  };
  $("#import-btn").onclick = () => $<HTMLInputElement>("#import-file").click();
  $<HTMLInputElement>("#import-file").onchange = async (e) => {
    const file = (e.target as HTMLInputElement).files?.[0];
    if (!file) return;
    try {
      if (file.size > 1_000_000) throw new Error("O arquivo deve ter no máximo 1 MB.");
      circuit = importCircuit(await file.text());
      navigate("editor");
      toast("Circuito importado. Valide esta versão antes de correr.");
    } catch (e) {
      fail(e);
    }
  };
  document.querySelectorAll<HTMLElement>("[data-circuit]").forEach(
    (el) =>
      (el.onclick = () => {
        circuit = [defaultCircuit(), ...savedCircuits][Number(el.dataset.circuit)];
        navigate("home");
        toast(`${circuit.name} selecionado.`);
      }),
  );
}
function renderBenchmark() {
  $("#content").innerHTML =
    heading(
      "DADOS REAIS. DECISÕES MELHORES.",
      "Encontre seu ponto ideal.",
      "Uma cena com dez carros, cenário e clima. Três segundos de aquecimento; pelo menos 30 segundos de medição.",
    ) +
    `<div class="benchmark-layout"><section class="panel benchmark-scene"><div id="scene-mount"></div><div class="benchmark-overlay"><span class="pill">CENA REPRESENTATIVA / 10 CARROS</span><div id="benchmark-status">PRONTO PARA MEDIR</div><div class="progress-track"><div id="benchmark-progress"></div></div></div></section><aside class="panel benchmark-controls"><span class="eyebrow">PROTOCOLO DE TESTE</span><h3>Sua máquina, em números.</h3><label>Perfil gráfico<select id="bench-quality">${Object.entries(
      labels,
    )
      .map(([v, l]) => `<option value="${v}" ${v === settings.quality ? "selected" : ""}>${l}</option>`)
      .join("")}</select></label><label>Cenário<select id="bench-weather">${Object.entries(weatherLabels)
      .map(([v, l]) => `<option value="${v}">${l}</option>`)
      .join(
        "",
      )}</select></label><div class="form-row"><label>Meta<select id="bench-target"><option>30</option><option selected>60</option><option>120</option></select></label><label>Duração<select id="bench-duration"><option value="30">30 segundos</option><option value="45">45 segundos</option><option value="60">60 segundos</option></select></label></div><button class="primary" id="start-benchmark">${icon("play")} Iniciar benchmark real</button><button class="secondary" id="cancel-benchmark" hidden>Cancelar medição</button><p class="small muted">Adaptação gráfica suspensa durante o teste. Mantenha esta aba visível e o tamanho da janela fixo. Nenhum resultado estima o desempenho da RTX 3050.</p></aside></div><div id="benchmark-result"></div><section class="section-title"><h2>Motor físico<span>.</span></h2><span>PYTHON EM WEBASSEMBLY, NO WEB WORKER</span></section><section class="panel physics-bench"><p class="muted">Mede no seu navegador o custo real do passo físico de 1/120 s (média, p95, p99), o custo por carro, a parcela da IA, o tempo de análise de pista, a memória e a latência entre o Python e o renderizador. A física roda em um Web Worker e não bloqueia a renderização.</p><button class="secondary" id="physics-bench-btn">${icon("chart")} Medir motor físico</button><div id="physics-bench-result" aria-live="polite"></div></section><section class="section-title"><h2>Histórico de medições<span>.</span></h2><span>SALVO LOCALMENTE</span></section><div id="history"></div>`;
  $("#start-benchmark").onclick = () => void startBenchmark();
  $("#cancel-benchmark").onclick = () => {
    benchmark?.invalidate("Medição cancelada pelo piloto.");
    updateBenchmark();
  };
  $("#physics-bench-btn").onclick = () => void runPhysicsBenchmark();
  renderHistory();
}
interface PhysicsBenchCase {
  cars: number;
  weather: string;
  step_mean_ms: number;
  step_p95_ms: number;
  step_p99_ms: number;
  step_max_ms: number;
  per_car_ms: number;
  ai_mean_ms: number;
  realtime_load: number;
  realtime_load_p99: number;
}
interface PhysicsBenchSummary {
  physics: PhysicsBenchCase[];
  analysis: Record<string, number>;
  snapshot: { snapshot_ms: number; frame_60hz_mean_ms: number; frame_60hz_p99_ms: number };
  memory: { peak_mb: number; analysis_peak_mb: number };
  environment: { python_version: string };
  total_s: number;
}
const BENCH_CIRCUITS: Record<string, string> = { aurora: "Aurora Park (1,0 km)", oval: "oval (3,3 km)" };
async function runPhysicsBenchmark() {
  const out = $("#physics-bench-result");
  const button = $<HTMLButtonElement>("#physics-bench-btn");
  button.disabled = true;
  out.innerHTML = '<p class="muted">Medindo (cerca de 20–60 s)… a interface continua responsiva.</p>';
  try {
    const client = await requirePhysics();
    const latency = await client.ping(120);
    const result = await client.benchmark<PhysicsBenchSummary>(true);
    const ms = (v: number) => `${v.toFixed(3)} ms`;
    out.innerHTML = `<div class="table-scroll"><table><thead><tr><th>CARROS / CLIMA</th><th>PASSO MÉDIO</th><th>P95</th><th>P99</th><th>MÁX.</th><th>POR CARRO</th><th>IA</th><th>CARGA (MÉDIA / P99)</th></tr></thead><tbody>${result.physics
      .map(
        (c) =>
          `<tr><td>${c.cars} / ${escape(c.weather)}</td><td>${ms(c.step_mean_ms)}</td><td>${ms(c.step_p95_ms)}</td><td>${ms(c.step_p99_ms)}</td><td>${ms(c.step_max_ms)}</td><td>${ms(c.per_car_ms)}</td><td>${ms(c.ai_mean_ms)}</td><td>${(c.realtime_load * 100).toFixed(1)}% / ${(c.realtime_load_p99 * 100).toFixed(1)}%</td></tr>`,
      )
      .join("")}</tbody></table></div><p class="small muted">Análise completa de pista (mediana): ${Object.entries(result.analysis)
      .map(([k, v]) => `${escape(BENCH_CIRCUITS[k] ?? k)} ${v.toFixed(0)} ms`)
      .join(" · ")}. Quadro de 60 Hz (2 passos + estado): média ${ms(result.snapshot.frame_60hz_mean_ms)}, p99 ${ms(result.snapshot.frame_60hz_p99_ms)}. Memória Python (pico, tracemalloc): sessão de 10 carros ${result.memory.peak_mb.toFixed(2)} MB, análise ${result.memory.analysis_peak_mb.toFixed(2)} MB. Latência ida e volta da mensagem ao Worker: mediana ${latency.median.toFixed(2)} ms, p95 ${latency.p95.toFixed(2)} ms. Pyodide ${escape(client.status.pyodide)} (Python ${escape(result.environment.python_version)}) carregado em ${client.status.initMs?.toFixed(0)} ms (motor em ${client.status.importMs?.toFixed(0)} ms); medição total ${result.total_s.toFixed(1)} s. Carga = tempo de física ÷ 8,33 ms por passo; acima de 100% a simulação não acompanha o tempo real.</p>`;
  } catch (e) {
    out.innerHTML = `<p class="muted">Não foi possível medir: ${escape((e as Error).message)}</p>`;
  } finally {
    button.disabled = false;
  }
}
async function startBenchmark() {
  if (!world || !gpu) {
    toast("WebGL 2 precisa estar disponível para medir.");
    return;
  }
  const quality = $<HTMLSelectElement>("#bench-quality").value as Quality,
    weather = $<HTMLSelectElement>("#bench-weather").value as Weather,
    target = Number($<HTMLSelectElement>("#bench-target").value),
    duration = Number($<HTMLSelectElement>("#bench-duration").value);
  benchmarkMeta = { quality, weather, target };
  benchmarkRestored = false;
  benchmarkPreparing = true;
  benchmark = new Benchmark(duration);
  $("#start-benchmark").setAttribute("disabled", "");
  $("#benchmark-status").textContent = "PRÉ-CARREGANDO SHADERS…";
  world.setQuality(quality, settings.scale);
  world.setWeather(weather);
  world.setCircuit(defaultCircuit());
  try {
    await world.warmup();
    benchmarkPreparing = false;
    if (document.hidden) throw new Error("Mantenha a aba visível para iniciar.");
    benchmark.start();
    benchmarkClock = 0;
    previous = performance.now();
    for (const el of document.querySelectorAll<HTMLSelectElement>(".benchmark-controls select")) el.disabled = true;
    $("#cancel-benchmark").removeAttribute("hidden");
    $("#benchmark-result").innerHTML = "";
  } catch (e) {
    benchmarkPreparing = false;
    benchmark = undefined;
    world.setQuality(settings.quality, settings.scale);
    world.setWeather(settings.weather);
    $("#start-benchmark").removeAttribute("disabled");
    fail(e);
  }
}
function updateBenchmark() {
  if (!benchmark || page !== "benchmark" || benchmarkPreparing) return;
  const status = $("#benchmark-status");
  const b = benchmark;
  status.textContent =
    b.state === "warmup"
      ? `AQUECIMENTO / ${Math.max(0, 3 - b.elapsed).toFixed(1)} S`
      : b.state === "measuring"
        ? `MEDINDO / ${b.elapsed.toFixed(1)} DE ${b.duration} S`
        : b.state === "done"
          ? "MEDIÇÃO CONCLUÍDA"
          : b.state === "invalid"
            ? b.reason
            : "PRONTO";
  $("#benchmark-progress").style.width = `${b.progress * 100}%`;
  if (!b.active && !benchmarkRestored) {
    benchmarkRestored = true;
    $("#start-benchmark").removeAttribute("disabled");
    $("#cancel-benchmark").setAttribute("hidden", "");
    document
      .querySelectorAll<HTMLSelectElement>(".benchmark-controls select")
      .forEach((el) => (el.disabled = false));
    world?.setQuality(settings.quality, settings.scale);
    world?.setWeather(settings.weather);
    world?.setCircuit(circuit, analysisMemo.get(docKey(circuit)));
  }
}
function showReport(r: BenchmarkReport) {
  $("#benchmark-result").innerHTML =
    `<section class="panel report"><div class="report-top"><div><p class="eyebrow">RELATÓRIO REAL / ${new Date(r.date).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" })}</p><h2>${r.stability}<span>.</span></h2><p>${labels[r.quality]} · ${weatherLabels[r.weather] ?? r.weather} · ${r.width} × ${r.height} · ${r.frames} quadros</p></div><button class="secondary" id="export-report">Exportar relatório ↗</button></div><div class="report-stats">${[
      [r.average.toFixed(1), "FPS MÉDIO"],
      [r.low1.toFixed(1), "1% LOW"],
      [r.minimum.toFixed(1), "FPS MÍNIMO"],
      [r.p95.toFixed(2) + " ms", "TEMPO P95"],
      [r.median.toFixed(2) + " ms", "MEDIANA"],
      [String(r.spikes), "QUADROS > 50 MS"],
    ]
      .map(([v, l]) => `<div><strong>${v}</strong><span>${l}</span></div>`)
      .join(
        "",
      )}</div><p class="small muted">CPU (submissão JavaScript): ${r.cpuMs.toFixed(2)} ms · GPU: ${r.gpuMs === null ? "consulta indisponível" : r.gpuMs.toFixed(2) + " ms"} · Heap JS: ${r.heapMB === null ? "indisponível" : r.heapMB.toFixed(1) + " MB"}. VRAM física não é consultável.</p><p class="small muted">${escape(r.gpu)} / ${escape(r.backend)}</p><div class="recommendation"><p><strong>${r.low1 >= r.target * 0.8 ? "Perfil atual dentro da margem avaliada." : "A meta não foi sustentada no 1% low."}</strong><br>${r.recommendation === "low" && r.low1 < r.target * 0.8 ? "Mesmo no perfil mínimo, reduza a resolução ou a meta e meça novamente." : `Perfil sugerido: ${labels[r.recommendation]}. Meça novamente para confirmar.`}</p><button class="primary" id="accept-recommendation">Aplicar ${labels[r.recommendation]}</button></div><p class="small muted">1% low = 1000 ÷ média dos 1% quadros mais lentos (arredondado para cima). FPS médio = quadros ÷ tempo; mínimo = inverso do quadro mais lento. Comparações dependem de GPU, resolução, perfil e clima.</p></section>`;
  $("#accept-recommendation").onclick = () => {
    if (benchmark?.active || benchmarkPreparing) {
      toast("Aguarde o fim da medição para aplicar outro perfil.");
      return;
    }
    settings.quality = r.recommendation;
    applySettings();
    toast("Perfil aplicado. Execute outro teste para confirmar.");
  };
  $("#export-report").onclick = () => download("apex-benchmark.json", JSON.stringify(r, null, 2));
}
function renderHistory() {
  if (!$("#history")) return;
  const recent = [...reports].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 10);
  $("#history").innerHTML = recent.length
    ? `<div class="table-scroll"><table><thead><tr><th>DATA</th><th>PERFIL / CLIMA</th><th>RESOLUÇÃO</th><th>FPS MÉDIO</th><th>1% LOW</th><th></th></tr></thead><tbody>${recent.map((r) => `<tr><td>${new Date(r.date).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" })}</td><td>${labels[r.quality]} / ${weatherLabels[r.weather] ?? r.weather}</td><td>${r.width} × ${r.height}</td><td>${r.average.toFixed(1)}</td><td>${r.low1.toFixed(1)}</td><td><button class="text-btn" data-report="${r.id}">Ver relatório ↗</button></td></tr>`).join("")}</tbody></table></div>`
    : '<div class="empty-state">Nenhum resultado ainda. Execute seu primeiro benchmark para criar uma referência.</div>';
  document
    .querySelectorAll<HTMLElement>("[data-report]")
    .forEach((el) => (el.onclick = () => showReport(reports.find((r) => r.id === el.dataset.report)!)));
}
function renderSettings() {
  const status = physics?.status;
  $("#content").innerHTML =
    heading(
      "DETALHE E FLUIDEZ, NA SUA MEDIDA",
      "Acerte seu setup.",
      "Os controles abaixo afetam a renderização real. Suas preferências são salvas neste navegador.",
    ) +
    `<div class="settings-layout"><section class="panel settings-panel"><h3>Qualidade gráfica</h3><div class="quality-options">${Object.entries(
      labels,
    )
      .map(
        ([v, l]) =>
          `<button data-quality="${v}" class="${settings.quality === v ? "selected" : ""}"><strong>${l}</strong><small>${v === "low" ? "Sombras desligadas · vegetação reduzida" : v === "medium" ? "Sombras de 1024 · detalhe equilibrado" : "Sombras de 2048 · vegetação densa"}</small></button>`,
      )
      .join(
        "",
      )}</div><label>Escala de renderização <output id="scale-value">${Math.round(settings.scale * 100)}%</output><input id="resolution-scale" type="range" min="0.5" max="1.5" step="0.1" value="${settings.scale}"></label><label class="toggle-row">Qualidade adaptativa <input id="adaptive" type="checkbox" ${settings.adaptive ? "checked" : ""}></label><p class="small muted">Reduz o perfil após FPS abaixo de 44 por cinco segundos, com intervalo mínimo de 12 segundos entre reduções. Pausada no benchmark.</p><label>Iluminação e clima<select id="setting-weather">${Object.entries(
      weatherLabels,
    )
      .map(([v, l]) => `<option value="${v}" ${settings.weather === v ? "selected" : ""}>${l}</option>`)
      .join(
        "",
      )}</select></label><label class="toggle-row">Exibir FPS na corrida <input id="fps-toggle" type="checkbox" ${settings.showFps ? "checked" : ""}></label><div class="settings-note">Antialiasing fixo na criação do contexto. Pós-processamento avançado, WebGPU e controle de V-Sync permanecem em desenvolvimento; não são expostos como controles fictícios.</div></section><aside class="panel diagnostics"><span class="eyebrow">DIAGNÓSTICO REAL DO NAVEGADOR</span><h3>O que sua GPU conta.</h3>${gpu ? `<dl><dt>Renderizador</dt><dd>${escape(gpu.name)}</dd><dt>Classificação aproximada</dt><dd>${gpu.kind}</dd><dt>Backend ativo</dt><dd>WebGL 2</dd><dt>API WebGPU exposta</dt><dd>${gpu.webgpuAvailable ? "Sim; backend não implementado" : "Não"}</dd><dt>Limite de textura</dt><dd>${gpu.maxTextureSize} px</dd><dt>Amostras máximas do contexto</dt><dd>${gpu.maxSamples}</dd><dt>Timer de GPU</dt><dd>${gpu.timerQueries ? "Extensão disponível" : "Indisponível"}</dd><dt>VRAM física</dt><dd>Não disponibilizada pela API web</dd></dl>` : `<p>${escape(worldError || "Inicializando…")}</p>`}<dl><dt>Motor físico</dt><dd>${escape(status ? (status.state === "ready" ? `Python ${status.engine} em Pyodide ${status.pyodide} (Web Worker)` : status.message) : "Indisponível")}</dd>${status?.initMs ? `<dt>Carregamento</dt><dd>${status.initMs.toFixed(0)} ms (WebAssembly) + ${status.importMs?.toFixed(0)} ms (motor)</dd>` : ""}</dl><div class="settings-note">O aplicativo solicita alto desempenho; a escolha da GPU cabe ao navegador e ao sistema. No Chrome, ative a aceleração gráfica. No Windows, selecione Chrome em Configurações → Sistema → Tela → Elementos gráficos → Alto desempenho.</div><button class="secondary" id="settings-benchmark">Medir desempenho ${icon("arrow")}</button></aside></div>`;
  document.querySelectorAll<HTMLElement>("[data-quality]").forEach(
    (el) =>
      (el.onclick = () => {
        settings.quality = el.dataset.quality as Quality;
        applySettings();
        renderSettings();
      }),
  );
  $<HTMLInputElement>("#resolution-scale").oninput = (e) => {
    settings.scale = Number((e.target as HTMLInputElement).value);
    $("#scale-value").textContent = `${Math.round(settings.scale * 100)}%`;
    applySettings();
  };
  $<HTMLInputElement>("#adaptive").onchange = (e) => {
    settings.adaptive = (e.target as HTMLInputElement).checked;
    applySettings();
  };
  $<HTMLSelectElement>("#setting-weather").onchange = (e) => {
    settings.weather = (e.target as HTMLSelectElement).value as Weather;
    applySettings();
  };
  $<HTMLInputElement>("#fps-toggle").onchange = (e) => {
    settings.showFps = (e.target as HTMLInputElement).checked;
    applySettings();
  };
  $("#settings-benchmark").onclick = () => navigate("benchmark");
}
function applySettings() {
  if (benchmark?.active || benchmarkPreparing) {
    toast("O perfil permanece fixo durante o benchmark.");
    return;
  }
  world?.setQuality(settings.quality, settings.scale);
  if (!racing) world?.setWeather(settings.weather);
  governor.reset();
  void storage.saveSettings(settings).catch(fail);
}
function renderHelp() {
  $("#content").innerHTML =
    heading(
      "CONHEÇA SUAS FERRAMENTAS",
      "Central do piloto.",
      "Um laboratório de corridas original, sem marcas ou assets comerciais de Fórmula 1.",
    ) +
    `<div class="help-grid"><section class="panel"><span class="eyebrow">01 / VOLANTE NAS MÃOS</span><h3>Controles</h3><div class="binding-list">${[
      ["throttle", "Acelerar"],
      ["brake", "Frear / ré parado"],
      ["left", "Virar à esquerda"],
      ["right", "Virar à direita"],
      ["drs", "DRS"],
    ]
      .map(
        ([key, name]) =>
          `<label>${name}<input class="binding" data-binding="${key}" value="${bindings[key].replace("Key", "")}" readonly aria-label="Reatribuir ${name}"></label>`,
      )
      .join(
        "",
      )}</div><p>Setas também controlam o carro. Espaço freia. <kbd>[</kbd> e <kbd>]</kbd> movem o balanço de freio. <kbd>C</kbd> alterna câmera, <kbd>R</kbd> reposiciona no último ponto, <kbd>F</kbd> mostra FPS e <kbd>Esc</kbd> pausa.</p><p class="muted">Clique em uma tecla acima e pressione a nova tecla para reatribuir. Gamepad: analógico esquerdo, RT acelera, LT freia (entrada analógica, sem filtro de teclado). Toque: controles aparecem na corrida.</p></section><section class="panel"><span class="eyebrow">02 / DO TRAÇO À PISTA</span><h3>Seu primeiro circuito</h3><ol><li>Abra Criar circuito e desenhe um laço sem cruzamentos.</li><li>Arraste os pontos; ajuste largura, elevação e inclinação lateral.</li><li>Escolha o asfalto e a área de escape.</li><li>Valide: o analisador Python calcula curvas, zonas de frenagem, linha de corrida, perfis de velocidade e riscos.</li><li>Teste ao volante e confirme a homologação da versão.</li></ol><p class="muted">Editar a estrutura invalida a homologação e os dados físicos derivados. Arquivos importados sempre precisam de nova validação.</p></section><section class="panel"><span class="eyebrow">03 / O QUE ESTÁ IMPLEMENTADO</span><h3>Física em Python, no seu navegador.</h3><p>O motor físico, o analisador de pistas e a IA rodam em Python (Pyodide/WebAssembly) dentro de um Web Worker: modelo de quatro rodas, pneus com temperatura, desgaste e água, aerodinâmica com efeito-solo e DRS, suspensão, motor e câmbio. Todos os carros usam as mesmas leis físicas.</p><p class="muted">Parâmetros dos carros são estimativas de ordem de grandeza por época, não especificações oficiais. Não há calibração com telemetria real; regras oficiais, pit stops, replay e recriações históricas precisas ainda não foram implementados.</p></section><section class="panel"><span class="eyebrow">04 / COMO MEDIR</span><h3>Sua RTX, seus resultados.</h3><p>Abra o jogo no Chrome do notebook, conectado à energia. Escolha perfil e clima; execute 30 segundos sem mudar de aba ou redimensionar. Compare FPS médio com 1% low e p95. Meça também o motor físico na mesma página.</p><p class="muted">A máquina na nuvem não mede sua RTX 3050. Gráficos do menu são elementos ilustrativos; relatórios são produzidos somente por medições completas.</p></section></div>`;
  document.querySelectorAll<HTMLInputElement>(".binding").forEach(
    (el) =>
      (el.onkeydown = (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (["Escape", "Tab"].includes(e.code)) return;
        if (Object.values(bindings).includes(e.code) && bindings[el.dataset.binding!] !== e.code) {
          toast("Essa tecla já está atribuída.");
          return;
        }
        bindings[el.dataset.binding!] = e.code;
        el.value = e.code.replace("Key", "");
        void storage.savePreference("bindings", bindings).catch(fail);
        el.blur();
      }),
  );
}
async function prepareRace() {
  const d = modal(
    `<p class="eyebrow">PRONTO PARA A PRÓXIMA VOLTA?</p><h2>Prepare sua corrida.</h2><p class="muted" id="race-analysis-status">Analisando o circuito com o motor físico Python…</p>`,
  );
  let result: TrackAnalysis;
  try {
    result = await ensureAnalysis(circuit, "full");
  } catch (e) {
    $("#race-analysis-status").textContent = `Motor físico indisponível: ${(e as Error).message}`;
    return;
  }
  if (!d.open) return;
  const homologated = isHomologated(circuit, result);
  const vehicleOptions = vehicles.length
    ? vehicles
    : [{ id: "ground-effect-2022", name: "Monoposto genérico de efeito-solo", era: "2022–2025 (referência)" } as VehicleSummary];
  modal(
    `<p class="eyebrow">PRONTO PARA A PRÓXIMA VOLTA?</p><h2>Prepare sua corrida.</h2><div class="race-track-summary">${miniTrack(circuit)}<div><h3>${escape(circuit.name)}</h3><p>${(result.length / 1000).toFixed(2)} km · versão ${circuit.version}${result.stats ? ` · ${result.stats.corners} curvas` : ""}</p></div></div>${
      result.errors.length
        ? `<div class="validation invalid">${result.errors.map((e) => `<p>${escape(e.message)}</p>`).join("")}<button id="fix-circuit" class="primary">Revisar no editor</button></div>`
        : `<form id="race-form"><div class="form-row"><label>Carros no grid<select name="cars">${Array.from({ length: 9 }, (_, i) => `<option value="${i + 2}" ${i + 2 === raceOptions.cars ? "selected" : ""}>${i + 2} carros</option>`).join("")}</select></label><label>Voltas<select name="laps">${[1, 3, 5, 10].map((n) => `<option ${n === raceOptions.laps ? "selected" : ""}>${n}</option>`).join("")}</select></label></div><label>Habilidade dos adversários<select name="difficulty">${DIFFICULTIES.map((v, i) => `<option value="${i}" ${i === raceOptions.difficulty ? "selected" : ""}>${v}</option>`).join("")}</select></label><label>Condições<select name="weather">${Object.entries(
            weatherLabels,
          )
            .map(([v, l]) => `<option value="${v}" ${v === raceOptions.weather ? "selected" : ""}>${l}</option>`)
            .join("")}</select></label><label>Carro (todos usam o mesmo)<select name="vehicle">${vehicleOptions
            .map((v) => `<option value="${v.id}" ${v.id === raceOptions.vehicle ? "selected" : ""}>${escape(v.name)} — ${escape(v.era)}</option>`)
            .join("")}</select></label><div class="form-row"><label>Assistências<select name="assists">${Object.entries(assistLabels)
            .map(([v, l]) => `<option value="${v}" ${v === raceOptions.assists ? "selected" : ""}>${l}</option>`)
            .join("")}</select></label><label>Balanço de freio <output id="bias-value">${Math.round((raceOptions.brakeBias ?? 0.57) * 100)}% dianteiro</output><input type="range" name="brakeBias" min="0.5" max="0.7" step="0.01" value="${raceOptions.brakeBias ?? 0.57}"></label></div>${result.warnings.length ? `<p class="small muted">${result.warnings.map((w) => escape(w.message)).join(" · ")}</p>` : ""}${homologated ? "" : `<label class="confirm-row"><input type="checkbox" required name="homologate"> Confirmo a homologação deste traçado, versão ${circuit.version} (análise ${escape(result.hash.slice(0, 8))}). Grid, zebras e escapes automáticos; sem pit lane.</label>`}<p class="small muted">Parâmetros dos carros são estimativas por época, não especificações oficiais. A IA usa a mesma física e o mesmo carro que você.</p><button class="primary full" type="submit">${icon("flag")} Ir para o grid</button></form>`
    }`,
  );
  const dialog = $<HTMLDialogElement>("#modal");
  if ($("#fix-circuit"))
    $("#fix-circuit").onclick = () => {
      dialog.close();
      navigate("editor");
    };
  if ($("#race-form")) {
    const form = $<HTMLFormElement>("#race-form");
    const bias = form.querySelector<HTMLInputElement>('input[name="brakeBias"]')!;
    bias.oninput = () => ($("#bias-value").textContent = `${Math.round(Number(bias.value) * 100)}% dianteiro`);
    form.onsubmit = (e) => {
      e.preventDefault();
      const data = new FormData(form);
      circuit = { ...circuit, homologatedVersion: circuit.version, homologatedHash: result.hash };
      const raceSettings: RaceSettings = {
        cars: Number(data.get("cars")),
        laps: Number(data.get("laps")),
        difficulty: Number(data.get("difficulty")),
        weather: data.get("weather") as Weather,
        vehicle: String(data.get("vehicle")),
        assists: data.get("assists") as AssistLevel,
        brakeBias: Number(data.get("brakeBias")),
      };
      raceOptions = raceSettings;
      void storage.savePreference("race", raceSettings).catch(fail);
      dialog.close();
      void startRace(false, raceSettings);
    };
  }
}
async function startRace(training = false, options?: RaceSettings) {
  if (!world) {
    toast("A corrida precisa de WebGL 2. Veja o diagnóstico.");
    return;
  }
  if (circuit.points.length < 4) {
    toast("Crie ao menos quatro pontos para testar.");
    return;
  }
  const raceSettings: RaceSettings = options ?? {
    ...raceOptions,
    cars: 1,
    laps: 3,
    weather: settings.weather,
  };
  try {
    toast(training ? "Preparando treino livre…" : "Preparando o grid…");
    const analysis = await ensureAnalysis(circuit, "full");
    if (!analysis.valid) throw new Error(analysis.errors[0]?.message ?? "Circuito inválido.");
    const client = await requirePhysics();
    const config: SessionConfig = {
      cars: raceSettings.cars,
      laps: raceSettings.laps,
      difficulty: raceSettings.difficulty,
      weather: raceSettings.weather,
      vehicle: raceSettings.vehicle ?? "ground-effect-2022",
      assists: raceSettings.assists ?? "full",
      brakeBias: raceSettings.brakeBias,
      seed: Math.floor(Math.random() * 2 ** 31),
      training,
    };
    raceInfo = await client.startSession<SessionInfo>(analysis.hash, config);
    raceAnalysis = analysis;
    raceOptions = { ...raceOptions, weather: raceSettings.weather };
    brakeBias = raceInfo.brake_bias;
    const g = raceInfo.vehicle.geometry;
    world.setVehicle({
      wheelbase: g.wheelbase,
      front: g.front,
      rear: g.rear,
      radiusFront: g.radius_front,
      radiusRear: g.radius_rear,
    });
    client.onSurface = (grid) => world?.setSurface(grid);
    client.onEvents = handleEvents;
    client.advance(0, { throttle: 0, brake: 0, steer: 0, drs: false, analog: false });
  } catch (e) {
    fail(e);
    return;
  }
  racing = true;
  paused = false;
  countdown = training ? 0 : 3;
  finishedShown = false;
  raceContacts = 0;
  keys.clear();
  page = "race";
  raceOptions.weather = raceSettings.weather;
  navigate("race");
  previous = performance.now();
  if (training) toast("Treino livre: use W/A/S/D ou as setas. R reposiciona o carro.");
}
function endRace() {
  racing = false;
  paused = false;
  physics?.endSession();
  if (physics) {
    physics.onSurface = () => {};
    physics.onEvents = () => {};
  }
}
function handleEvents(events: PhysicsEvent[]) {
  for (const event of events) {
    if (event.type === "contact" && event.cars?.includes(0) && (event.dv ?? 0) > 2) {
      raceContacts++;
      toast(`Contato com outro carro (${(event.dv ?? 0).toFixed(1)} m/s).`);
    } else if (event.type === "barrier" && event.car === 0 && (event.dv ?? 0) > 4) {
      toast(`Impacto na barreira (${(event.dv ?? 0).toFixed(1)} m/s).`);
    }
  }
}
function renderRace() {
  $("#content").innerHTML =
    `<section class="race-stage"><div id="scene-mount"></div><div class="race-top"><div class="race-session"><span class="eyebrow">${escape(circuit.name)}</span><strong id="race-lap">VOLTA 1</strong></div><div id="race-position">P1</div><button class="secondary" id="pause-race">Ⅱ Pausar</button></div><div id="leaderboard" class="leaderboard"></div><div id="countdown" class="countdown">${countdown > 0 ? Math.ceil(countdown) : ""}</div><canvas id="minimap" width="240" height="170" aria-label="Minimapa da corrida"></canvas><div class="telemetry"><div class="rpm-bar"><i id="rpm-bar"></i></div><div class="speed-row"><div><strong id="speed">0</strong><span>KM/H</span></div><b id="gear">N</b></div><div class="telemetry-extra" id="car-condition"></div><div class="telemetry-extra" id="car-tyres"></div></div><div id="lap-timing" class="lap-timing"></div><div id="fps" class="fps"></div><div class="race-controls"><span>W A S D <b>DIRIGIR</b></span><span>E <b>DRS</b></span><span>[ ] <b>FREIO</b></span><span>C <b>CÂMERA</b></span><span>R <b>REPOSICIONAR</b></span><span>ESC <b>PAUSA</b></span></div><div class="touch-controls"><button data-touch="ArrowLeft" aria-label="Virar à esquerda">←</button><button data-touch="ArrowRight" aria-label="Virar à direita">→</button><button data-touch="ArrowDown" aria-label="Frear">FREIO</button><button data-touch="ArrowUp" aria-label="Acelerar">GÁS</button></div></section>`;
  $("#pause-race").onclick = togglePause;
  document.querySelectorAll<HTMLElement>("[data-touch]").forEach((el) => {
    el.onpointerdown = (e) => {
      e.preventDefault();
      el.setPointerCapture(e.pointerId);
      keys.add(el.dataset.touch!);
    };
    el.onpointerup = el.onpointercancel = () => keys.delete(el.dataset.touch!);
  });
}
function togglePause() {
  if (!racing) return;
  paused = !paused;
  keys.clear();
  if (!paused) {
    previous = performance.now();
    $<HTMLDialogElement>("#modal").close();
    return;
  }
  const d = modal(
    `<p class="eyebrow">RESPIRA. AJUSTA. VOLTA.</p><h2>Nos boxes.</h2><p>Corrida pausada. ${currentFps.toFixed(0)} FPS · ${labels[settings.quality]}</p><div class="stack"><button class="primary" id="resume">Continuar corrida</button><label>Qualidade<select id="pause-quality">${Object.entries(
      labels,
    )
      .map(([v, l]) => `<option value="${v}" ${v === settings.quality ? "selected" : ""}>${l}</option>`)
      .join(
        "",
      )}</select></label><label>Balanço de freio <output id="pause-bias">${Math.round(brakeBias * 100)}% dianteiro</output><input type="range" id="pause-bias-input" min="0.5" max="0.7" step="0.01" value="${brakeBias}"></label><button class="secondary" id="leave-race">Sair para o menu</button></div>`,
  );
  d.onclose = () => {
    paused = false;
    keys.clear();
    previous = performance.now();
  };
  $("#resume").onclick = togglePause;
  $("#leave-race").onclick = () => {
    d.close();
    navigate("home");
  };
  $<HTMLSelectElement>("#pause-quality").onchange = (e) => {
    settings.quality = (e.target as HTMLSelectElement).value as Quality;
    applySettings();
  };
  $<HTMLInputElement>("#pause-bias-input").oninput = (e) => void setBias(Number((e.target as HTMLInputElement).value));
}
async function setBias(value: number) {
  if (!physics || !racing) return;
  brakeBias = await physics.setBrakeBias(value);
  if ($("#pause-bias")) $("#pause-bias").textContent = `${Math.round(brakeBias * 100)}% dianteiro`;
}
function formatTime(t: number | null) {
  if (t === null) return "—";
  return `${Math.floor(t / 60)}:${(t % 60).toFixed(3).padStart(6, "0")}`;
}
function currentCars(): CarState[] | undefined {
  const frame = physics?.frame;
  if (!racing || !frame || !raceInfo) return undefined;
  const alpha = paused ? 1 : Math.min(1, Math.max(0, frame.header.alpha));
  return frame.curr.map((car, i) => ({
    ...interpolateCar(frame.prev[i], car, alpha),
    name: i === 0 ? "Você" : `Piloto ${i + 1}`,
    color: COLORS[i % COLORS.length],
  }));
}
function updateRaceHud(cars: CarState[] | undefined) {
  const frame = physics?.frame;
  if (!racing || page !== "race" || !frame || !cars) return;
  const car = cars[0];
  $("#speed").textContent = String(Math.round(Math.abs(car.speed) * 3.6));
  $("#gear").textContent = frame.header.playerReverse ? "R" : car.gear <= 0 ? "N" : String(car.gear);
  $("#rpm-bar").style.width = `${Math.min(100, (car.rpm / 15000) * 100)}%`;
  $("#race-position").innerHTML = `P${frame.header.playerPosition}<small> / ${cars.length}</small>`;
  $("#race-lap").textContent = `VOLTA ${Math.min(car.lap, raceInfo?.laps ?? car.lap)}`;
  $("#car-condition").textContent = `COMB. ${((car.fuel / 100) * (raceInfo?.vehicle.fuel_capacity ?? 100)).toFixed(1)} KG · DANOS ${car.damage.toFixed(0)}% · FREIO ${Math.round(brakeBias * 100)}%${raceInfo?.vehicle.ers_power_kw ? ` · ERS ${car.ers.toFixed(0)}%` : ""}${car.drsOpen ? " · DRS ABERTO" : car.drsAllowed ? " · DRS DISPONÍVEL" : ""}`;
  const t = car.tyreTemp.map((v) => Math.round(v));
  $("#car-tyres").textContent = `PNEUS ${t[0]}/${t[1]} · ${t[2]}/${t[3]} °C · DESGASTE ${car.tyreWear.toFixed(1)}%${car.waterFilm > 0.05 ? ` · ÁGUA ${car.waterFilm.toFixed(1)} MM` : ""}`;
  $("#lap-timing").innerHTML = `<span>VOLTA ATUAL <b>${formatTime(car.lapTime)}</b></span><span>MELHOR VOLTA <b>${formatTime(car.bestLap)}</b></span>`;
  $("#countdown").textContent = countdown > 0 ? String(Math.ceil(countdown)) : "";
  const latency = physics?.latencies.length ? physics.latencies[physics.latencies.length - 1] : 0;
  $("#fps").textContent = settings.showFps
    ? `${currentFps.toFixed(0)} FPS · ${labels[settings.quality]} · física ${frame.header.physicsMs.toFixed(1)} ms · latência ${latency.toFixed(1)} ms`
    : "";
  $("#leaderboard").innerHTML = [...cars]
    .sort((a, b) => a.position - b.position)
    .slice(0, 6)
    .map(
      (c) =>
        `<div class="${c.id === 0 ? "player" : ""}"><b>${c.position}</b><i style="background:${c.color}"></i><span>${escape(c.name)}</span><small>${c.id === 0 ? "VOCÊ" : `V${c.lap}`}</small></div>`,
    )
    .join("");
  drawMinimap(cars);
  if (frame.header.finished && !finishedShown) {
    finishedShown = true;
    paused = true;
    const result = [...cars].sort((a, b) => a.position - b.position);
    modal(
      `<p class="eyebrow">BANDEIRADA FINAL</p><h2>Uma volta além.</h2><p>Você terminou em P${frame.header.playerPosition}. Melhor volta: ${formatTime(car.bestLap)}${raceContacts ? ` · ${raceContacts} contato(s)` : ""}</p><ol class="finish-list">${result.map((c) => `<li>${escape(c.name)} <span>${formatTime(c.bestLap)}</span></li>`).join("")}</ol><button class="primary" id="finish-home">Voltar ao laboratório</button>`,
    );
    $("#finish-home").onclick = () => {
      $<HTMLDialogElement>("#modal").close();
      navigate("home");
    };
    void persistRecord(car.bestLap).catch(fail);
  }
}
function drawMinimap(cars: CarState[]) {
  const canvas = $<HTMLCanvasElement>("#minimap"),
    table = raceAnalysis?.physics_table;
  if (!canvas || !table) return;
  const ctx = canvas.getContext("2d")!;
  const minX = Math.min(...table.x),
    maxX = Math.max(...table.x),
    minZ = Math.min(...table.z),
    maxZ = Math.max(...table.z),
    scale = Math.min(210 / (maxX - minX), 140 / (maxZ - minZ));
  ctx.clearRect(0, 0, 240, 170);
  ctx.beginPath();
  for (let i = 0; i < table.x.length; i += 2) {
    const x = 15 + (table.x[i] - minX) * scale,
      z = 15 + (table.z[i] - minZ) * scale;
    if (i) ctx.lineTo(x, z);
    else ctx.moveTo(x, z);
  }
  ctx.closePath();
  ctx.strokeStyle = "#a7b2a980";
  ctx.lineWidth = 3;
  ctx.stroke();
  cars.forEach((c) => {
    ctx.beginPath();
    ctx.arc(15 + (c.x - minX) * scale, 15 + (c.z - minZ) * scale, c.id === 0 ? 4 : 2.5, 0, Math.PI * 2);
    ctx.fillStyle = c.id === 0 ? "#ff603d" : c.color;
    ctx.fill();
  });
}
async function persistRecord(time: number | null) {
  if (time === null) return;
  const key = `record:${circuit.id}@${circuit.version}:${raceInfo?.vehicle.id ?? ""}`;
  const previousBest = await storage.readPreference<number>(key);
  if (previousBest === undefined || time < previousBest) await storage.savePreference(key, time);
}
let analogInput = false;
function readInput(): DriverInput & { analog: boolean } {
  const input = {
    throttle: keys.has(bindings.throttle) || keys.has("ArrowUp") ? 1 : 0,
    brake: keys.has(bindings.brake) || keys.has("ArrowDown") || keys.has("Space") ? 1 : 0,
    steer:
      (keys.has(bindings.right) || keys.has("ArrowRight") ? 1 : 0) -
      (keys.has(bindings.left) || keys.has("ArrowLeft") ? 1 : 0),
    drs: keys.has(bindings.drs),
    analog: false,
  };
  const pad = navigator.getGamepads?.()[0];
  if (pad) {
    const axis = pad.axes[0] || 0;
    const rt = pad.buttons[7]?.value || 0,
      lt = pad.buttons[6]?.value || 0;
    if (Math.abs(axis) > 0.12 || rt > 0.05 || lt > 0.05) analogInput = true;
    if (Math.abs(axis) > 0.12) input.steer = axis;
    input.throttle = Math.max(input.throttle, rt);
    input.brake = Math.max(input.brake, lt);
    input.drs = input.drs || !!pad.buttons[0]?.pressed;
  }
  if (keys.size) analogInput = false;
  input.analog = analogInput;
  return input;
}
function cycleCamera() {
  const modes: CameraMode[] = ["chase", "cockpit", "rear", "broadcast"];
  settings.camera = modes[(modes.indexOf(settings.camera) + 1) % modes.length];
  void storage.saveSettings(settings).catch(fail);
  toast(
    `Câmera: ${{ chase: "externa", cockpit: "cockpit", rear: "traseira", broadcast: "transmissão", orbit: "órbita" }[settings.camera]}`,
  );
}
window.addEventListener("keydown", (e) => {
  if ((e.target as HTMLElement).matches("input,select,textarea")) return;
  if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Space"].includes(e.code) && racing) e.preventDefault();
  keys.add(e.code);
  if (e.repeat) return;
  if (e.code === "Escape" && racing) {
    e.preventDefault();
    togglePause();
  }
  if (e.code === "KeyC" && racing) cycleCamera();
  if (e.code === "KeyR" && racing) physics?.reset();
  if (e.code === "KeyF" && racing) settings.showFps = !settings.showFps;
  if (e.code === "BracketLeft" && racing) void setBias(brakeBias - 0.01);
  if (e.code === "BracketRight" && racing) void setBias(brakeBias + 0.01);
});
window.addEventListener("keyup", (e) => keys.delete(e.code));
window.addEventListener("blur", () => keys.clear());
document.addEventListener("visibilitychange", () => {
  keys.clear();
  previous = performance.now();
  benchmark?.invalidate("Aba oculta: medição invalidada. Repita com esta aba visível.");
  if (racing && document.hidden && !paused) togglePause();
  updateBenchmark();
});
window.addEventListener("resize", () => {
  benchmark?.invalidate("Janela redimensionada: a resolução mudou. Repita o teste.");
  world?.resize();
  updateBenchmark();
});
function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function startPhysics() {
  try {
    physics = new PhysicsClient();
    physics.onStatus = () => updatePhysicsBadge();
    updatePhysicsBadge();
    physics
      .whenReady()
      .then(async () => {
        vehicles = await physics!.vehicles<VehicleSummary[]>();
        updatePhysicsBadge();
      })
      .catch((e) => {
        updatePhysicsBadge();
        console.error(e);
      });
  } catch (e) {
    console.error(e);
  }
}
async function init() {
  startPhysics();
  let savedGraphics = false;
  try {
    const [saved, tracks, history, controls, race] = await Promise.all([
      storage.settings(),
      storage.circuits(),
      storage.benchmarks(),
      storage.readPreference<Record<string, string>>("bindings"),
      storage.readPreference<RaceSettings>("race"),
    ]);
    if (race) raceOptions = { ...raceOptions, ...race };
    if (saved && ["low", "medium", "high"].includes(saved.quality) && Number.isFinite(saved.scale)) savedGraphics = true;
    if (savedGraphics && saved)
      settings = {
        ...settings,
        ...saved,
        scale: Math.min(1.5, Math.max(0.5, saved.scale)),
        weather: saved.weather in weatherLabels ? saved.weather : "sun",
      };
    savedCircuits = tracks;
    reports = history;
    if (controls) Object.assign(bindings, controls);
  } catch (e) {
    fail(e);
  }
  navigate("home");
  try {
    world = new RacingWorld(worldHost);
    gpu = inspectGpu(world.renderer);
    document.documentElement.dataset.gpu = gpu.kind === "Software" ? "software" : "hardware";
    if (!savedGraphics && gpu.kind === "Software") settings = { ...settings, quality: "low", scale: 0.6 };
    const forcedQuality = new URLSearchParams(location.search).get("quality");
    if (forcedQuality && ["low", "medium", "high"].includes(forcedQuality))
      settings = { ...settings, quality: forcedQuality as Quality, adaptive: false };
    world.setQuality(settings.quality, settings.scale);
    world.setCircuit(circuit);
    world.setWeather(settings.weather);
    if (page === "home") {
      renderHome();
      mountWorld();
    }
    timer = gpu.kind === "Software" ? undefined : new GpuTimer(world.renderer.getContext() as WebGL2RenderingContext);
    world.renderer.domElement.addEventListener("webglcontextlost", (e: Event) => {
      e.preventDefault();
      benchmark?.invalidate("Contexto gráfico perdido. Recarregue antes de medir novamente.");
      if (racing) paused = true;
      toast("Contexto gráfico perdido. Recarregue a página.");
    });
    world.renderer.domElement.addEventListener("webglcontextrestored", () => location.reload());
    updateGpuLabel();
  } catch (e) {
    worldError = e instanceof Error ? e.message : String(e);
    mountWorld();
    updateGpuLabel();
  }
  requestAnimationFrame(frame);
}
function frame(now: number) {
  requestAnimationFrame(frame);
  // rAF timestamps can precede a performance.now() taken after an await.
  const raw = Math.max(0, (now - previous) / 1000);
  previous = now;
  if (document.hidden) return;
  const dt = Math.min(raw, 0.1);
  elapsed += dt;
  fpsTime += raw;
  fpsFrames++;
  if (fpsTime > 0.5) {
    currentFps = fpsFrames / fpsTime;
    fpsFrames = 0;
    fpsTime = 0;
  }
  // Physics runs in the worker at a fixed 1/120 s step; here we only send the
  // elapsed wall time and the controls, then interpolate the returned states.
  if (racing && physics && !paused) {
    if (countdown > 0) countdown -= raw;
    else physics.advance(Math.min(raw, 0.25), readInput());
  }
  if (benchmark?.active) benchmarkClock += raw;
  const cpuStart = performance.now();
  const gpuMs = benchmark?.active ? (timer?.poll() ?? null) : null;
  if (benchmark?.active) timer?.begin();
  const renderCars = currentCars();
  if (worldHost.isConnected && !$<HTMLDialogElement>("#modal").open)
    world?.render(renderCars, benchmark?.active ? benchmarkClock : elapsed, racing ? settings.camera : "orbit");
  if (benchmark?.active) timer?.end();
  const cpuMs = performance.now() - cpuStart;
  if (benchmark?.active) benchmark.tick(raw * 1000, cpuMs, gpuMs);
  if (benchmark?.state === "done" && benchmarkMeta && gpu && world) {
    const canvas = world.renderer.domElement;
    const report = benchmark.report({ ...benchmarkMeta, gpu: gpu.name, width: canvas.width, height: canvas.height });
    benchmarkMeta = undefined;
    reports.push(report);
    showReport(report);
    renderHistory();
    void storage.saveBenchmark(report).catch(fail);
    updateBenchmark();
  }
  hudElapsed += dt;
  if (hudElapsed > 0.1) {
    hudElapsed = 0;
    updateRaceHud(renderCars);
    updateBenchmark();
  }
  if (settings.adaptive && !benchmark?.active && !benchmarkPreparing && world && page !== "editor" && !paused && worldHost.isConnected) {
    const next = governor.tick(dt, settings.quality);
    if (next) {
      settings.quality = next;
      world.setQuality(next, settings.scale);
      void storage.saveSettings(settings).catch(fail);
      if ($("#quality-label")) $("#quality-label").textContent = labels[next];
      toast(`Qualidade ajustada para ${labels[next]} após FPS baixo sustentado.`);
    }
  }
}
void init();

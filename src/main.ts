import "@fontsource/barlow-condensed/latin-600.css";
import "@fontsource/barlow-condensed/latin-800-italic.css";
import "@fontsource/dm-sans/latin-400.css";
import "@fontsource/dm-sans/latin-500.css";
import "@fontsource/dm-sans/latin-600.css";
import "./style.css";
import type {
  Circuit,
  GraphicsSettings,
  Quality,
  Weather,
  DriverInput,
  CameraMode,
  RaceSettings,
  CarState,
} from "./core/types";
import { defaultCircuit, importCircuit } from "./circuits/model";
import { sampleCircuit, validateCircuit } from "./circuits/geometry";
import { mountCircuitEditor } from "./circuits/editor";
import { RaceSimulation } from "./racing/simulation";
import { RacingWorld } from "./graphics/world";
import { inspectGpu, type GpuInfo } from "./graphics/gpu";
import {
  Benchmark,
  GpuTimer,
  type BenchmarkReport,
} from "./graphics/benchmark";
import { AdaptiveQuality } from "./graphics/quality";
import { storage } from "./storage/database";

const $ = <T extends Element = HTMLElement>(s: string) =>
  document.querySelector<T>(s)!;
const escape = (s: unknown) =>
  String(s).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ]!,
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
const weatherLabels = {
  sun: "Dia ensolarado",
  night: "Corrida noturna",
  rain: "Chuva intensa",
};
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
};
let raceGeometry = sampleCircuit(circuit);
let previousCars: CarState[] = [];
let sim: RaceSimulation | undefined,
  paused = false,
  countdown = 0,
  finishedShown = false,
  editor: ReturnType<typeof mountCircuitEditor> | undefined;
let benchmarkRestored = true,
  benchmarkPreparing = false,
  benchmarkClock = 0;
let benchmark: Benchmark | undefined,
  benchmarkMeta:
    | { quality: Quality; weather: Weather; target: number }
    | undefined;
let previous = performance.now(),
  elapsed = 0,
  accumulator = 0,
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
};
const worldHost = document.createElement("div");
worldHost.id = "world";
let worldError = "";
let previewTimeout: ReturnType<typeof setTimeout>;
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
  )}</nav><div class="sidebar-bottom"><div class="build-badge"><span class="status-dot"></span> PRIMEIRA VERSÃO JOGÁVEL</div><button data-nav="help" class="help-btn">${icon("help")} Ajuda e controles</button><div class="sidebar-foot">FEITO PARA QUEM<br>DESENHA O PRÓPRIO CAMINHO.<span>v0.1 / WEBGL 2</span></div></div></aside><div class="main"><header class="topbar"><div class="breadcrumb">WORKSPACE <span>/</span> <strong id="page-name">VISÃO GERAL</strong></div><div class="topbar-right"><span class="local-label"><span class="status-dot"></span> SALVO NESTE NAVEGADOR</span><button class="profile" data-nav="help" aria-label="Ajuda do piloto">HL</button></div></header><main id="content"></main><footer class="footer"><span>APEX RACING LAB <b>© 2026</b></span><span>CRIAR. TESTAR. ACELERAR. <i>↗</i></span></footer></div><div id="toast" role="status" aria-live="polite"></div><dialog id="modal"></dialog>`;
for (const button of document.querySelectorAll<HTMLElement>("[data-nav]"))
  button.onclick = () => navigate(button.dataset.nav!);
window.addEventListener("hashchange", () => {
  const id = location.hash.slice(1);
  if (
    ["home", "editor", "circuits", "benchmark", "settings", "help"].includes(
      id,
    ) &&
    id !== page
  )
    navigate(id);
});
function toast(message: string) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("visible");
  setTimeout(() => el.classList.remove("visible"), 5000);
}
function fail(error: unknown) {
  toast(
    error instanceof Error
      ? error.message
      : "Não foi possível concluir a operação.",
  );
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
  if (sim && page === "race" && id !== "race") {
    sim = undefined;
    paused = false;
  }
  editor?.dispose();
  editor = undefined;
  clearTimeout(previewTimeout);
  page = id;
  location.hash = id;
  document
    .querySelectorAll("[data-nav]")
    .forEach((el) =>
      el.classList.toggle("active", (el as HTMLElement).dataset.nav === id),
    );
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
    world?.setCircuit(circuit);
    world?.setWeather(settings.weather);
  }
  governor.reset();
}
function renderHome() {
  $("#content").innerHTML =
    `<section class="welcome-row"><div><p class="eyebrow">O ASFALTO É SÓ O COMEÇO</p><h1>Bem-vindo ao seu laboratório.</h1></div><div class="session-tag">SESSÃO LOCAL <span>01</span></div></section><section class="hero"><div id="scene-mount"></div><div class="hero-shade"></div><div class="hero-copy"><span class="pill"><span></span> RACING SANDBOX / 3D</span><h2>SUA PISTA.<br>SEU <em>LIMITE.</em></h2><p>Do primeiro traço à última curva.<br>Crie circuitos, acerte sua máquina e assuma o volante.</p><div class="hero-actions"><button class="primary" id="quick-race">${icon("play")} Iniciar corrida</button><button class="hero-secondary" id="hero-editor">Criar circuito ${icon("arrow")}</button></div></div><div class="hero-number">01<span>/ A PRÓXIMA VOLTA É SUA</span></div><div class="scene-caption"><span class="live-dot"></span> CENA 3D EM TEMPO REAL <b>WEBGL 2</b></div><div class="hero-circuit"><span>CIRCUITO EM DESTAQUE</span><strong>${escape(circuit.name)}</strong><small>TRAÇADO ORIGINAL · ${(sampleCircuit(circuit).length / 1000).toFixed(2)} KM</small></div></section><section class="system-strip"><div class="system-gpu"><span class="chip-icon">▧</span><div><label>SEU HARDWARE</label><strong id="gpu-short">Detectando GPU…</strong></div></div><div><label>PERFIL GRÁFICO</label><strong><i class="green-dot"></i><span id="quality-label">${labels[settings.quality]}</span></strong></div><div><label>RENDERIZAÇÃO</label><strong>WebGL 2 <span class="muted">/ Three.js</span></strong></div><button id="diagnostic-btn">Ver diagnóstico ${icon("arrow")}</button></section><section class="section-title"><h2>Da ideia à pista<span>.</span></h2><span>SEU FLUXO, SEM DESVIOS</span></section><div class="launch-grid"><button class="launch-card editor-card" id="card-editor"><div class="card-top"><span>01 / CONSTRUIR</span>${icon("arrow")}</div><div class="track-art">${miniTrack(circuit)}<span class="point-art p1"></span><span class="point-art p2"></span></div><h3>Uma pista. Sua assinatura.</h3><p>Desenhe curvas, ajuste elevações<br>e transforme linhas em circuitos 3D.</p><div class="card-bottom">ABRIR EDITOR ${icon("plus")}</div></button><button class="launch-card benchmark-card" id="card-benchmark"><div class="card-top"><span>02 / CALIBRAR</span>${icon("arrow")}</div><div class="graph-art"><div class="graph-bars">${[30, 43, 36, 58, 49, 73, 65, 82, 76, 91, 84, 96, 86, 100, 91, 110, 100, 114, 105, 122].map((h) => `<i style="height:${h}px"></i>`).join("")}</div><span>MEDIR. AJUSTAR. EVOLUIR.</span></div><h3>Conheça sua máquina.</h3><p>Benchmark real, qualidade sob medida.<br>Encontre seu equilíbrio entre detalhe e fluidez.</p><div class="card-bottom">EXECUTAR BENCHMARK ${icon("chart")}</div></button><button class="launch-card race-card" id="card-race"><div class="card-top"><span>03 / COMPETIR</span>${icon("arrow")}</div><div class="race-art"><div class="speed-lines"></div><span>LIGHTS<br><em>OUT.</em></span><div class="start-lights"><i></i><i></i><i></i><i></i><i></i></div></div><h3>Cada milésimo importa.</h3><p>Até 10 carros, cinco níveis de IA.<br>Encontre seu ritmo e conquiste a próxima volta.</p><div class="card-bottom">PREPARAR CORRIDA ${icon("flag")}</div></button></div><section class="bottom-note"><div>${icon("track")} <span>UM ESPAÇO PARA EXPERIMENTAR.<b> Física aproximada. Circuitos originais. Possibilidades abertas.</b></span></div><button id="learn-more">Conheça o projeto ↗</button></section>`;
  $("#quick-race").onclick = prepareRace;
  $("#hero-editor").onclick = () => navigate("editor");
  $("#card-editor").onclick = () => navigate("editor");
  $("#card-benchmark").onclick = () => navigate("benchmark");
  $("#card-race").onclick = prepareRace;
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
      "Desenhe um traçado fechado ou arraste os pontos. A prévia 3D acompanha suas alterações.",
    ) +
    `<div class="editor-layout"><section class="panel"><div id="editor-mount"></div></section><aside class="panel inspector"><div class="panel-heading"><span>PRÉVIA 3D</span><span class="status-dot"></span></div><div id="scene-mount" class="editor-preview"></div><div id="circuit-info"></div><div class="stack"><button id="validate" class="primary">${icon("check")} Validar circuito</button><button id="save-track" class="secondary">Salvar versão local</button><button id="test-track" class="secondary">Testar ao volante</button><button id="export-track" class="text-btn">Exportar JSON ↗</button></div><div id="validation" aria-live="polite"></div></aside></div>`;
  editor = mountCircuitEditor($("#editor-mount"), circuit, (c) => {
    circuit = c;
    updateCircuitInfo();
    clearTimeout(previewTimeout);
    previewTimeout = setTimeout(() => world?.setCircuit(circuit), 180);
    $("#validation").innerHTML = "";
  });
  updateCircuitInfo();
  $("#validate").onclick = () => showValidation();
  $("#save-track").onclick = () => saveCircuit();
  $("#test-track").onclick = () => startRace(true);
  $("#export-track").onclick = () =>
    download(
      `${circuit.name}.json`,
      JSON.stringify({ schemaVersion: 1, ...circuit }, null, 2),
    );
}
function updateCircuitInfo() {
  const geo = sampleCircuit(circuit);
  if ($("#circuit-info"))
    $("#circuit-info").innerHTML =
      `<h3>${escape(circuit.name)}</h3><div class="stat-pair"><span>${(geo.length / 1000).toFixed(2)}<small>KM DE TRAÇADO</small></span><span>${circuit.points.length}<small>PONTOS DE CONTROLE</small></span></div><p class="muted">Versão ${circuit.version} · ${circuit.homologatedVersion === circuit.version ? "Homologada" : "Aguardando homologação"}</p>`;
}
function showValidation() {
  const result = validateCircuit(circuit);
  $("#validation").innerHTML =
    `<div class="validation ${result.errors.length ? "invalid" : "valid"}"><strong>${result.errors.length ? "Revise o traçado" : "Geometria validada"}</strong>${result.errors.map((s) => `<p>× ${escape(s)}</p>`).join("")}${result.warnings.map((s) => `<p>△ ${escape(s)}</p>`).join("")}${result.errors.length ? "" : `<p>Grid e checkpoints gerados automaticamente. Pit lane ainda indisponível.</p><button class="primary" id="homologate">Confirmar homologação v${circuit.version}</button>`}</div>`;
  if ($("#homologate"))
    $("#homologate").onclick = () => {
      circuit = { ...circuit, homologatedVersion: circuit.version };
      editor?.setCircuit(circuit);
      updateCircuitInfo();
      toast(
        "Versão homologada. Qualquer alteração estrutural exige nova validação.",
      );
      $("#validation").innerHTML =
        '<p class="success">✓ Circuito homologado para corrida.</p>';
    };
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
    `<div class="toolbar"><button class="primary" id="new-track">${icon("plus")} Criar circuito</button><button class="secondary" id="import-btn">Importar JSON</button><input type="file" id="import-file" accept="application/json,.json" hidden></div><div class="circuit-grid">${[defaultCircuit(), ...savedCircuits].map((c, i) => `<button class="circuit-card panel" data-circuit="${i}">${miniTrack(c)}<span class="eyebrow">${i === 0 ? "CIRCUITO ORIGINAL" : "VERSÃO LOCAL"} / V${c.version}</span><h3>${escape(c.name)}</h3><p>${(sampleCircuit(c).length / 1000).toFixed(2)} km · ${c.homologatedVersion === c.version ? "Homologado" : "Validar antes de correr"}</p><span class="text-link">Selecionar circuito ↗</span></button>`).join("")}</div><section class="section-title"><h2>Arquivo histórico<span>.</span></h2><span>REFERÊNCIAS INFORMATIVAS</span></section><p class="muted">Sem geometrias licenciadas e verificadas, estes locais não estão disponíveis para corrida. O catálogo completo de layouts desde 1980 permanece em desenvolvimento.</p><div class="historical-grid">${[
      [
        "Monza",
        "Itália",
        "Autódromo permanente",
        "https://www.formula1.com/en/racing/2025/italy/circuit",
      ],
      [
        "Interlagos",
        "Brasil",
        "Autódromo permanente",
        "https://www.formula1.com/en/racing/2025/brazil/circuit",
      ],
      [
        "Suzuka",
        "Japão",
        "Autódromo permanente",
        "https://www.formula1.com/en/racing/2025/japan/circuit",
      ],
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
      homologatedVersion: undefined,
    };
    navigate("editor");
  };
  $("#import-btn").onclick = () => $<HTMLInputElement>("#import-file").click();
  $<HTMLInputElement>("#import-file").onchange = async (e) => {
    const file = (e.target as HTMLInputElement).files?.[0];
    if (!file) return;
    try {
      if (file.size > 1_000_000)
        throw new Error("O arquivo deve ter no máximo 1 MB.");
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
        circuit = [defaultCircuit(), ...savedCircuits][
          Number(el.dataset.circuit)
        ];
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
      .map(
        ([v, l]) =>
          `<option value="${v}" ${v === settings.quality ? "selected" : ""}>${l}</option>`,
      )
      .join(
        "",
      )}</select></label><label>Cenário<select id="bench-weather">${Object.entries(
      weatherLabels,
    )
      .map(([v, l]) => `<option value="${v}">${l}</option>`)
      .join(
        "",
      )}</select></label><div class="form-row"><label>Meta<select id="bench-target"><option>30</option><option selected>60</option><option>120</option></select></label><label>Duração<select id="bench-duration"><option value="30">30 segundos</option><option value="45">45 segundos</option><option value="60">60 segundos</option></select></label></div><button class="primary" id="start-benchmark">${icon("play")} Iniciar benchmark real</button><button class="secondary" id="cancel-benchmark" hidden>Cancelar medição</button><p class="small muted">Adaptação gráfica suspensa durante o teste. Mantenha esta aba visível e o tamanho da janela fixo. Nenhum resultado estima o desempenho da RTX 3050.</p></aside></div><div id="benchmark-result"></div><section class="section-title"><h2>Histórico de medições<span>.</span></h2><span>SALVO LOCALMENTE</span></section><div id="history"></div>`;
  $("#start-benchmark").onclick = () => void startBenchmark();
  $("#cancel-benchmark").onclick = () => {
    benchmark?.invalidate("Medição cancelada pelo piloto.");
    updateBenchmark();
  };
  renderHistory();
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
    if (document.hidden)
      throw new Error("Mantenha a aba visível para iniciar.");
    benchmark.start();
    benchmarkClock = 0;
    previous = performance.now();
    for (const el of document.querySelectorAll<HTMLSelectElement>(
      ".benchmark-controls select",
    ))
      el.disabled = true;
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
    world?.setCircuit(circuit);
  }
}
function showReport(r: BenchmarkReport) {
  $("#benchmark-result").innerHTML =
    `<section class="panel report"><div class="report-top"><div><p class="eyebrow">RELATÓRIO REAL / ${new Date(r.date).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" })}</p><h2>${r.stability}<span>.</span></h2><p>${labels[r.quality]} · ${weatherLabels[r.weather]} · ${r.width} × ${r.height} · ${r.frames} quadros</p></div><button class="secondary" id="export-report">Exportar relatório ↗</button></div><div class="report-stats">${[
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
  $("#export-report").onclick = () =>
    download("apex-benchmark.json", JSON.stringify(r, null, 2));
}
function renderHistory() {
  if (!$("#history")) return;
  const recent = [...reports]
    .sort((a, b) => b.date.localeCompare(a.date))
    .slice(0, 10);
  $("#history").innerHTML = recent.length
    ? `<div class="table-scroll"><table><thead><tr><th>DATA</th><th>PERFIL / CLIMA</th><th>RESOLUÇÃO</th><th>FPS MÉDIO</th><th>1% LOW</th><th></th></tr></thead><tbody>${recent.map((r) => `<tr><td>${new Date(r.date).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" })}</td><td>${labels[r.quality]} / ${weatherLabels[r.weather]}</td><td>${r.width} × ${r.height}</td><td>${r.average.toFixed(1)}</td><td>${r.low1.toFixed(1)}</td><td><button class="text-btn" data-report="${r.id}">Ver relatório ↗</button></td></tr>`).join("")}</tbody></table></div>`
    : '<div class="empty-state">Nenhum resultado ainda. Execute seu primeiro benchmark para criar uma referência.</div>';
  document
    .querySelectorAll<HTMLElement>("[data-report]")
    .forEach(
      (el) =>
        (el.onclick = () =>
          showReport(reports.find((r) => r.id === el.dataset.report)!)),
    );
}
function renderSettings() {
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
      .map(
        ([v, l]) =>
          `<option value="${v}" ${settings.weather === v ? "selected" : ""}>${l}</option>`,
      )
      .join(
        "",
      )}</select></label><label class="toggle-row">Exibir FPS na corrida <input id="fps-toggle" type="checkbox" ${settings.showFps ? "checked" : ""}></label><div class="settings-note">Antialiasing fixo na criação do contexto. Pós-processamento avançado, WebGPU e controle de V-Sync permanecem em desenvolvimento; não são expostos como controles fictícios.</div></section><aside class="panel diagnostics"><span class="eyebrow">DIAGNÓSTICO REAL DO NAVEGADOR</span><h3>O que sua GPU conta.</h3>${gpu ? `<dl><dt>Renderizador</dt><dd>${escape(gpu.name)}</dd><dt>Classificação aproximada</dt><dd>${gpu.kind}</dd><dt>Backend ativo</dt><dd>WebGL 2</dd><dt>API WebGPU exposta</dt><dd>${gpu.webgpuAvailable ? "Sim; backend não implementado" : "Não"}</dd><dt>Limite de textura</dt><dd>${gpu.maxTextureSize} px</dd><dt>Amostras máximas do contexto</dt><dd>${gpu.maxSamples}</dd><dt>Timer de GPU</dt><dd>${gpu.timerQueries ? "Extensão disponível" : "Indisponível"}</dd><dt>VRAM física</dt><dd>Não disponibilizada pela API web</dd></dl>` : `<p>${escape(worldError || "Inicializando…")}</p>`}<div class="settings-note">O aplicativo solicita alto desempenho; a escolha da GPU cabe ao navegador e ao sistema. No Chrome, ative a aceleração gráfica. No Windows, selecione Chrome em Configurações → Sistema → Tela → Elementos gráficos → Alto desempenho.</div><button class="secondary" id="settings-benchmark">Medir desempenho ${icon("arrow")}</button></aside></div>`;
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
  world?.setWeather(settings.weather);
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
      ["brake", "Frear"],
      ["left", "Virar à esquerda"],
      ["right", "Virar à direita"],
    ]
      .map(
        ([key, name]) =>
          `<label>${name}<input class="binding" data-binding="${key}" value="${bindings[key].replace("Key", "")}" readonly aria-label="Reatribuir ${name}"></label>`,
      )
      .join(
        "",
      )}</div><p>Setas também controlam o carro. Espaço freia. <kbd>C</kbd> alterna câmera, <kbd>R</kbd> reposiciona no último ponto, <kbd>F</kbd> mostra FPS e <kbd>Esc</kbd> pausa.</p><p class="muted">Clique em uma tecla acima e pressione a nova tecla para reatribuir. Gamepad: analógico esquerdo, RT acelera, LT freia. Toque: controles aparecem na corrida.</p></section><section class="panel"><span class="eyebrow">02 / DO TRAÇO À PISTA</span><h3>Seu primeiro circuito</h3><ol><li>Abra Criar circuito e desenhe um laço sem cruzamentos.</li><li>Arraste os pontos; ajuste largura e elevação.</li><li>Valide o traçado e leia os avisos.</li><li>Confirme a homologação da versão para liberar a corrida.</li><li>Salve e exporte JSON para conservar seu trabalho.</li></ol><p class="muted">Editar a estrutura invalida a homologação. Arquivos importados sempre precisam de nova validação.</p></section><section class="panel"><span class="eyebrow">03 / O QUE ESTÁ IMPLEMENTADO</span><h3>Uma base jogável, com limites claros.</h3><p>Renderização WebGL 2, carros procedurais originais, circuito 2D/3D, benchmark real, condução com passo fixo, IA, voltas e armazenamento IndexedDB.</p><p class="muted">A física é uma aproximação, sem certificação de simulador profissional. Modelos comerciais detalhados, suspensão completa, pneus avançados, regras oficiais, pit stops, replay e recriações históricas precisas ainda não foram implementados.</p></section><section class="panel"><span class="eyebrow">04 / COMO MEDIR</span><h3>Sua RTX, seus resultados.</h3><p>Abra o jogo no Chrome do notebook, conectado à energia. Escolha perfil e clima; execute 30 segundos sem mudar de aba ou redimensionar. Compare FPS médio com 1% low e p95.</p><p class="muted">A máquina na nuvem não mede sua RTX 3050. Gráficos do menu são elementos ilustrativos; relatórios são produzidos somente por medições completas.</p></section></div>`;
  document.querySelectorAll<HTMLInputElement>(".binding").forEach(
    (el) =>
      (el.onkeydown = (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (["Escape", "Tab"].includes(e.code)) return;
        if (
          Object.values(bindings).includes(e.code) &&
          bindings[el.dataset.binding!] !== e.code
        ) {
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
function prepareRace() {
  const result = validateCircuit(circuit);
  const d = modal(
    `<p class="eyebrow">PRONTO PARA A PRÓXIMA VOLTA?</p><h2>Prepare sua corrida.</h2><div class="race-track-summary">${miniTrack(circuit)}<div><h3>${escape(circuit.name)}</h3><p>${(result.length / 1000).toFixed(2)} km · versão ${circuit.version}</p></div></div>${
      result.errors.length
        ? `<div class="validation invalid">${result.errors.map((e) => `<p>${escape(e)}</p>`).join("")}<button id="fix-circuit" class="primary">Revisar no editor</button></div>`
        : `<form id="race-form"><div class="form-row"><label>Carros no grid<select name="cars">${Array.from({ length: 9 }, (_, i) => `<option value="${i + 2}" ${i + 2 === raceOptions.cars ? "selected" : ""}>${i + 2} carros</option>`).join("")}</select></label><label>Voltas<select name="laps">${[1, 3, 5, 10].map((n) => `<option ${n === raceOptions.laps ? "selected" : ""}>${n}</option>`).join("")}</select></label></div><label>Habilidade dos adversários<select name="difficulty">${["Fácil", "Intermediário", "Médio", "Difícil", "Realista (experimental)"].map((v, i) => `<option value="${i}" ${i === raceOptions.difficulty ? "selected" : ""}>${v}</option>`).join("")}</select></label><label>Condições<select name="weather">${Object.entries(
            weatherLabels,
          )
            .map(
              ([v, l]) =>
                `<option value="${v}" ${v === raceOptions.weather ? "selected" : ""}>${l}</option>`,
            )
            .join(
              "",
            )}</select></label>${result.warnings.length ? `<p class="small muted">${result.warnings.map(escape).join(" · ")}</p>` : ""}${circuit.homologatedVersion === circuit.version ? "" : `<label class="confirm-row"><input type="checkbox" required name="homologate"> Confirmo a homologação deste traçado, versão ${circuit.version}. Grid e checkpoints automáticos; sem pit lane.</label>`}<p class="small muted">Física aproximada. IA experimental com direção e frenagem; sem regras oficiais ou estratégia de boxes.</p><button class="primary full" type="submit">${icon("flag")} Ir para o grid</button></form>`
    }`,
  );
  if ($("#fix-circuit"))
    $("#fix-circuit").onclick = () => {
      d.close();
      navigate("editor");
    };
  if ($("#race-form"))
    $<HTMLFormElement>("#race-form").onsubmit = (e) => {
      e.preventDefault();
      const data = new FormData(e.currentTarget as HTMLFormElement);
      circuit = { ...circuit, homologatedVersion: circuit.version };
      const raceSettings = {
        cars: Number(data.get("cars")),
        laps: Number(data.get("laps")),
        difficulty: Number(data.get("difficulty")),
        weather: data.get("weather") as Weather,
      };
      raceOptions = raceSettings;
      void storage.savePreference("race", raceSettings).catch(fail);
      d.close();
      startRace(false, raceSettings);
    };
}
function startRace(
  training = false,
  options = { cars: 2, laps: 3, difficulty: 2, weather: settings.weather },
) {
  if (!world) {
    toast("A corrida precisa de WebGL 2. Veja o diagnóstico.");
    return;
  }
  if (circuit.points.length < 4) {
    toast("Crie ao menos quatro pontos para testar.");
    return;
  }
  raceGeometry = sampleCircuit(circuit);
  try {
    sim = new RaceSimulation(circuit, options);
  } catch (e) {
    fail(e);
    return;
  }
  previousCars = sim.cars.map((c) => ({ ...c }));
  paused = false;
  countdown = training ? 0 : 3;
  finishedShown = false;
  accumulator = 0;
  keys.clear();
  page = "race";
  settings.weather = options.weather;
  navigate("race");
  world.setWeather(options.weather);
  previous = performance.now();
  if (training)
    toast("Treino livre: use W/A/S/D ou as setas. R reposiciona o carro.");
}
function renderRace() {
  $("#content").innerHTML =
    `<section class="race-stage"><div id="scene-mount"></div><div class="race-top"><div class="race-session"><span class="eyebrow">${escape(circuit.name)}</span><strong id="race-lap">VOLTA 1</strong></div><div id="race-position">P1</div><button class="secondary" id="pause-race">Ⅱ Pausar</button></div><div id="leaderboard" class="leaderboard"></div><div id="countdown" class="countdown">${countdown > 0 ? Math.ceil(countdown) : ""}</div><canvas id="minimap" width="240" height="170" aria-label="Minimapa da corrida"></canvas><div class="telemetry"><div class="rpm-bar"><i id="rpm-bar"></i></div><div class="speed-row"><div><strong id="speed">0</strong><span>KM/H</span></div><b id="gear">N</b></div><div class="telemetry-extra" id="car-condition"></div></div><div id="lap-timing" class="lap-timing"></div><div id="fps" class="fps"></div><div class="race-controls"><span>W A S D <b>DIRIGIR</b></span><span>C <b>CÂMERA</b></span><span>R <b>REPOSICIONAR</b></span><span>ESC <b>PAUSA</b></span></div><div class="touch-controls"><button data-touch="ArrowLeft" aria-label="Virar à esquerda">←</button><button data-touch="ArrowRight" aria-label="Virar à direita">→</button><button data-touch="ArrowDown" aria-label="Frear">FREIO</button><button data-touch="ArrowUp" aria-label="Acelerar">GÁS</button></div></section>`;
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
  if (!sim) return;
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
      .map(
        ([v, l]) =>
          `<option value="${v}" ${v === settings.quality ? "selected" : ""}>${l}</option>`,
      )
      .join(
        "",
      )}</select></label><button class="secondary" id="leave-race">Sair para o menu</button></div>`,
  );
  d.onclose = () => {
    paused = false;
    keys.clear();
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
}
function formatTime(t: number | null) {
  if (t === null) return "—";
  return `${Math.floor(t / 60)}:${(t % 60).toFixed(3).padStart(6, "0")}`;
}
function updateRaceHud() {
  if (!sim || page !== "race") return;
  const car = sim.cars[0];
  $("#speed").textContent = String(Math.round(Math.abs(car.speed) * 3.6));
  $("#gear").textContent = String(car.gear);
  $("#rpm-bar").style.width = `${(car.rpm / 15000) * 100}%`;
  $("#race-position").innerHTML =
    `P${sim.position}<small> / ${sim.cars.length}</small>`;
  $("#race-lap").textContent = `VOLTA ${Math.min(car.lap, sim.settings.laps)}`;
  $("#car-condition").textContent =
    `COMB. ${car.fuel.toFixed(0)}% · PNEUS ${(100 - car.tyreWear).toFixed(0)}% · DANOS ${car.damage.toFixed(0)}%`;
  $("#lap-timing").innerHTML =
    `<span>VOLTA ATUAL <b>${formatTime(car.lapTime)}</b></span><span>MELHOR VOLTA <b>${formatTime(car.bestLap)}</b></span>`;
  $("#countdown").textContent =
    countdown > 0 ? String(Math.ceil(countdown)) : "";
  $("#fps").textContent = settings.showFps
    ? `${currentFps.toFixed(0)} FPS · ${labels[settings.quality]} · WebGL 2`
    : "";
  $("#leaderboard").innerHTML = sim.standings
    .slice(0, 6)
    .map(
      (c, i) =>
        `<div class="${c.id === 0 ? "player" : ""}"><b>${i + 1}</b><i style="background:${c.color}"></i><span>${escape(c.name)}</span><small>${c.id === 0 ? "VOCÊ" : `V${c.lap}`}</small></div>`,
    )
    .join("");
  const canvas = $<HTMLCanvasElement>("#minimap"),
    ctx = canvas.getContext("2d")!,
    samples = raceGeometry.samples;
  const minX = Math.min(...samples.map((p) => p.x)),
    maxX = Math.max(...samples.map((p) => p.x)),
    minZ = Math.min(...samples.map((p) => p.z)),
    maxZ = Math.max(...samples.map((p) => p.z)),
    scale = Math.min(210 / (maxX - minX), 140 / (maxZ - minZ));
  ctx.clearRect(0, 0, 240, 170);
  ctx.beginPath();
  samples.forEach((p, i) => {
    const x = 15 + (p.x - minX) * scale,
      z = 15 + (p.z - minZ) * scale;
    i ? ctx.lineTo(x, z) : ctx.moveTo(x, z);
  });
  ctx.closePath();
  ctx.strokeStyle = "#a7b2a980";
  ctx.lineWidth = 3;
  ctx.stroke();
  sim.cars.forEach((c) => {
    ctx.beginPath();
    ctx.arc(
      15 + (c.x - minX) * scale,
      15 + (c.z - minZ) * scale,
      c.id === 0 ? 4 : 2.5,
      0,
      Math.PI * 2,
    );
    ctx.fillStyle = c.id === 0 ? "#ff603d" : c.color;
    ctx.fill();
  });
  if (sim.finished && !finishedShown) {
    finishedShown = true;
    paused = true;
    const result = sim.standings;
    modal(
      `<p class="eyebrow">BANDEIRADA FINAL</p><h2>Uma volta além.</h2><p>Você terminou em P${sim.position}. Melhor volta: ${formatTime(car.bestLap)}</p><ol class="finish-list">${result.map((c) => `<li>${escape(c.name)} <span>${formatTime(c.bestLap)}</span></li>`).join("")}</ol><button class="primary" id="finish-home">Voltar ao laboratório</button>`,
    );
    $("#finish-home").onclick = () => {
      $<HTMLDialogElement>("#modal").close();
      navigate("home");
    };
    void persistRecord(car.bestLap).catch(fail);
  }
}
async function persistRecord(time: number | null) {
  if (time === null) return;
  const key = `record:${circuit.id}@${circuit.version}`;
  const previousBest = await storage.readPreference<number>(key);
  if (previousBest === undefined || time < previousBest)
    await storage.savePreference(key, time);
}
function readInput(): DriverInput {
  const input = {
    throttle: keys.has(bindings.throttle) || keys.has("ArrowUp") ? 1 : 0,
    brake:
      keys.has(bindings.brake) || keys.has("ArrowDown") || keys.has("Space")
        ? 1
        : 0,
    steer:
      (keys.has(bindings.right) || keys.has("ArrowRight") ? 1 : 0) -
      (keys.has(bindings.left) || keys.has("ArrowLeft") ? 1 : 0),
    drs: false,
  };
  const pad = navigator.getGamepads?.()[0];
  if (pad) {
    const axis = pad.axes[0] || 0;
    if (Math.abs(axis) > 0.12) input.steer = axis;
    input.throttle = Math.max(input.throttle, pad.buttons[7]?.value || 0);
    input.brake = Math.max(input.brake, pad.buttons[6]?.value || 0);
  }
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
  if (
    ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Space"].includes(
      e.code,
    ) &&
    sim
  )
    e.preventDefault();
  keys.add(e.code);
  if (e.repeat) return;
  if (e.code === "Escape" && sim) {
    e.preventDefault();
    togglePause();
  }
  if (e.code === "KeyC" && sim) cycleCamera();
  if (e.code === "KeyR" && sim) sim.resetPlayer();
  if (e.code === "KeyF" && sim) settings.showFps = !settings.showFps;
});
window.addEventListener("keyup", (e) => keys.delete(e.code));
window.addEventListener("blur", () => keys.clear());
document.addEventListener("visibilitychange", () => {
  keys.clear();
  previous = performance.now();
  benchmark?.invalidate(
    "Aba oculta: medição invalidada. Repita com esta aba visível.",
  );
  if (sim && document.hidden && !paused) togglePause();
  updateBenchmark();
});
window.addEventListener("resize", () => {
  benchmark?.invalidate(
    "Janela redimensionada: a resolução mudou. Repita o teste.",
  );
  world?.resize();
  updateBenchmark();
});
function download(name: string, text: string) {
  const url = URL.createObjectURL(
    new Blob([text], { type: "application/json" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function init() {
  let savedGraphics = false;
  try {
    const [saved, tracks, history, controls, race] = await Promise.all([
      storage.settings(),
      storage.circuits(),
      storage.benchmarks(),
      storage.readPreference<Record<string, string>>("bindings"),
      storage.readPreference<RaceSettings>("race"),
    ]);
    if (race) raceOptions = race;
    if (
      saved &&
      ["low", "medium", "high"].includes(saved.quality) &&
      Number.isFinite(saved.scale)
    )
      savedGraphics = true;
    if (savedGraphics && saved)
      settings = {
        ...settings,
        ...saved,
        scale: Math.min(1.5, Math.max(0.5, saved.scale)),
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
    document.documentElement.dataset.gpu =
      gpu.kind === "Software" ? "software" : "hardware";
    if (!savedGraphics && gpu.kind === "Software")
      settings = { ...settings, quality: "low", scale: 0.6 };
    const forcedQuality = new URLSearchParams(location.search).get("quality");
    if (forcedQuality && ["low", "medium", "high"].includes(forcedQuality))
      settings = {
        ...settings,
        quality: forcedQuality as Quality,
        adaptive: false,
      };
    world.setQuality(settings.quality, settings.scale);
    world.setCircuit(circuit);
    world.setWeather(settings.weather);
    if (page === "home") {
      renderHome();
      mountWorld();
    }
    timer =
      gpu.kind === "Software"
        ? undefined
        : new GpuTimer(world.renderer.getContext() as WebGL2RenderingContext);
    world.renderer.domElement.addEventListener(
      "webglcontextlost",
      (e: Event) => {
        e.preventDefault();
        benchmark?.invalidate(
          "Contexto gráfico perdido. Recarregue antes de medir novamente.",
        );
        if (sim) paused = true;
        toast("Contexto gráfico perdido. Recarregue a página.");
      },
    );
    world.renderer.domElement.addEventListener("webglcontextrestored", () =>
      location.reload(),
    );
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
  const raw = (now - previous) / 1000;
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
  if (sim && !paused) {
    if (countdown > 0) countdown -= raw;
    else {
      accumulator += Math.min(raw, 5);
      const input = readInput();
      while (accumulator >= 1 / 120) {
        previousCars = sim.cars.map((c) => ({ ...c }));
        sim.update(1 / 120, input);
        accumulator -= 1 / 120;
      }
    }
  }
  if (benchmark?.active) benchmarkClock += raw;
  const cpuStart = performance.now();
  const gpuMs = benchmark?.active ? (timer?.poll() ?? null) : null;
  if (benchmark?.active) timer?.begin();
  const renderCars = sim?.cars.map((c, i) => {
    const p = previousCars[i] || c,
      alpha = paused ? 1 : accumulator * 120;
    const dh = Math.atan2(
      Math.sin(c.heading - p.heading),
      Math.cos(c.heading - p.heading),
    );
    return {
      ...c,
      x: p.x + (c.x - p.x) * alpha,
      y: p.y + (c.y - p.y) * alpha,
      z: p.z + (c.z - p.z) * alpha,
      heading: p.heading + dh * alpha,
    };
  });
  if (worldHost.isConnected && !$<HTMLDialogElement>("#modal").open)
    world?.render(
      renderCars,
      benchmark?.active ? benchmarkClock : elapsed,
      sim ? settings.camera : "orbit",
    );
  if (benchmark?.active) timer?.end();
  const cpuMs = performance.now() - cpuStart;
  if (benchmark?.active) benchmark.tick(raw * 1000, cpuMs, gpuMs);
  if (benchmark?.state === "done" && benchmarkMeta && gpu && world) {
    const canvas = world.renderer.domElement;
    const report = benchmark.report({
      ...benchmarkMeta,
      gpu: gpu.name,
      width: canvas.width,
      height: canvas.height,
    });
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
    updateRaceHud();
    updateBenchmark();
  }
  if (
    settings.adaptive &&
    !benchmark?.active &&
    !benchmarkPreparing &&
    world &&
    page !== "editor" &&
    !paused &&
    worldHost.isConnected
  ) {
    const next = governor.tick(dt, settings.quality);
    if (next) {
      settings.quality = next;
      world.setQuality(next, settings.scale);
      void storage.saveSettings(settings).catch(fail);
      if ($("#quality-label")) $("#quality-label").textContent = labels[next];
      toast(
        `Qualidade ajustada para ${labels[next]} após FPS baixo sustentado.`,
      );
    }
  }
}
void init();

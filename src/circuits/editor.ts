import type { Circuit, Point3 } from "../core/types";
import { sampleCircuit } from "./geometry";
import { cloneCircuit, reviseCircuit } from "./model";

type State = Pick<Circuit, "name" | "width" | "points" | "surface" | "runoff">;
type Mode = "edit" | "draw" | "add";
const snapshot = (c: Circuit): State => ({
  name: c.name,
  width: c.width,
  points: c.points.map((p) => ({ ...p })),
  surface: c.surface ? { ...c.surface } : undefined,
  runoff: c.runoff ? { ...c.runoff } : undefined,
});
const distance = (a: Point3, b: Point3) => Math.hypot(a.x - b.x, a.z - b.z);

export function mountCircuitEditor(
  container: HTMLElement,
  initial: Circuit,
  onChange: (c: Circuit) => void,
): {
  getCircuit(): Circuit;
  setCircuit(c: Circuit): void;
  dispose(): void;
} {
  const root = document.createElement("section");
  root.className = "circuit-editor";
  root.innerHTML = `<style>
    .ce-tools{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}.ce-tools button{background:#17232b;border:1px solid #36434a;color:#d5e2e9;border-radius:6px;padding:8px 10px;font:inherit;font-size:12px;cursor:pointer}.ce-tools button[aria-pressed=true]{background:#c8ec72;color:#14200e}.ce-tools button:disabled{opacity:.35}
    .ce-map{height:clamp(290px,44vh,470px);background:#101b22;position:relative;overflow:hidden;border:1px solid #34434b;border-radius:8px}.ce-map canvas{width:100%;height:100%;display:block;touch-action:none;cursor:crosshair}.ce-map span{position:absolute;left:14px;top:12px;font:10px monospace;letter-spacing:1px;color:#99acb5;pointer-events:none}
    .ce-fields{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}.ce-fields label{font-size:11px;color:#a0b0bb;display:flex;flex-direction:column;gap:7px}.ce-fields input,.ce-fields select{min-width:0;accent-color:#c8ec72}.ce-fields select{padding:9px;border:1px solid #36434a;border-radius:6px;background:#14212a;color:#e8edf1;font:inherit;font-size:12px}.ce-fields input[type=text]{padding:10px;border:1px solid #36434a;border-radius:6px;background:#14212a;color:#e8edf1}.ce-name{grid-column:1/-1}.ce-status{font-size:12px;line-height:1.6;min-height:36px;color:#a2b5bf}.ce-map:focus-within{outline:1px solid #c8ec72}@media(max-width:520px){.ce-fields{grid-template-columns:1fr}.ce-map{height:310px}}
    </style><div class="ce-tools" role="toolbar" aria-label="Ferramentas do circuito">
    <button type="button" data-mode="edit">↖ Mover</button><button type="button" data-mode="draw">✎ Desenhar</button><button type="button" data-mode="add">＋ Ponto</button><button type="button" data-action="remove">− Ponto</button><button type="button" data-action="undo" aria-label="Desfazer">↶</button><button type="button" data-action="redo" aria-label="Refazer">↷</button><button type="button" data-action="fit">Enquadrar</button></div>
    <div class="ce-map"><canvas tabindex="0" aria-label="Editor da pista: arraste os pontos de controle para editar o circuito"></canvas><span>CIRCUIT DESIGN · VISTA SUPERIOR</span></div>
    <div class="ce-fields"><label class="ce-name">NOME DO CIRCUITO<input data-field="name" aria-label="Nome do circuito" maxlength="80"></label><label>LARGURA <output data-value="width"></output><input data-field="width" aria-label="Largura da pista em metros" type="range" min="8" max="24" step="0.5"></label><label>ELEVAÇÃO DO PONTO <output data-value="height"></output><input data-field="height" aria-label="Elevação do ponto em metros" type="range" min="-30" max="60" step="0.5"></label><label>INCLINAÇÃO LATERAL DO PONTO <output data-value="bank"></output><input data-field="bank" aria-label="Inclinação lateral do ponto em graus" type="range" min="-12" max="12" step="0.5"></label><label>ASFALTO<select data-field="asphalt" aria-label="Tipo de asfalto"><option value="standard">Padrão</option><option value="smooth">Liso (menos desgaste, drena menos)</option><option value="abrasive">Abrasivo (mais aderência e desgaste)</option></select></label><label>ÁREA DE ESCAPE<select data-field="runoff" aria-label="Tipo de área de escape"><option value="mixed">Mista (brita em curvas rápidas)</option><option value="grass">Grama</option><option value="gravel">Brita</option><option value="asphalt">Asfalto</option></select></label><label>LARGURA DO ESCAPE <output data-value="runoffWidth"></output><input data-field="runoffWidth" aria-label="Largura da área de escape em metros" type="range" min="2" max="30" step="0.5"></label></div><p class="ce-status" aria-live="polite"></p>`;
  container.append(root);
  const canvas = root.querySelector("canvas")!;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Canvas 2D não disponível.");
  const ctx = context;
  const name = root.querySelector<HTMLInputElement>("[data-field=name]")!;
  const width = root.querySelector<HTMLInputElement>("[data-field=width]")!;
  const height = root.querySelector<HTMLInputElement>("[data-field=height]")!;
  const bank = root.querySelector<HTMLInputElement>("[data-field=bank]")!;
  const asphalt = root.querySelector<HTMLSelectElement>("[data-field=asphalt]")!;
  const runoffType = root.querySelector<HTMLSelectElement>("[data-field=runoff]")!;
  const runoffWidth = root.querySelector<HTMLInputElement>("[data-field=runoffWidth]")!;
  const bankValue = root.querySelector<HTMLOutputElement>("[data-value=bank]")!;
  const runoffValue = root.querySelector<HTMLOutputElement>("[data-value=runoffWidth]")!;
  const widthValue =
    root.querySelector<HTMLOutputElement>("[data-value=width]")!;
  const heightValue = root.querySelector<HTMLOutputElement>(
    "[data-value=height]",
  )!;
  const status = root.querySelector<HTMLElement>(".ce-status")!;
  const undo = root.querySelector<HTMLButtonElement>("[data-action=undo]")!;
  const redo = root.querySelector<HTMLButtonElement>("[data-action=redo]")!;
  const remove = root.querySelector<HTMLButtonElement>("[data-action=remove]")!;
  const abort = new AbortController(),
    events = { signal: abort.signal };
  let circuit = cloneCircuit(initial),
    history: State[] = [snapshot(initial)],
    cursor = 0;
  let selected = 0,
    mode: Mode = "edit",
    dragging = -1,
    pointer: number | null = null;
  let before: Circuit | null = null,
    drawn: Point3[] = [];
  let w = 600,
    h = 400,
    scale = 1,
    cx = 0,
    cz = 0,
    disposed = false;
  const screen = (p: { x: number; z: number }) => ({
    x: (p.x - cx) * scale + w / 2,
    y: (p.z - cz) * scale + h / 2,
  });
  const world = (e: PointerEvent): Point3 => {
    const b = canvas.getBoundingClientRect();
    return {
      x: (e.clientX - b.left - w / 2) / scale + cx,
      y: 1,
      z: (e.clientY - b.top - h / 2) / scale + cz,
    };
  };
  function fit(): void {
    const xs = circuit.points.map((p) => p.x),
      zs = circuit.points.map((p) => p.z);
    const left = Math.min(...xs),
      right = Math.max(...xs),
      top = Math.min(...zs),
      bottom = Math.max(...zs);
    cx = (left + right) / 2;
    cz = (top + bottom) / 2;
    scale = Math.max(
      0.01,
      Math.min(
        (w - 100) / Math.max(right - left, 250),
        (h - 80) / Math.max(bottom - top, 200),
      ),
    );
  }
  function sync(): void {
    name.value = circuit.name;
    width.value = String(circuit.width);
    widthValue.textContent = `${circuit.width.toFixed(1)} m`;
    const p = circuit.points[selected];
    height.disabled = !p;
    height.value = String(p?.y ?? 0);
    heightValue.textContent = p
      ? `${p.y.toFixed(1)} m · ponto ${selected + 1}`
      : "selecione um ponto";
    bank.disabled = !p;
    bank.value = String(p?.bank ?? 0);
    bankValue.textContent = p ? `${(p.bank ?? 0).toFixed(1)}° · ${(p.bank ?? 0) > 0 ? "esquerda mais alta" : (p.bank ?? 0) < 0 ? "direita mais alta" : "plano"}` : "selecione um ponto";
    asphalt.value = circuit.surface?.asphalt ?? "standard";
    runoffType.value = circuit.runoff?.type ?? "mixed";
    runoffWidth.value = String(circuit.runoff?.width ?? 6.5);
    runoffValue.textContent = `${Number(runoffWidth.value).toFixed(1)} m`;
    undo.disabled = cursor === 0;
    redo.disabled = cursor === history.length - 1;
    remove.disabled = circuit.points.length <= 6 || !p;
  }
  function path(points: { x: number; z: number }[], closed = true): void {
    ctx.beginPath();
    points.forEach((p, i) => {
      const s = screen(p);
      if (i) ctx.lineTo(s.x, s.y);
      else ctx.moveTo(s.x, s.y);
    });
    if (closed) ctx.closePath();
  }
  function render(): void {
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#101b22";
    ctx.fillRect(0, 0, w, h);
    const step = 50 * scale,
      zero = screen({ x: 0, z: 0 });
    ctx.strokeStyle = "#1b2e36";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = zero.x % step; x < w; x += step) {
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
    }
    for (let y = zero.y % step; y < h; y += step) {
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
    }
    ctx.stroke();
    const geometry = sampleCircuit(circuit),
      samples = geometry.samples;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    for (const [color, roadWidth] of [
      ["#365734", circuit.width + 12],
      ["#6a7975", circuit.width + 2],
      ["#25323b", circuit.width],
    ] as const) {
      path(samples);
      ctx.strokeStyle = color;
      ctx.lineWidth = Math.max(1, roadWidth * scale);
      ctx.stroke();
    }
    path(samples);
    ctx.strokeStyle = "#c7ed75";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 6]);
    ctx.stroke();
    ctx.setLineDash([]);
    const first = samples[0];
    if (first) {
      const p = screen(first),
        half = (circuit.width * scale) / 2;
      ctx.strokeStyle = "#f2f5e8";
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(p.x - first.tz * half, p.y + first.tx * half);
      ctx.lineTo(p.x + first.tz * half, p.y - first.tx * half);
      ctx.stroke();
      ctx.fillStyle = "#b4c6cf";
      ctx.font = "10px monospace";
      ctx.fillText("S/F", p.x + 12, p.y - 10);
    }
    circuit.points.forEach((p, i) => {
      const s = screen(p),
        active = i === selected;
      ctx.beginPath();
      ctx.arc(s.x, s.y, active ? 6 : 4, 0, Math.PI * 2);
      ctx.fillStyle = active ? "#d9f58d" : "#254447";
      ctx.strokeStyle = active ? "#fff" : "#8fa68d";
      ctx.lineWidth = active ? 2 : 1;
      ctx.fill();
      ctx.stroke();
    });
    if (drawn.length) {
      path(drawn, false);
      ctx.strokeStyle = "#e5f8a0";
      ctx.lineWidth = 3;
      ctx.stroke();
      const s = screen(drawn[0]);
      ctx.beginPath();
      ctx.arc(s.x, s.y, 28, 0, Math.PI * 2);
      ctx.setLineDash([3, 4]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.fillStyle = "#89a0ac";
    ctx.font = "10px monospace";
    ctx.fillText(
      `QUADRA 50 M · ${(geometry.length / 1000).toFixed(2)} KM`,
      14,
      h - 14,
    );
  }
  function commit(
    changes: Partial<State>,
    message = "Traçado alterado. Valide novamente para homologar esta versão.",
  ): void {
    circuit = reviseCircuit(circuit, changes);
    history = history.slice(0, cursor + 1);
    history.push(snapshot(circuit));
    if (history.length > 60) history.shift();
    cursor = history.length - 1;
    selected = Math.max(0, Math.min(selected, circuit.points.length - 1));
    sync();
    render();
    status.textContent = message;
    onChange(cloneCircuit(circuit));
  }
  function travel(delta: number): void {
    const next = cursor + delta;
    if (next < 0 || next >= history.length) return;
    cursor = next;
    circuit = reviseCircuit(circuit, history[cursor]);
    selected = Math.min(selected, circuit.points.length - 1);
    sync();
    render();
    onChange(cloneCircuit(circuit));
  }
  function setMode(value: Mode): void {
    mode = value;
    root
      .querySelectorAll<HTMLButtonElement>("[data-mode]")
      .forEach((b) =>
        b.setAttribute("aria-pressed", String(b.dataset.mode === mode)),
      );
    status.textContent =
      mode === "draw"
        ? "Desenhe uma volta e termine no círculo de início. O desenho substituirá o traçado atual."
        : mode === "add"
          ? "Toque perto de um trecho para inserir um ponto."
          : "Arraste os pontos para editar. Selecione um ponto e ajuste sua elevação abaixo.";
  }
  root
    .querySelectorAll<HTMLButtonElement>("[data-mode]")
    .forEach((b) =>
      b.addEventListener(
        "click",
        () => setMode(b.dataset.mode as Mode),
        events,
      ),
    );
  undo.addEventListener("click", () => travel(-1), events);
  redo.addEventListener("click", () => travel(1), events);
  remove.addEventListener(
    "click",
    () => {
      if (circuit.points.length > 6)
        commit({ points: circuit.points.filter((_, i) => i !== selected) });
    },
    events,
  );
  root.querySelector("[data-action=fit]")!.addEventListener(
    "click",
    () => {
      fit();
      render();
    },
    events,
  );
  name.addEventListener(
    "change",
    () => {
      const value = name.value
        .trim()
        .replace(/[\u0000-\u001f]/g, "")
        .slice(0, 80);
      if (value && value !== circuit.name) commit({ name: value });
      else sync();
    },
    events,
  );
  width.addEventListener(
    "input",
    () => {
      widthValue.textContent = `${Number(width.value).toFixed(1)} m`;
    },
    events,
  );
  width.addEventListener(
    "change",
    () => commit({ width: Number(width.value) }),
    events,
  );
  height.addEventListener(
    "input",
    () => {
      heightValue.textContent = `${Number(height.value).toFixed(1)} m · ponto ${selected + 1}`;
    },
    events,
  );
  height.addEventListener(
    "change",
    () => {
      if (circuit.points[selected])
        commit({
          points: circuit.points.map((p, i) =>
            i === selected ? { ...p, y: Number(height.value) } : p,
          ),
        });
    },
    events,
  );
  bank.addEventListener(
    "input",
    () => {
      bankValue.textContent = `${Number(bank.value).toFixed(1)}° · ponto ${selected + 1}`;
    },
    events,
  );
  bank.addEventListener(
    "change",
    () => {
      if (circuit.points[selected])
        commit({
          points: circuit.points.map((p, i) => (i === selected ? { ...p, bank: Number(bank.value) } : p)),
        });
    },
    events,
  );
  asphalt.addEventListener(
    "change",
    () => commit({ surface: { asphalt: asphalt.value as NonNullable<Circuit["surface"]>["asphalt"] } }),
    events,
  );
  runoffType.addEventListener(
    "change",
    () =>
      commit({
        runoff: {
          type: runoffType.value as NonNullable<Circuit["runoff"]>["type"],
          width: Number(runoffWidth.value),
        },
      }),
    events,
  );
  runoffWidth.addEventListener(
    "input",
    () => {
      runoffValue.textContent = `${Number(runoffWidth.value).toFixed(1)} m`;
    },
    events,
  );
  runoffWidth.addEventListener(
    "change",
    () =>
      commit({
        runoff: {
          type: runoffType.value as NonNullable<Circuit["runoff"]>["type"],
          width: Number(runoffWidth.value),
        },
      }),
    events,
  );
  canvas.addEventListener(
    "pointerdown",
    (e) => {
      if (pointer !== null || e.button !== 0) return;
      canvas.focus({ preventScroll: true });
      const p = world(e);
      if (mode === "draw") {
        drawn = [p];
        pointer = e.pointerId;
        canvas.setPointerCapture(pointer);
        render();
        return;
      }
      if (mode === "add") {
        if (circuit.points.length >= 128) {
          status.textContent = "Limite de 128 pontos atingido.";
          return;
        }
        let edge = 0,
          best = Infinity,
          ratio = 0;
        circuit.points.forEach((a, i) => {
          const b = circuit.points[(i + 1) % circuit.points.length],
            dx = b.x - a.x,
            dz = b.z - a.z;
          const t = Math.max(
            0,
            Math.min(
              1,
              ((p.x - a.x) * dx + (p.z - a.z) * dz) /
                Math.max(1, dx * dx + dz * dz),
            ),
          );
          const d = distance(p, { x: a.x + t * dx, y: 0, z: a.z + t * dz });
          if (d < best) {
            best = d;
            edge = i;
            ratio = t;
          }
        });
        const a = circuit.points[edge],
          b = circuit.points[(edge + 1) % circuit.points.length],
          points = circuit.points.map((q) => ({ ...q }));
        points.splice(edge + 1, 0, {
          ...p,
          y: a.y + (b.y - a.y) * ratio,
          bank: (a.bank ?? 0) + ((b.bank ?? 0) - (a.bank ?? 0)) * ratio,
        });
        selected = edge + 1;
        commit({ points });
        return;
      }
      let nearest = -1,
        best = 18 / scale;
      circuit.points.forEach((q, i) => {
        const d = distance(p, q);
        if (d < best) {
          best = d;
          nearest = i;
        }
      });
      if (nearest < 0) return;
      selected = nearest;
      dragging = nearest;
      before = cloneCircuit(circuit);
      pointer = e.pointerId;
      canvas.setPointerCapture(pointer);
      sync();
      render();
    },
    events,
  );
  canvas.addEventListener(
    "pointermove",
    (e) => {
      if (e.pointerId !== pointer) return;
      const p = world(e);
      if (mode === "draw") {
        if (
          distance(drawn[drawn.length - 1], p) * scale >= 5 &&
          drawn.length < 3000
        )
          drawn.push(p);
        render();
        return;
      }
      if (dragging >= 0) {
        circuit.points[dragging] = {
          ...circuit.points[dragging],
          x: Math.max(-10000, Math.min(10000, p.x)),
          z: Math.max(-10000, Math.min(10000, p.z)),
        };
        render();
      }
    },
    events,
  );
  function finish(e: PointerEvent): void {
    if (e.pointerId !== pointer) return;
    if (canvas.hasPointerCapture(e.pointerId))
      canvas.releasePointerCapture(e.pointerId);
    pointer = null;
    if (mode === "draw") {
      const raw = drawn;
      drawn = [];
      if (
        e.type === "pointercancel" ||
        raw.length < 8 ||
        distance(raw[0], world(e)) * scale > 28
      ) {
        status.textContent =
          "Volta incompleta. Termine o desenho dentro do círculo de início.";
        render();
        return;
      }
      const total = raw.reduce(
          (sum, p, i) => sum + (i ? distance(p, raw[i - 1]) : 0),
          0,
        ),
        spacing = Math.max(12, total / 48),
        points: Point3[] = [raw[0]];
      let sum = 0;
      for (let i = 1; i < raw.length; i++) {
        sum += distance(raw[i - 1], raw[i]);
        if (sum >= spacing) {
          points.push(raw[i]);
          sum = 0;
        }
      }
      if (distance(points[0], points[points.length - 1]) < spacing * 0.8)
        points.pop();
      if (points.length < 6) {
        status.textContent =
          "Desenhe uma volta maior, com pelo menos 6 pontos.";
        render();
        return;
      }
      selected = 0;
      commit({ points: points.slice(0, 128) });
      setMode("edit");
      return;
    }
    if (dragging >= 0 && before) {
      const points = circuit.points.map((p) => ({ ...p })),
        previous = before;
      circuit = previous;
      before = null;
      dragging = -1;
      if (
        e.type !== "pointercancel" &&
        points.some((p, i) => distance(p, previous.points[i]) > 0.05)
      )
        commit({ points });
      else render();
    }
  }
  canvas.addEventListener("pointerup", finish, events);
  canvas.addEventListener("pointercancel", finish, events);
  canvas.addEventListener(
    "keydown",
    (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        travel(e.shiftKey ? 1 : -1);
      }
      if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        remove.click();
      }
    },
    events,
  );
  function resize(): void {
    if (disposed) return;
    const b = canvas.parentElement!.getBoundingClientRect();
    w = Math.max(160, b.width);
    h = Math.max(200, b.height);
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(w * ratio);
    canvas.height = Math.round(h * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    fit();
    render();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(canvas.parentElement!);
  sync();
  setMode("edit");
  resize();
  return {
    getCircuit: () => cloneCircuit(circuit),
    setCircuit(next) {
      circuit = cloneCircuit(next);
      history = [snapshot(circuit)];
      cursor = 0;
      selected = 0;
      sync();
      fit();
      render();
    },
    dispose() {
      disposed = true;
      observer.disconnect();
      abort.abort();
      root.remove();
    },
  };
}

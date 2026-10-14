import type { WebGLRenderer } from "three";
export interface GpuInfo {
  name: string;
  kind: string;
  webgl2: boolean;
  webgpuAvailable: boolean;
  maxTextureSize: number;
  maxSamples: number;
  timerQueries: boolean;
}
export function inspectGpu(renderer: WebGLRenderer): GpuInfo {
  const gl = renderer.getContext() as WebGL2RenderingContext;
  const debug = gl.getExtension("WEBGL_debug_renderer_info");
  const raw = String(
    gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : gl.RENDERER) ||
      "Não disponibilizada pelo navegador",
  );
  const kind = /swiftshader|llvmpipe|software|softpipe/i.test(raw)
    ? "Software"
    : /nvidia|geforce|radeon rx|quadro|arc a\d/i.test(raw)
      ? "Dedicada (classificação aproximada)"
      : /intel|iris|uhd|vega|radeon graphics/i.test(raw)
        ? "Integrada (classificação aproximada)"
        : "Não classificada";
  return {
    name: raw,
    kind,
    webgl2: renderer.capabilities.isWebGL2,
    webgpuAvailable: "gpu" in navigator,
    maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
    maxSamples: gl.getParameter(gl.MAX_SAMPLES),
    timerQueries: !!gl.getExtension("EXT_disjoint_timer_query_webgl2"),
  };
}

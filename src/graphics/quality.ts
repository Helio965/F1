import type { Quality } from "../core/types";
/** Hysteresis: sustained slow windows, then 12 seconds to settle. Only outside benchmark. */
export class AdaptiveQuality {
  private time = 0;
  private count = 0;
  private cooldown = 6;
  reset() {
    this.time = 0;
    this.count = 0;
    this.cooldown = 6;
  }
  tick(dt: number, quality: Quality): Quality | null {
    this.cooldown -= dt;
    if (this.cooldown > 0) return null;
    this.time += dt;
    this.count++;
    if (this.time < 5) return null;
    const fps = this.count / this.time;
    this.time = 0;
    this.count = 0;
    if (fps < 44 && quality !== "low") {
      this.cooldown = 12;
      return quality === "high" ? "medium" : "low";
    }
    return null;
  }
}

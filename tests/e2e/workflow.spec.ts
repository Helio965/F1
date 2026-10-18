import { test, expect } from "@playwright/test";
test("opens real WebGL scene, persists setup, homologates and drives", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => {
    errors.push(e.message);
    if (errors.length <= 3) console.error("Browser error:", e.message);
  });
  await page.goto("/?quality=low");
  await expect(page.locator("#world canvas")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Bem-vindo ao seu laboratório." }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Configurações", exact: true })
    .click();
  await page.locator('[data-quality="low"]').click();
  await page.reload();
  await page
    .getByRole("button", { name: "Configurações", exact: true })
    .click();
  await expect(page.locator('[data-quality="low"]')).toHaveClass("selected");
  await page.getByRole("button", { name: "Visão geral", exact: true }).click();
  await page
    .getByRole("button", { name: "Iniciar corrida", exact: true })
    .click();
  await page.locator('input[name="homologate"]').check();
  await page.locator('select[name="cars"]').selectOption("2");
  await page.getByRole("button", { name: "Ir para o grid" }).click();
  await expect(page.locator("#speed")).toHaveText("0");
  await expect(page.locator("#countdown")).toHaveText("", { timeout: 30000 });
  await page.keyboard.down("w");
  await expect
    .poll(async () => Number(await page.locator("#speed").textContent()))
    .toBeGreaterThan(15);
  await page.keyboard.up("w");
  await page.keyboard.press("c");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "Nos boxes." })).toBeVisible();
  await page.getByRole("button", { name: "Sair para o menu" }).click();
  await page
    .getByRole("button", { name: "Criar circuito", exact: true })
    .click();
  await expect(page.locator("#editor-mount canvas")).toBeVisible();
  await page
    .getByRole("button", { name: "Validar circuito", exact: true })
    .click();
  await expect(page.locator("#validation")).toContainText("Geometria validada");
  await page
    .getByRole("button", { name: "Salvar versão local", exact: true })
    .click();
  await expect(page.locator("#toast")).toContainText("salva");
  await page
    .getByRole("button", { name: "Meus circuitos", exact: true })
    .click();
  await expect(page.locator(".circuit-card")).toHaveCount(2);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Visão geral", exact: true }).click();
  await page.screenshot({ path: "test-results/mobile.png", fullPage: true });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  expect(errors).toEqual([]);
});
test("runs a complete 30 second benchmark and saves measured report", async ({
  page,
}) => {
  await page.goto("/?quality=low");
  await expect(page.locator("#world canvas")).toBeVisible();
  await page.getByRole("button", { name: "Benchmark", exact: true }).click();
  await page.locator("#bench-quality").selectOption("low");
  await page.locator("#start-benchmark").click();
  await expect(page.locator("#benchmark-status")).toContainText(
    /MEDINDO|AQUECIMENTO/,
  );
  await expect(page.locator("#benchmark-result .report")).toBeVisible({
    timeout: 70000,
  });
  await expect(page.locator("#history tbody tr")).toHaveCount(1);
  const value = Number(
    await page.locator(".report-stats strong").first().textContent(),
  );
  expect(value).toBeGreaterThan(0);
  await page.reload();
  await page.getByRole("button", { name: "Benchmark", exact: true }).click();
  await expect(page.locator("#history tbody tr")).toHaveCount(1);
});

test("draws a custom closed circuit, homologates and reloads its saved version", async ({
  page,
}) => {
  await page.goto("/?quality=low");
  await expect(page.locator("#world canvas")).toBeVisible();
  await page.locator('[data-nav="editor"]').click();
  await page.locator('[data-mode="draw"]').click();
  const bounds = await page.locator("#editor-mount canvas").boundingBox();
  expect(bounds).not.toBeNull();
  const { x, y, width, height } = bounds!;
  const cx = x + width / 2,
    cy = y + height / 2,
    rx = width * 0.32,
    ry = height * 0.32;
  await page.mouse.move(cx + rx, cy);
  await page.mouse.down();
  for (let i = 1; i <= 40; i++) {
    const angle = (i / 40) * Math.PI * 2;
    await page.mouse.move(cx + Math.cos(angle) * rx, cy + Math.sin(angle) * ry);
  }
  await page.mouse.up();
  await page
    .getByRole("textbox", { name: "Nome do circuito" })
    .fill("Circuito de integração");
  await page.keyboard.press("Tab");
  await page.locator("#validate").click();
  await expect(page.locator("#validation")).toContainText("Geometria validada");
  await page.locator("#homologate").click();
  await expect(page.locator("#validation")).toContainText(
    "Circuito homologado",
  );
  await page.locator("#save-track").click();
  await expect(page.locator("#toast")).toContainText("salva");
  await page.reload();
  await page.locator('[data-nav="circuits"]').click();
  await expect(
    page.locator(".circuit-card").filter({ hasText: "Circuito de integração" }),
  ).toContainText("Homologado");
});

test("measures the Python physics engine inside the browser worker", async ({ page }) => {
  test.setTimeout(300000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/?quality=low");
  await page.getByRole("button", { name: "Benchmark", exact: true }).click();
  await expect(page.locator("#physics-status")).toContainText(/pronto/i, { timeout: 60000 });
  await page.locator("#physics-bench-btn").click();
  const rows = page.locator("#physics-bench-result tbody tr");
  await expect(rows).toHaveCount(4, { timeout: 240000 });
  // Every scenario reports a finite, positive mean step time and real-time load.
  for (const text of await rows.allTextContents()) expect(text).toMatch(/\d+\.\d{3} ms/);
  await expect(page.locator("#physics-bench-result")).toContainText("Aurora Park");
  await expect(page.locator("#physics-bench-result")).toContainText("Latência ida e volta");
  expect(errors).toEqual([]);
});

import { test, expect } from "@playwright/test";
test("opens real WebGL scene, persists setup, homologates and drives", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
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
  await page.waitForTimeout(3500);
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

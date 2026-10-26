import { expect, test } from "@playwright/test";

import { RUN, adminApi, asOwner, signIn } from "../helpers";

test("models show live health and the measured quality matrix", async ({ page }) => {
  await asOwner(page, "/models");
  await expect(page.getByRole("cell", { name: /claude-pro/ }).first()).toBeVisible();
  await expect(page.getByText("Healthy").first()).toBeVisible();
  await page.getByRole("tab", { name: "Quality" }).click();
  await expect(page.getByText("Quality by task and difficulty")).toBeVisible();
  await expect(page.getByRole("table", { name: "Quality scores by model, task and difficulty" }).getByText("Coding", { exact: true })).toBeVisible();
});

test("an endpoint connection test reaches the upstream", async ({ page }) => {
  await asOwner(page, "/endpoints");
  await page.getByRole("button", { name: /test connection/i }).first().click();
  await expect(page.getByText("Connected").first()).toBeVisible();
});

test("benchmark results show savings and every answer downloads", async ({ page }) => {
  await asOwner(page, "/benchmarks");
  await expect(page.getByText("Saved within 3 points")).toBeVisible();
  await expect(page.getByText("Quality and cost, side by side")).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: /download every answer/i }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^benchmark-.*\.jsonl\.gz$/);
});

test("a viewer can read everything and change nothing", async ({ browser, request }) => {
  const headers = await adminApi(request);
  const email = `viewer-${RUN}@example.test`;
  const created = await request.post("/api/v1/users", {
    data: { email, name: "E2E Viewer", role: "viewer", password: "Viewer-Pass-123" },
    headers,
  });
  expect(created.status()).toBe(201);
  const viewer = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const page = await viewer.newPage();
  await signIn(page, email, "Viewer-Pass-123");
  await page.goto("/policies");
  await expect(page.getByRole("heading", { name: /Policies/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /new policy/i })).toHaveCount(0);
  await page.goto("/keys");
  await expect(page.getByRole("button", { name: /new key/i })).toHaveCount(0);
  await page.goto("/benchmarks");
  await expect(page.getByRole("button", { name: /run benchmark/i })).toHaveCount(0);
  await viewer.close();
});

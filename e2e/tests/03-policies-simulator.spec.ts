import { expect, test } from "@playwright/test";

import { RUN, asOwner } from "../helpers";

test("create a policy in the dashboard and call it as auto:<name>", async ({ page, request }) => {
  const name = `e2e-cheap-${RUN}`;
  await asOwner(page, "/policies");
  await page.getByRole("button", { name: /new policy/i }).click();
  const dialog = page.getByRole("dialog");
  await dialog.locator("#p-name").fill(name);
  await dialog.getByRole("radio", { name: "Cost first" }).click();
  await dialog.locator("#p-floor").fill("0.75");
  await dialog.getByRole("button", { name: /create policy/i }).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();

  // The demo key the seed created for Alpha Tutor is not exposed; use a fresh one.
  const projects = await (await request.get("/api/v1/projects")).json();
  const created = await request.post("/api/v1/keys", {
    data: { project_id: projects[0].id, name: `e2e-${RUN}` },
    headers: { "x-smartrouter-csrf": "1" },
  });
  const key = (await created.json()).key;
  const resp = await request.post("/v1/chat/completions", {
    data: { model: `auto:${name}`, messages: [{ role: "user", content: "Hello there" }] },
    headers: { authorization: `Bearer ${key}` },
  });
  expect(resp.status()).toBe(200);
  expect(resp.headers()["x-smartrouter-policy"]).toBe(name);
});

test("the simulator ranks models without calling any", async ({ page }) => {
  await asOwner(page, "/simulator");
  await page.getByRole("button", { name: "Classify a review" }).click();
  await page.getByRole("button", { name: /simulate routing/i }).click();
  await expect(page.getByText(/would choose/)).toBeVisible();
  await expect(page.getByText("What the analyzer saw")).toBeVisible();
  await expect(page.getByText("Classification", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/Ranking: \d eligible of 5/)).toBeVisible();
});

import { expect, test } from "@playwright/test";

import { RUN, asOwner, choose } from "../helpers";

/**
 * The core journey: create a project, give it a routing policy and a key in the dashboard,
 * call the gateway with model "auto" exactly as an application would, then open the decision
 * and see why that model was chosen and what it saved.
 */
test.describe.serial("route a new project", () => {
  const project = `E2E Shop ${RUN}`;
  let key = "";
  let rid = "";

  test("create a project with a default policy and a key", async ({ page }) => {
    await asOwner(page);
    await page.goto("/projects");
    await page.getByRole("button", { name: /new project/i }).click();
    await page.locator("#project-name").fill(project);
    await page.locator("#project-desc").fill("Checkout assistant");
    await page.getByRole("button", { name: /create project/i }).click();
    await expect(page.getByRole("heading", { name: project })).toBeVisible();

    await choose(page, "project-policy", "cost");
    await page.getByRole("button", { name: /save routing/i }).click();
    await expect(page.getByText("Routing settings saved.")).toBeVisible();

    await page.getByRole("button", { name: /new key/i }).first().click();
    await page.locator("#key-name").fill("e2e");
    await page.getByRole("button", { name: /create key/i }).click();
    key = (await page.getByTestId("new-key").textContent())?.trim() ?? "";
    expect(key).toMatch(/^tsr_[0-9a-f]{12}_/);
    await page.keyboard.press("Escape");
  });

  test("model auto is routed by the project's policy and explained", async ({ page, request }) => {
    const resp = await request.post("/v1/chat/completions", {
      data: { model: "auto", messages: [{ role: "user", content: `Classify the sentiment as positive or negative: great service ${RUN}` }] },
      headers: { authorization: `Bearer ${key}` },
    });
    expect(resp.status()).toBe(200);
    const h = resp.headers();
    expect(h["x-smartrouter-policy"]).toBe("cost");
    expect(h["x-smartrouter-task"]).toBe("classification");
    expect((await resp.json()).model).toBe(h["x-smartrouter-model"]);
    rid = h["x-smartrouter-request-id"];

    await asOwner(page);
    // Filters live in the URL, so a filtered view is a shareable link.
    await page.goto(`/decisions?q=${rid}`);
    await page.getByRole("row", { name: `Decision ${rid}` }).click();
    const drawer = page.getByRole("dialog");
    await expect(drawer.getByText(rid)).toBeVisible();
    await expect(drawer.getByText("Why this model")).toBeVisible();
    await expect(drawer.getByText(h["x-smartrouter-model"]).first()).toBeVisible();
    await expect(drawer.getByText(/eligible of 5/)).toBeVisible();
    await expect(drawer.getByText("classification: classify verb")).toBeVisible();
  });

  test("a pinned model is passed through, never switched", async ({ request }) => {
    const resp = await request.post("/v1/chat/completions", {
      data: { model: "gpt-pro", messages: [{ role: "user", content: `Hello ${RUN}` }] },
      headers: { authorization: `Bearer ${key}` },
    });
    expect(resp.status()).toBe(200);
    expect(resp.headers()["x-smartrouter-model"]).toBe("gpt-pro");
    expect(resp.headers()["x-smartrouter-policy"]).toBe("passthrough");
  });
});

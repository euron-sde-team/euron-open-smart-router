import { describe, expect, it } from "vitest";

import { attemptChip, circuitChip, errorLabel, qualityTone, severityTone, statusChip } from "@/lib/chips";
import { formatQuality, modelColors, nusdToUsd, perMillion, taskLabel } from "@/lib/models";
import { bucketUnit, isRangeKey, previousPeriodLabel, rangeLabel, rangeMs, rangeStart } from "@/lib/range";
import { canManageUsers, canWrite, isRole, roleLabel } from "@/lib/roles";

describe("chips", () => {
  it("maps decision status, circuit state and attempt outcomes", () => {
    expect(statusChip("SUCCESS").tone).toBe("success");
    expect(statusChip("rejected").label).toBe("Rejected");
    expect(statusChip("SOMETHING_NEW").label).toBe("Something new");
    expect(circuitChip("open").tone).toBe("danger");
    expect(circuitChip("half_open").label).toBe("Probing");
    expect(circuitChip(undefined).label).toBe("Healthy");
    expect(attemptChip("budget").hint).toMatch(/cheaper/);
    expect(attemptChip("ok").tone).toBe("success");
  });

  it("labels every error code the gateway records", () => {
    for (const code of ["all_models_failed", "no_eligible_model", "model_not_found", "policy_not_found", "budget_exceeded", "rpm_limit"]) {
      expect(errorLabel(code)).not.toContain("_");
    }
    expect(errorLabel("brand_new_code")).toBe("Brand new code");
    expect(errorLabel(null)).toBe("");
  });

  it("grades quality against a floor and alert severity", () => {
    expect(qualityTone(0.92)).toBe("success");
    expect(qualityTone(0.72)).toBe("info");
    expect(qualityTone(0.6)).toBe("warning");
    expect(qualityTone(0.3)).toBe("danger");
    expect(qualityTone(0.85, 0.85)).toBe("info");
    expect(severityTone("critical")).toBe("danger");
    expect(severityTone("whatever")).toBe("neutral");
  });
});

describe("models", () => {
  it("gives each model a stable color that follows the model, not its rank", () => {
    const all = modelColors(["gpt-pro", "claude-pro", "llama-local"]);
    const filtered = modelColors(["llama-local", "claude-pro", "gpt-pro", "claude-pro"]);
    expect(all("claude-pro")).toBe("var(--series-1)");
    expect(all("llama-local")).toBe(filtered("llama-local"));
    expect(all("unknown")).toBe("var(--chart-baseline)");
    const many = modelColors(["a", "b", "c", "d", "e", "f", "g"]);
    expect(many("g")).toBe("var(--chart-baseline)");
  });

  it("formats prices, money and scores", () => {
    expect(perMillion(0)).toBe("Free");
    expect(perMillion("0.05")).toBe("$0.05");
    expect(perMillion(0.1)).toBe("$0.10");
    expect(perMillion(0.075)).toBe("$0.075");
    expect(perMillion(3)).toBe("$3");
    expect(perMillion(2.5)).toBe("$2.50");
    expect(perMillion(null)).toBe("n/a");
    expect(nusdToUsd(1_500_000_000)).toBe(1.5);
    expect(formatQuality(0.876)).toBe("0.88");
    expect(taskLabel("coding")).toBe("Coding");
  });
});

describe("ranges", () => {
  it("knows its windows and labels", () => {
    expect(isRangeKey("7d")).toBe(true);
    expect(isRangeKey("1y")).toBe(false);
    expect(rangeMs("24h")).toBe(86_400_000);
    expect(rangeLabel("30d")).toBe("Last 30 days");
    expect(previousPeriodLabel("7d")).toBe("vs prior 7d");
    expect(bucketUnit("24h")).toBe("hour");
    expect(bucketUnit("7d")).toBe("day");
    expect(rangeStart("1h", new Date("2026-10-23T10:00:00Z")).toISOString()).toBe("2026-10-23T09:00:00.000Z");
  });
});

describe("roles", () => {
  it("gates writes and user management", () => {
    expect(canWrite("admin")).toBe(true);
    expect(canWrite("viewer")).toBe(false);
    expect(canManageUsers("admin")).toBe(false);
    expect(canManageUsers("owner")).toBe(true);
    expect(isRole("root")).toBe(false);
    expect(roleLabel("viewer")).toBe("Viewer");
    expect(roleLabel(undefined)).toBe("Unknown");
  });
});

/**
 * Named aliases over the generated OpenAPI types (`pnpm gen:api`). Nothing here is
 * hand-written: every shape comes from `schema.d.ts`, which is generated from
 * docs/api/openapi.yaml.
 */
import type { components, operations } from "./schema";

type S = components["schemas"];

export type User = S["UserOut"];
export type UserCreate = S["UserCreate"];
export type UserUpdate = S["UserUpdate"];
export type Session = S["SessionOut"];
export type Team = S["TeamOut"];
export type TeamIn = S["TeamIn"];
export type TeamUpdate = S["TeamUpdate"];
export type Project = S["ProjectOut"];
export type ProjectIn = S["ProjectIn"];
export type ProjectUpdate = S["ProjectUpdate"];
export type ApiKey = S["KeyOut"];
export type ApiKeyCreated = S["KeyCreated"];
export type ApiKeyIn = S["KeyIn"];
export type Provider = S["ProviderOut"];
export type ProviderIn = S["ProviderIn"];
export type ProviderUpdate = S["ProviderUpdate"];
export type ProviderTest = S["ProviderTest"];
export type Model = S["ModelOut"];
export type ModelIn = S["ModelIn"];
export type ModelUpdate = S["ModelUpdate"];
export type ModelHealth = S["ModelHealth"];
export type Policy = S["PolicyOut"];
export type PolicyIn = S["PolicyIn"];
export type PolicyUpdate = S["PolicyUpdate"];
export type QualityCell = S["QualityCell"];
export type QualityOverride = S["QualityOverride"];
export type SimulateIn = S["SimulateIn"];
export type SimulateOut = S["SimulateOut"];
export type Analysis = S["AnalysisOut"];
export type Decision = S["DecisionOut"];
export type DecisionDetail = S["DecisionDetail"];
export type DecisionPage = S["Page_DecisionOut_"];
export type Overview = S["Overview"];
export type Totals = S["Totals"];
export type SeriesPoint = S["SeriesPoint"];
export type BreakdownRow = S["BreakdownRow"];
export type BenchmarkRun = S["RunOut"];
export type BenchmarkRunDetail = S["RunDetail"];
export type BenchmarkResult = S["ResultOut"];
export type BenchmarkRunIn = S["RunIn"];
export type SuiteCase = S["SuiteCase"];
export type Alert = S["AlertOut"];
export type AlertList = S["AlertList"];
export type AuditEntry = S["AuditOut"];
export type AuditPage = S["Page_AuditOut_"];
export type SystemInfo = S["SystemInfo"];

export type RangeKey = NonNullable<
  NonNullable<operations["overview_api_v1_analytics_overview_get"]["parameters"]["query"]>["range"]
>;
export type Strategy = NonNullable<PolicyIn["strategy"]>;
export type Tier = NonNullable<ModelIn["tier"]>;
export type EndpointKind = ProviderIn["kind"];
export type Role = NonNullable<UserCreate["role"]>;
export type BreakdownBy = NonNullable<
  NonNullable<operations["breakdown_api_v1_analytics_breakdown_get"]["parameters"]["query"]>["by"]
>;
export type DecisionFilters = NonNullable<
  operations["list_decisions_api_v1_decisions_get"]["parameters"]["query"]
>;

/** One candidate as the engine explains it (candidates are free-form JSON in the schema). */
export interface Candidate {
  model: string;
  eligible: boolean;
  reason: string;
  score: number;
  estimated_cost_nusd: number;
  latency_ms: number;
  quality: number;
  quality_source: string;
  components: Partial<Record<"cost" | "latency" | "quality", number>>;
}

/** One entry of a decision's fallback chain. */
export interface Attempt {
  model: string;
  outcome: string;
  ms: number;
  status?: number;
  detail?: string;
}

/** A benchmark summary as the runner writes it. */
export interface BenchmarkSummary {
  baseline: string | null;
  cases: number;
  models: Record<string, { quality: number; cost_usd: number; latency_ms: number }>;
  policies: Record<
    string,
    {
      quality: number;
      cost_usd: number;
      latency_ms: number;
      model_mix: Record<string, number>;
      savings_pct?: number;
      quality_delta?: number;
    }
  >;
  calibration?: Record<string, [number, number]>;
}

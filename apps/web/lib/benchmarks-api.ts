export class BenchmarksApiError extends Error {}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new BenchmarksApiError(`benchmarks request failed: ${response.status} ${url}`);
  }
  return (await response.json()) as T;
}

export interface EvalRunSummary {
  id: string;
  dataset: string;
  backend: string;
  modelVersion: string | null;
  sampleCount: number;
  startedAt: string;
  finishedAt: string | null;
}

interface WireEvalRunSummary {
  id: string;
  dataset: string;
  backend: string;
  model_version: string | null;
  sample_count: number;
  started_at: string;
  finished_at: string | null;
}

export interface EvalFieldMetrics {
  fieldPath: string;
  n: number;
  precision: string | null;
  recall: string | null;
  f1: string | null;
  exactMatchRate: string | null;
  meanConfidence: string | null;
  meanAbsoluteError: string | null;
  withinToleranceRate: string | null;
}

interface WireEvalFieldMetrics {
  field_path: string;
  n: number;
  precision: string | null;
  recall: string | null;
  f1: string | null;
  exact_match_rate: string | null;
  mean_confidence: string | null;
  mean_absolute_error: string | null;
  within_tolerance_rate: string | null;
}

export interface EvalCalibrationBucket {
  bucketLow: number;
  bucketHigh: number;
  n: number;
  meanConfidence: string | null;
  actualAccuracy: string | null;
}

interface WireEvalCalibrationBucket {
  bucket_low: number;
  bucket_high: number;
  n: number;
  mean_confidence: string | null;
  actual_accuracy: string | null;
}

export interface EvalRunDetail {
  id: string;
  dataset: string;
  backend: string;
  modelVersion: string | null;
  sampleCount: number;
  startedAt: string;
  finishedAt: string | null;
  overallExactMatchRate: number | null;
  meanLatencyMs: string | null;
  latencyP50Ms: string | null;
  latencyP95Ms: string | null;
  latencyP99Ms: string | null;
  totalEstimatedCostUsd: string | null;
  costPer1000Usd: number | null;
  lineItemPrecision: string | null;
  lineItemRecall: string | null;
  lineItemF1: string | null;
  fields: EvalFieldMetrics[];
  calibration: EvalCalibrationBucket[];
}

interface WireEvalRunDetail {
  id: string;
  dataset: string;
  backend: string;
  model_version: string | null;
  sample_count: number;
  started_at: string;
  finished_at: string | null;
  overall_exact_match_rate: number | null;
  mean_latency_ms: string | null;
  latency_p50_ms: string | null;
  latency_p95_ms: string | null;
  latency_p99_ms: string | null;
  total_estimated_cost_usd: string | null;
  cost_per_1000_usd: number | null;
  line_item_precision: string | null;
  line_item_recall: string | null;
  line_item_f1: string | null;
  fields: WireEvalFieldMetrics[];
  calibration: WireEvalCalibrationBucket[];
}

export interface EvalFailureDocument {
  docId: string;
  groundTruth: Record<string, unknown>;
  extractionResult: Record<string, unknown>;
  mismatchCount: number;
  thumbnailUrl: string | null;
  mimeType: string | null;
}

interface WireEvalFailureDocument {
  doc_id: string;
  ground_truth: Record<string, unknown>;
  extraction_result: Record<string, unknown>;
  mismatch_count: number;
  thumbnail_url: string | null;
  mime_type: string | null;
}

function toRunSummary(w: WireEvalRunSummary): EvalRunSummary {
  return {
    id: w.id,
    dataset: w.dataset,
    backend: w.backend,
    modelVersion: w.model_version,
    sampleCount: w.sample_count,
    startedAt: w.started_at,
    finishedAt: w.finished_at,
  };
}

function toFieldMetrics(w: WireEvalFieldMetrics): EvalFieldMetrics {
  return {
    fieldPath: w.field_path,
    n: w.n,
    precision: w.precision,
    recall: w.recall,
    f1: w.f1,
    exactMatchRate: w.exact_match_rate,
    meanConfidence: w.mean_confidence,
    meanAbsoluteError: w.mean_absolute_error,
    withinToleranceRate: w.within_tolerance_rate,
  };
}

function toCalibrationBucket(w: WireEvalCalibrationBucket): EvalCalibrationBucket {
  return {
    bucketLow: w.bucket_low,
    bucketHigh: w.bucket_high,
    n: w.n,
    meanConfidence: w.mean_confidence,
    actualAccuracy: w.actual_accuracy,
  };
}

export async function fetchRuns(apiBaseUrl: string): Promise<EvalRunSummary[]> {
  const rows = await getJson<WireEvalRunSummary[]>(`${apiBaseUrl}/v1/benchmarks/runs`);
  return rows.map(toRunSummary);
}

export async function fetchRunDetail(apiBaseUrl: string, runId: string): Promise<EvalRunDetail> {
  const w = await getJson<WireEvalRunDetail>(`${apiBaseUrl}/v1/benchmarks/runs/${runId}`);
  return {
    id: w.id,
    dataset: w.dataset,
    backend: w.backend,
    modelVersion: w.model_version,
    sampleCount: w.sample_count,
    startedAt: w.started_at,
    finishedAt: w.finished_at,
    overallExactMatchRate: w.overall_exact_match_rate,
    meanLatencyMs: w.mean_latency_ms,
    latencyP50Ms: w.latency_p50_ms,
    latencyP95Ms: w.latency_p95_ms,
    latencyP99Ms: w.latency_p99_ms,
    totalEstimatedCostUsd: w.total_estimated_cost_usd,
    costPer1000Usd: w.cost_per_1000_usd,
    lineItemPrecision: w.line_item_precision,
    lineItemRecall: w.line_item_recall,
    lineItemF1: w.line_item_f1,
    fields: w.fields.map(toFieldMetrics),
    calibration: w.calibration.map(toCalibrationBucket),
  };
}

export async function fetchFailures(
  apiBaseUrl: string,
  runId: string,
  limit = 12,
): Promise<EvalFailureDocument[]> {
  const rows = await getJson<WireEvalFailureDocument[]>(
    `${apiBaseUrl}/v1/benchmarks/runs/${runId}/failures?limit=${limit}`,
  );
  return rows.map((w) => ({
    docId: w.doc_id,
    groundTruth: w.ground_truth,
    extractionResult: w.extraction_result,
    mismatchCount: w.mismatch_count,
    thumbnailUrl: w.thumbnail_url,
    mimeType: w.mime_type,
  }));
}

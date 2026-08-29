export class AnalyticsApiError extends Error {}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new AnalyticsApiError(`analytics request failed: ${response.status} ${url}`);
  }
  return (await response.json()) as T;
}

export interface AnalyticsSummary {
  periodDays: number;
  invoicesProcessed: number;
  invoicesProcessedDelta: number;
  autoPostRatePct: string | null;
  meanExtractionConfidence: string | null;
  exceptionsBySeverity: Record<string, number>;
  valueAtRisk: string;
  meanSecondsToDecision: number | null;
}

interface WireSummary {
  period_days: number;
  invoices_processed: number;
  invoices_processed_delta: number;
  auto_post_rate_pct: string | null;
  mean_extraction_confidence: string | null;
  exceptions_by_severity: Record<string, number>;
  value_at_risk: string;
  mean_seconds_to_decision: number | null;
}

export interface VolumePoint {
  day: string;
  outcome: "auto_posted" | "approved" | "rejected" | "failed" | "pending";
  count: number;
}

export interface ExceptionTypeCount {
  exceptionType: string;
  count: number;
}

interface WireExceptionTypeCount {
  exception_type: string;
  count: number;
}

export interface ConfidenceBucket {
  bucketStart: number;
  bucketEnd: number;
  count: number;
}

interface WireConfidenceBucket {
  bucket_start: number;
  bucket_end: number;
  count: number;
}

export interface LatencyPercentiles {
  p50: number | null;
  p95: number | null;
  p99: number | null;
}

export interface LatencyResponse {
  extraction: LatencyPercentiles;
  matching: LatencyPercentiles;
  notification: LatencyPercentiles;
}

export interface AutoPostTrendPoint {
  day: string;
  autoPosted: number;
  settled: number;
  ratePct: string | null;
}

interface WireAutoPostTrendPoint {
  day: string;
  auto_posted: number;
  settled: number;
  rate_pct: string | null;
}

export interface VendorAnalyticsRow {
  vendorId: string;
  vendorName: string;
  invoiceCount: number;
  exceptionRatePct: string;
  meanPriceVariancePct: string | null;
}

interface WireVendorAnalyticsRow {
  vendor_id: string;
  vendor_name: string;
  invoice_count: number;
  exception_rate_pct: string;
  mean_price_variance_pct: string | null;
}

export interface DeliveryHealth {
  totalDeliveries: number;
  sentDeliveries: number;
  successRatePct: string | null;
  meanAttempts: string;
  maxAttempts: number;
  deadLetterCount: number;
}

interface WireDeliveryHealth {
  total_deliveries: number;
  sent_deliveries: number;
  success_rate_pct: string | null;
  mean_attempts: string;
  max_attempts: number;
  dead_letter_count: number;
}

function qs(tenantId: string, days?: number): string {
  const params = new URLSearchParams({ tenant_id: tenantId });
  if (days !== undefined) params.set("days", String(days));
  return params.toString();
}

export async function fetchSummary(
  apiBaseUrl: string,
  tenantId: string,
  days: number,
): Promise<AnalyticsSummary> {
  const w = await getJson<WireSummary>(`${apiBaseUrl}/v1/analytics/summary?${qs(tenantId, days)}`);
  return {
    periodDays: w.period_days,
    invoicesProcessed: w.invoices_processed,
    invoicesProcessedDelta: w.invoices_processed_delta,
    autoPostRatePct: w.auto_post_rate_pct,
    meanExtractionConfidence: w.mean_extraction_confidence,
    exceptionsBySeverity: w.exceptions_by_severity,
    valueAtRisk: w.value_at_risk,
    meanSecondsToDecision: w.mean_seconds_to_decision,
  };
}

export async function fetchVolumeOverTime(
  apiBaseUrl: string,
  tenantId: string,
  days: number,
): Promise<VolumePoint[]> {
  return getJson<VolumePoint[]>(`${apiBaseUrl}/v1/analytics/volume-over-time?${qs(tenantId, days)}`);
}

export async function fetchExceptionsByType(
  apiBaseUrl: string,
  tenantId: string,
  days: number,
): Promise<ExceptionTypeCount[]> {
  const rows = await getJson<WireExceptionTypeCount[]>(
    `${apiBaseUrl}/v1/analytics/exceptions-by-type?${qs(tenantId, days)}`,
  );
  return rows.map((r) => ({ exceptionType: r.exception_type, count: r.count }));
}

export async function fetchConfidenceDistribution(
  apiBaseUrl: string,
  tenantId: string,
  days: number,
): Promise<ConfidenceBucket[]> {
  const rows = await getJson<WireConfidenceBucket[]>(
    `${apiBaseUrl}/v1/analytics/confidence-distribution?${qs(tenantId, days)}`,
  );
  return rows.map((r) => ({ bucketStart: r.bucket_start, bucketEnd: r.bucket_end, count: r.count }));
}

export async function fetchLatency(
  apiBaseUrl: string,
  tenantId: string,
  days: number,
): Promise<LatencyResponse> {
  return getJson<LatencyResponse>(`${apiBaseUrl}/v1/analytics/latency?${qs(tenantId, days)}`);
}

export async function fetchAutoPostTrend(
  apiBaseUrl: string,
  tenantId: string,
  days: number,
): Promise<AutoPostTrendPoint[]> {
  const rows = await getJson<WireAutoPostTrendPoint[]>(
    `${apiBaseUrl}/v1/analytics/auto-post-trend?${qs(tenantId, days)}`,
  );
  return rows.map((r) => ({
    day: r.day,
    autoPosted: r.auto_posted,
    settled: r.settled,
    ratePct: r.rate_pct,
  }));
}

export async function fetchVendors(apiBaseUrl: string, tenantId: string): Promise<VendorAnalyticsRow[]> {
  const rows = await getJson<WireVendorAnalyticsRow[]>(
    `${apiBaseUrl}/v1/analytics/vendors?${qs(tenantId)}`,
  );
  return rows.map((r) => ({
    vendorId: r.vendor_id,
    vendorName: r.vendor_name,
    invoiceCount: r.invoice_count,
    exceptionRatePct: r.exception_rate_pct,
    meanPriceVariancePct: r.mean_price_variance_pct,
  }));
}

export async function fetchDeliveryHealth(apiBaseUrl: string, tenantId: string): Promise<DeliveryHealth> {
  const w = await getJson<WireDeliveryHealth>(
    `${apiBaseUrl}/v1/analytics/delivery-health?${qs(tenantId)}`,
  );
  return {
    totalDeliveries: w.total_deliveries,
    sentDeliveries: w.sent_deliveries,
    successRatePct: w.success_rate_pct,
    meanAttempts: w.mean_attempts,
    maxAttempts: w.max_attempts,
    deadLetterCount: w.dead_letter_count,
  };
}

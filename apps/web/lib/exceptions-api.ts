export type ExceptionSeverity = "info" | "warn" | "block";

export interface ExceptionCard {
  id: string;
  exceptionType: string;
  severity: ExceptionSeverity;
  detail: string;
  status: string;
  createdAt: string;
  invoiceId: string;
  invoiceNumber: string | null;
  invoiceTotal: string | null;
  currency: string;
  vendorId: string | null;
  vendorName: string | null;
  expectedValue: string | null;
  actualValue: string | null;
  delta: string | null;
  deltaPct: string | null;
}

export interface VendorOption {
  id: string;
  name: string;
}

export interface ExceptionsList {
  exceptions: ExceptionCard[];
  autoPostedThisWeek: number;
  vendors: VendorOption[];
}

interface WireExceptionCard {
  id: string;
  exception_type: string;
  severity: ExceptionSeverity;
  detail: string;
  status: string;
  created_at: string;
  invoice_id: string;
  invoice_number: string | null;
  invoice_total: string | null;
  currency: string;
  vendor_id: string | null;
  vendor_name: string | null;
  expected_value: string | null;
  actual_value: string | null;
  delta: string | null;
  delta_pct: string | null;
}

interface WireExceptionsList {
  exceptions: WireExceptionCard[];
  auto_posted_this_week: number;
  vendors: VendorOption[];
}

function toCard(wire: WireExceptionCard): ExceptionCard {
  return {
    id: wire.id,
    exceptionType: wire.exception_type,
    severity: wire.severity,
    detail: wire.detail,
    status: wire.status,
    createdAt: wire.created_at,
    invoiceId: wire.invoice_id,
    invoiceNumber: wire.invoice_number,
    invoiceTotal: wire.invoice_total,
    currency: wire.currency,
    vendorId: wire.vendor_id,
    vendorName: wire.vendor_name,
    expectedValue: wire.expected_value,
    actualValue: wire.actual_value,
    delta: wire.delta,
    deltaPct: wire.delta_pct,
  };
}

export class ExceptionsApiError extends Error {}

export interface ExceptionsFilters {
  status?: string;
  severity?: ExceptionSeverity;
  exceptionType?: string;
  vendorId?: string;
  dateFrom?: string;
  dateTo?: string;
  sort?: "severity" | "age" | "amount";
  order?: "asc" | "desc";
}

export async function fetchExceptions(
  apiBaseUrl: string,
  tenantId: string,
  filters: ExceptionsFilters,
): Promise<ExceptionsList> {
  const params = new URLSearchParams({ tenant_id: tenantId });
  if (filters.status) params.set("status", filters.status);
  if (filters.severity) params.set("severity", filters.severity);
  if (filters.exceptionType) params.set("exception_type", filters.exceptionType);
  if (filters.vendorId) params.set("vendor_id", filters.vendorId);
  if (filters.dateFrom) params.set("date_from", filters.dateFrom);
  if (filters.dateTo) params.set("date_to", filters.dateTo);
  if (filters.sort) params.set("sort", filters.sort);
  if (filters.order) params.set("order", filters.order);

  const response = await fetch(`${apiBaseUrl}/v1/exceptions?${params.toString()}`);
  if (!response.ok) {
    throw new ExceptionsApiError(`failed to load exceptions: ${response.status}`);
  }
  const data = (await response.json()) as WireExceptionsList;
  return {
    exceptions: data.exceptions.map(toCard),
    autoPostedThisWeek: data.auto_posted_this_week,
    vendors: data.vendors,
  };
}

export async function resolveException(
  apiBaseUrl: string,
  tenantId: string,
  exceptionId: string,
  actorUserId: string,
  note?: string,
): Promise<void> {
  const response = await fetch(
    `${apiBaseUrl}/v1/exceptions/${exceptionId}/resolve?tenant_id=${encodeURIComponent(tenantId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor_user_id: actorUserId, note }),
    },
  );
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new ExceptionsApiError(
      typeof detail?.detail === "string" ? detail.detail : `resolve failed: ${response.status}`,
    );
  }
}

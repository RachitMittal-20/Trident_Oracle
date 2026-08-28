import type { InvoiceStatus } from "@/components/status-pill";

export interface FieldConfidence {
  fieldPath: string;
  confidence: string;
  bbox: { page: number; x: number; y: number; w: number; h: number } | null;
  rawText: string | null;
  humanCorrected: boolean;
}

export interface InvoiceLine {
  id: string;
  lineNo: number;
  description: string;
  qty: string;
  unitPrice: string;
  lineTotal: string;
  matchedPoLineId: string | null;
  matchMethod: string | null;
}

export interface VerificationInvoice {
  id: string;
  tenantId: string;
  invoiceNumber: string | null;
  invoiceDate: string | null;
  dueDate: string | null;
  currency: string;
  subtotal: string | null;
  tax: string | null;
  total: string | null;
  status: InvoiceStatus;
  fileUrl: string;
  lines: InvoiceLine[];
  fieldConfidences: FieldConfidence[];
  policyMinFieldConfidence: string | null;
}

interface WireLine {
  id: string;
  line_no: number;
  description: string;
  qty: string;
  unit_price: string;
  line_total: string;
  matched_po_line_id: string | null;
  match_method: string | null;
}

interface WireFieldConfidence {
  field_path: string;
  confidence: string;
  bbox: { page: number; x: number; y: number; w: number; h: number } | null;
  raw_text: string | null;
  human_corrected: boolean;
}

interface WireInvoice {
  id: string;
  tenant_id: string;
  invoice_number: string | null;
  invoice_date: string | null;
  due_date: string | null;
  currency: string;
  subtotal: string | null;
  tax: string | null;
  total: string | null;
  status: InvoiceStatus;
  file_url: string;
  lines: WireLine[];
  field_confidences: WireFieldConfidence[];
  policy_min_field_confidence: string | null;
}

function toInvoice(wire: WireInvoice): VerificationInvoice {
  return {
    id: wire.id,
    tenantId: wire.tenant_id,
    invoiceNumber: wire.invoice_number,
    invoiceDate: wire.invoice_date,
    dueDate: wire.due_date,
    currency: wire.currency,
    subtotal: wire.subtotal,
    tax: wire.tax,
    total: wire.total,
    status: wire.status,
    fileUrl: wire.file_url,
    lines: wire.lines.map((line) => ({
      id: line.id,
      lineNo: line.line_no,
      description: line.description,
      qty: line.qty,
      unitPrice: line.unit_price,
      lineTotal: line.line_total,
      matchedPoLineId: line.matched_po_line_id,
      matchMethod: line.match_method,
    })),
    fieldConfidences: wire.field_confidences.map((fc) => ({
      fieldPath: fc.field_path,
      confidence: fc.confidence,
      bbox: fc.bbox,
      rawText: fc.raw_text,
      humanCorrected: fc.human_corrected,
    })),
    policyMinFieldConfidence: wire.policy_min_field_confidence,
  };
}

export class VerifyApiError extends Error {}

export async function fetchInvoiceForVerification(
  apiBaseUrl: string,
  invoiceId: string,
  tenantId: string,
): Promise<VerificationInvoice> {
  const response = await fetch(
    `${apiBaseUrl}/v1/invoices/${invoiceId}?tenant_id=${encodeURIComponent(tenantId)}`,
  );
  if (!response.ok) {
    throw new VerifyApiError(`failed to load invoice: ${response.status}`);
  }
  return toInvoice((await response.json()) as WireInvoice);
}

export async function correctInvoiceField(
  apiBaseUrl: string,
  invoiceId: string,
  tenantId: string,
  fieldPath: string,
  value: string,
): Promise<{ fieldPath: string; value: string }> {
  const response = await fetch(
    `${apiBaseUrl}/v1/invoices/${invoiceId}/fields?tenant_id=${encodeURIComponent(tenantId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ field_path: fieldPath, value }),
    },
  );
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new VerifyApiError(
      typeof detail?.detail === "string" ? detail.detail : `correction failed: ${response.status}`,
    );
  }
  const data = (await response.json()) as { field_path: string; value: string };
  return { fieldPath: data.field_path, value: data.value };
}

export async function rerunMatch(
  apiBaseUrl: string,
  invoiceId: string,
  tenantId: string,
): Promise<{ jobId: string }> {
  const response = await fetch(
    `${apiBaseUrl}/v1/invoices/${invoiceId}/rerun-match?tenant_id=${encodeURIComponent(tenantId)}`,
    { method: "POST" },
  );
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new VerifyApiError(
      typeof detail?.detail === "string" ? detail.detail : `rerun-match failed: ${response.status}`,
    );
  }
  const data = (await response.json()) as { job_id: string };
  return { jobId: data.job_id };
}

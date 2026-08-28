export interface MatchInvoice {
  id: string;
  invoiceNumber: string | null;
  total: string | null;
  currency: string;
  status: string;
}

export interface MatchPO {
  id: string;
  poNumber: string;
  total: string | null;
}

export interface MatchPOLine {
  id: string;
  lineNo: number;
  sku: string | null;
  description: string;
  qtyOrdered: string | null;
  qtyReceived: string | null;
  unitPrice: string | null;
  lineTotal: string | null;
}

export interface MatchInvoiceLine {
  id: string;
  lineNo: number;
  description: string;
  qty: string | null;
  unitPrice: string | null;
  lineTotal: string | null;
  matchedPoLineId: string | null;
  matchMethod: string | null;
  matchConfidence: string | null;
}

export type ExceptionSeverity = "info" | "warn" | "block";

export interface MatchException {
  id: string;
  exceptionType: string;
  severity: ExceptionSeverity;
  detail: string;
  poLineId: string | null;
  invoiceLineId: string | null;
  expectedValue: string | null;
  actualValue: string | null;
  delta: string | null;
  deltaPct: string | null;
  status: string;
}

export interface MatchRun {
  id: string;
  result: "clean" | "exceptions" | "blocked";
  reason: string | null;
  policyVersion: number;
  executedAt: string;
}

export interface MatchView {
  invoice: MatchInvoice;
  po: MatchPO | null;
  poLines: MatchPOLine[];
  invoiceLines: MatchInvoiceLine[];
  exceptions: MatchException[];
  matchRun: MatchRun | null;
}

interface WireMatchView {
  invoice: {
    id: string;
    invoice_number: string | null;
    total: string | null;
    currency: string;
    status: string;
  };
  po: { id: string; po_number: string; total: string | null } | null;
  po_lines: {
    id: string;
    line_no: number;
    sku: string | null;
    description: string;
    qty_ordered: string | null;
    qty_received: string | null;
    unit_price: string | null;
    line_total: string | null;
  }[];
  invoice_lines: {
    id: string;
    line_no: number;
    description: string;
    qty: string | null;
    unit_price: string | null;
    line_total: string | null;
    matched_po_line_id: string | null;
    match_method: string | null;
    match_confidence: string | null;
  }[];
  exceptions: {
    id: string;
    exception_type: string;
    severity: ExceptionSeverity;
    detail: string;
    po_line_id: string | null;
    invoice_line_id: string | null;
    expected_value: string | null;
    actual_value: string | null;
    delta: string | null;
    delta_pct: string | null;
    status: string;
  }[];
  match_run: {
    id: string;
    result: "clean" | "exceptions" | "blocked";
    reason: string | null;
    policy_version: number;
    executed_at: string;
  } | null;
}

function toView(wire: WireMatchView): MatchView {
  return {
    invoice: {
      id: wire.invoice.id,
      invoiceNumber: wire.invoice.invoice_number,
      total: wire.invoice.total,
      currency: wire.invoice.currency,
      status: wire.invoice.status,
    },
    po: wire.po && { id: wire.po.id, poNumber: wire.po.po_number, total: wire.po.total },
    poLines: wire.po_lines.map((line) => ({
      id: line.id,
      lineNo: line.line_no,
      sku: line.sku,
      description: line.description,
      qtyOrdered: line.qty_ordered,
      qtyReceived: line.qty_received,
      unitPrice: line.unit_price,
      lineTotal: line.line_total,
    })),
    invoiceLines: wire.invoice_lines.map((line) => ({
      id: line.id,
      lineNo: line.line_no,
      description: line.description,
      qty: line.qty,
      unitPrice: line.unit_price,
      lineTotal: line.line_total,
      matchedPoLineId: line.matched_po_line_id,
      matchMethod: line.match_method,
      matchConfidence: line.match_confidence,
    })),
    exceptions: wire.exceptions.map((exc) => ({
      id: exc.id,
      exceptionType: exc.exception_type,
      severity: exc.severity,
      detail: exc.detail,
      poLineId: exc.po_line_id,
      invoiceLineId: exc.invoice_line_id,
      expectedValue: exc.expected_value,
      actualValue: exc.actual_value,
      delta: exc.delta,
      deltaPct: exc.delta_pct,
      status: exc.status,
    })),
    matchRun: wire.match_run && {
      id: wire.match_run.id,
      result: wire.match_run.result,
      reason: wire.match_run.reason,
      policyVersion: wire.match_run.policy_version,
      executedAt: wire.match_run.executed_at,
    },
  };
}

export class MatchApiError extends Error {}

export async function fetchMatchView(
  apiBaseUrl: string,
  invoiceId: string,
  tenantId: string,
): Promise<MatchView> {
  const response = await fetch(
    `${apiBaseUrl}/v1/invoices/${invoiceId}/match?tenant_id=${encodeURIComponent(tenantId)}`,
  );
  if (!response.ok) {
    throw new MatchApiError(`failed to load match view: ${response.status}`);
  }
  return toView((await response.json()) as WireMatchView);
}

export interface DecideResult {
  /** "pending" means this caller's own approval was recorded but at least
   * one other required approver still hasn't acted -- the invoice has NOT
   * transitioned (apps/api/api/match_view.py::decide_invoice never
   * collapses every open approval_requests row on a single call). */
  status: "approved" | "rejected" | "pending";
  approvalsReceived: number;
  approvalsRequired: number;
}

export async function decideInvoiceMatch(
  apiBaseUrl: string,
  invoiceId: string,
  tenantId: string,
  decision: "approved" | "rejected",
  actorUserId: string,
): Promise<DecideResult> {
  const response = await fetch(
    `${apiBaseUrl}/v1/invoices/${invoiceId}/decide?tenant_id=${encodeURIComponent(tenantId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, actor_user_id: actorUserId }),
    },
  );
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new MatchApiError(
      typeof detail?.detail === "string" ? detail.detail : `decide failed: ${response.status}`,
    );
  }
  const data = (await response.json()) as {
    status: "approved" | "rejected" | "pending";
    approvals_received: number;
    approvals_required: number;
  };
  return {
    status: data.status,
    approvalsReceived: data.approvals_received,
    approvalsRequired: data.approvals_required,
  };
}

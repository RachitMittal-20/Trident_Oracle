/**
 * Parses/formats field_confidences.field_path values -- mirrors
 * apps/api/api/verification.py's _HEADER_FIELD_RE / _LINE_FIELD_RE exactly
 * (keep in sync with that module, not the other way around).
 */

export type FieldGroup = "header" | "totals" | "line";

const TOTALS_COLUMNS = new Set(["subtotal", "tax", "total"]);
const MONEY_COLUMNS = new Set(["subtotal", "tax", "total", "unit_price", "line_total"]);

export interface ParsedFieldPath {
  fieldPath: string;
  group: FieldGroup;
  /** 0-based, only present for a `lines[n].column` path. */
  lineIndex: number | null;
  column: string;
  label: string;
  isMoney: boolean;
}

const LABELS: Record<string, string> = {
  invoice_number: "Invoice Number",
  invoice_date: "Invoice Date",
  due_date: "Due Date",
  currency: "Currency",
  subtotal: "Subtotal",
  tax: "Tax",
  total: "Total",
  description: "Description",
  qty: "Qty",
  unit_price: "Unit Price",
  line_total: "Line Total",
};

export function parseFieldPath(fieldPath: string): ParsedFieldPath | null {
  const headerMatch = /^header\.(\w+)$/.exec(fieldPath);
  if (headerMatch) {
    const column = headerMatch[1]!;
    return {
      fieldPath,
      group: TOTALS_COLUMNS.has(column) ? "totals" : "header",
      lineIndex: null,
      column,
      label: LABELS[column] ?? column,
      isMoney: MONEY_COLUMNS.has(column),
    };
  }

  const lineMatch = /^lines\[(\d+)\]\.(\w+)$/.exec(fieldPath);
  if (lineMatch) {
    const column = lineMatch[2]!;
    return {
      fieldPath,
      group: "line",
      lineIndex: Number(lineMatch[1]),
      column,
      label: LABELS[column] ?? column,
      isMoney: MONEY_COLUMNS.has(column),
    };
  }

  return null;
}

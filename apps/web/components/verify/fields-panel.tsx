"use client";

import { FieldRow } from "@/components/verify/field-row";
import { EditableValue } from "@/components/verify/editable-value";
import { ConfidenceBar } from "@/components/confidence-bar";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { parseFieldPath } from "@/lib/field-paths";
import type { FieldConfidence, InvoiceLine, VerificationInvoice } from "@/lib/verify-api";

export interface FieldsPanelProps {
  invoice: VerificationInvoice;
  hoveredFieldPath: string | null;
  onHoverField: (fieldPath: string | null) => void;
  onClickField: (fieldPath: string) => void;
  onSaveField: (fieldPath: string, newValue: string) => Promise<void>;
}

function headerValue(invoice: VerificationInvoice, column: string): string {
  switch (column) {
    case "invoice_number":
      return invoice.invoiceNumber ?? "";
    case "invoice_date":
      return invoice.invoiceDate ?? "";
    case "due_date":
      return invoice.dueDate ?? "";
    case "currency":
      return invoice.currency;
    case "subtotal":
      return invoice.subtotal ?? "0";
    case "tax":
      return invoice.tax ?? "0";
    case "total":
      return invoice.total ?? "0";
    default:
      return "";
  }
}

function lineValue(line: InvoiceLine, column: string): string {
  switch (column) {
    case "description":
      return line.description;
    case "qty":
      return line.qty;
    case "unit_price":
      return line.unitPrice;
    case "line_total":
      return line.lineTotal;
    default:
      return "";
  }
}

function GroupSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-1">
      <h2 className="px-2 text-xs font-semibold uppercase tracking-wide text-text-muted">{title}</h2>
      <div className="space-y-0.5">{children}</div>
    </section>
  );
}

export function FieldsPanel({
  invoice,
  hoveredFieldPath,
  onHoverField,
  onClickField,
  onSaveField,
}: FieldsPanelProps) {
  const parsed = invoice.fieldConfidences
    .map((fc) => ({ fc, parsed: parseFieldPath(fc.fieldPath) }))
    .filter((entry): entry is { fc: FieldConfidence; parsed: NonNullable<ReturnType<typeof parseFieldPath>> } =>
      entry.parsed !== null,
    );

  const headerFields = parsed.filter((entry) => entry.parsed.group === "header");
  const totalsFields = parsed.filter((entry) => entry.parsed.group === "totals");
  const lineFieldsByIndex = new Map<number, typeof parsed>();
  for (const entry of parsed.filter((e) => e.parsed.group === "line")) {
    const list = lineFieldsByIndex.get(entry.parsed.lineIndex!) ?? [];
    list.push(entry);
    lineFieldsByIndex.set(entry.parsed.lineIndex!, list);
  }

  return (
    <div className="flex h-full flex-col gap-6 overflow-y-auto pr-1">
      {headerFields.length > 0 && (
        <GroupSection title="Header">
          {headerFields.map(({ fc, parsed: p }) => (
            <FieldRow
              key={fc.fieldPath}
              parsed={p}
              value={headerValue(invoice, p.column)}
              confidence={Number(fc.confidence)}
              currency={invoice.currency}
              humanCorrected={fc.humanCorrected}
              isHovered={hoveredFieldPath === fc.fieldPath}
              onHoverChange={onHoverField}
              onClick={onClickField}
              onSave={onSaveField}
            />
          ))}
        </GroupSection>
      )}

      {totalsFields.length > 0 && (
        <GroupSection title="Totals">
          {totalsFields.map(({ fc, parsed: p }) => (
            <FieldRow
              key={fc.fieldPath}
              parsed={p}
              value={headerValue(invoice, p.column)}
              confidence={Number(fc.confidence)}
              currency={invoice.currency}
              humanCorrected={fc.humanCorrected}
              isHovered={hoveredFieldPath === fc.fieldPath}
              onHoverChange={onHoverField}
              onClick={onClickField}
              onSave={onSaveField}
            />
          ))}
        </GroupSection>
      )}

      {invoice.lines.length > 0 && (
        <GroupSection title="Line Items">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Description</TableHead>
                <TableHead>Qty</TableHead>
                <TableHead>Unit Price</TableHead>
                <TableHead>Line Total</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {invoice.lines.map((line) => {
                const fields = lineFieldsByIndex.get(line.lineNo - 1) ?? [];
                const byColumn = new Map(fields.map((entry) => [entry.parsed.column, entry]));
                const columns = ["description", "qty", "unit_price", "line_total"] as const;
                return (
                  <TableRow key={line.id}>
                    {columns.map((column) => {
                      const entry = byColumn.get(column);
                      if (!entry) {
                        return <TableCell key={column}>{lineValue(line, column)}</TableCell>;
                      }
                      const isHovered = hoveredFieldPath === entry.fc.fieldPath;
                      return (
                        <TableCell
                          key={column}
                          className={isHovered ? "bg-bg-overlay" : undefined}
                          onMouseEnter={() => onHoverField(entry.fc.fieldPath)}
                          onMouseLeave={() => onHoverField(null)}
                          onClick={() => onClickField(entry.fc.fieldPath)}
                        >
                          <div className="flex flex-col gap-1">
                            <EditableValue
                              value={lineValue(line, column)}
                              isMoney={entry.parsed.isMoney}
                              currency={invoice.currency}
                              onSave={(newValue) => onSaveField(entry.fc.fieldPath, newValue)}
                            />
                            <ConfidenceBar value={Number(entry.fc.confidence)} className="max-w-20" />
                          </div>
                        </TableCell>
                      );
                    })}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </GroupSection>
      )}
    </div>
  );
}

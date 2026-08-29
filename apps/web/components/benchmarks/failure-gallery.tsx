"use client";

import { ImageOffIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import type { EvalFailureDocument } from "@/lib/benchmarks-api";

const HEADER_FIELDS = [
  "invoice_number",
  "invoice_date",
  "due_date",
  "vendor_name",
  "currency",
  "subtotal",
  "tax",
  "total",
] as const;

const FIELD_LABEL: Record<string, string> = {
  invoice_number: "Invoice #",
  invoice_date: "Invoice date",
  due_date: "Due date",
  vendor_name: "Vendor",
  currency: "Currency",
  subtotal: "Subtotal",
  tax: "Tax",
  total: "Total",
};

function headerValue(obj: Record<string, unknown>, field: string): string | null {
  const header = obj.header as Record<string, unknown> | undefined;
  const value = header?.[field];
  return typeof value === "string" ? value : null;
}

function FailureCard({ doc }: { doc: EvalFailureDocument }) {
  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-signal-block/30 bg-card">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <span className="font-mono text-xs text-text-muted">{doc.docId}</span>
        <span className="rounded-full bg-signal-block/10 px-2 py-0.5 text-xs font-medium text-signal-block">
          {doc.mismatchCount} field{doc.mismatchCount === 1 ? "" : "s"} wrong
        </span>
      </div>

      <div className="flex aspect-[4/3] items-center justify-center bg-bg-overlay">
        {doc.thumbnailUrl ? (
          // eslint-disable-next-line @next/next/no-img-element -- signed, short-lived Supabase Storage URL; next/image's remote-pattern allowlist doesn't fit a per-run signed path
          <img
            src={doc.thumbnailUrl}
            alt={`Source document for ${doc.docId}`}
            className="size-full object-cover"
          />
        ) : (
          <div className="flex flex-col items-center gap-1 text-text-muted">
            <ImageOffIcon className="size-6" />
            <span className="text-xs">No thumbnail</span>
          </div>
        )}
      </div>

      <div className="flex-1 space-y-1.5 p-3">
        {HEADER_FIELDS.map((field) => {
          const expected = headerValue(doc.groundTruth, field);
          const extracted = headerValue(doc.extractionResult, field);
          if (!expected && !extracted) return null;
          const mismatch = (expected ?? "").trim().toLowerCase() !== (extracted ?? "").trim().toLowerCase();
          return (
            <div key={field} className="grid grid-cols-[80px_1fr_1fr] items-baseline gap-2 text-xs">
              <span className="text-text-muted">{FIELD_LABEL[field]}</span>
              <span className="truncate font-mono text-text-primary" title={expected ?? undefined}>
                {expected ?? "—"}
              </span>
              <span
                className={cn(
                  "truncate font-mono",
                  mismatch ? "font-semibold text-signal-block" : "text-text-muted",
                )}
                title={extracted ?? undefined}
              >
                {extracted ?? "—"}
              </span>
            </div>
          );
        })}
        <div className="grid grid-cols-[80px_1fr_1fr] gap-2 pt-1 text-[10px] uppercase tracking-wide text-text-muted">
          <span />
          <span>Expected</span>
          <span>Extracted</span>
        </div>
      </div>
    </div>
  );
}

export function FailureGallery({ documents }: { documents: EvalFailureDocument[] }) {
  if (documents.length === 0) {
    return null;
  }

  return (
    <div className="rounded-lg border-2 border-signal-block/40 bg-card p-4">
      <div className="mb-1 flex items-center gap-2">
        <h3 className="text-sm font-semibold text-text-primary">Failure gallery</h3>
        <span className="text-xs text-text-muted">
          the worst {documents.length} documents in this run, worst first
        </span>
      </div>
      <p className="mb-4 text-xs text-text-muted">
        Shown deliberately, not buried: this is where the backend got it wrong.
      </p>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {documents.map((doc) => (
          <FailureCard key={doc.docId} doc={doc} />
        ))}
      </div>
    </div>
  );
}

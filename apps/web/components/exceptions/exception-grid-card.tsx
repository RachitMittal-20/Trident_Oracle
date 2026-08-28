"use client";

import { CheckIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { MoneyValue } from "@/components/money-value";
import { formatAge } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ExceptionCard } from "@/lib/exceptions-api";

const TYPE_LABEL: Record<string, string> = {
  NO_PO: "No purchase order",
  NO_GRN: "No goods receipt",
  DUPLICATE_INVOICE: "Duplicate invoice",
  SUSPECTED_DUPLICATE: "Suspected duplicate",
  PRICE_VARIANCE: "Price variance",
  QTY_SHORT: "Quantity short",
  QTY_OVER: "Quantity over",
  UNMATCHED_LINE: "Unmatched line",
  ARITHMETIC_ERROR: "Arithmetic error",
  TAX_MISMATCH: "Tax mismatch",
  DATE_ANOMALY: "Date anomaly",
};

// Weight and colour both carry severity -- restraint over a loud badge.
const SEVERITY_BORDER: Record<string, string> = {
  info: "border-l-2 border-l-text-muted",
  warn: "border-l-[3px] border-l-signal-warn",
  block: "border-l-4 border-l-signal-block",
};

export interface ExceptionGridCardProps {
  exception: ExceptionCard;
  selected: boolean;
  resolving: boolean;
  onToggleSelect: (event: React.MouseEvent) => void;
  onResolve: () => void;
  onOpenInvoice: () => void;
}

export function ExceptionGridCard({
  exception,
  selected,
  resolving,
  onToggleSelect,
  onResolve,
  onOpenInvoice,
}: ExceptionGridCardProps) {
  return (
    <div
      data-exception-card
      className={cn(
        "group/card flex cursor-pointer flex-col gap-2 rounded-lg border border-border bg-card p-4",
        SEVERITY_BORDER[exception.severity],
        selected && "ring-1 ring-accent",
      )}
      style={{ opacity: 0, filter: "blur(4px)", transform: "translateY(12px)" }}
      onClick={onOpenInvoice}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-text-primary">
            {exception.vendorName ?? "Unknown vendor"}
          </p>
          <p className="truncate font-mono text-xs text-text-muted">
            {exception.invoiceNumber ?? "—"}
          </p>
        </div>
        <span onClick={(event) => event.stopPropagation()}>
          <Checkbox
            checked={selected}
            onCheckedChange={() => {}}
            onClick={onToggleSelect}
            className="opacity-0 group-hover/card:opacity-100 data-checked:opacity-100"
            aria-label="Select exception"
          />
        </span>
      </div>

      {exception.invoiceTotal !== null && (
        <MoneyValue amount={exception.invoiceTotal} currency={exception.currency} className="text-lg font-semibold" />
      )}

      <p className="text-xs font-medium text-text-muted">
        {TYPE_LABEL[exception.exceptionType] ?? exception.exceptionType}
      </p>
      <p className="line-clamp-2 text-xs text-text-muted">{exception.detail}</p>

      <div className="mt-1 flex items-center justify-between">
        <span className="text-xs text-text-muted">{formatAge(exception.createdAt)} ago</span>
        <Button
          variant="ghost"
          size="sm"
          disabled={resolving}
          onClick={(event) => {
            event.stopPropagation();
            onResolve();
          }}
        >
          <CheckIcon data-icon="inline-start" />
          {resolving ? "Resolving…" : "Resolve"}
        </Button>
      </div>
    </div>
  );
}

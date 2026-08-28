import { MoneyValue } from "@/components/money-value";
import { cn } from "@/lib/utils";
import type { MatchException } from "@/lib/match-api";

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

export interface ExceptionCardProps {
  exception: MatchException;
  currency: string;
  isHovered: boolean;
  onHoverChange: (id: string | null) => void;
}

export function ExceptionCard({ exception, currency, isHovered, onHoverChange }: ExceptionCardProps) {
  const isBlock = exception.severity === "block";

  return (
    <div
      data-exception-id={exception.id}
      className={cn(
        "cursor-default rounded-lg border-l-2 bg-card p-3 transition-colors",
        isBlock ? "border-l-signal-block" : "border-l-signal-warn",
        isHovered && "bg-bg-overlay",
      )}
      onMouseEnter={() => onHoverChange(exception.id)}
      onMouseLeave={() => onHoverChange(null)}
    >
      <div className="flex items-center justify-between">
        <span
          className={cn(
            "text-xs font-semibold uppercase tracking-wide",
            isBlock ? "text-signal-block" : "text-signal-warn",
          )}
        >
          {TYPE_LABEL[exception.exceptionType] ?? exception.exceptionType}
        </span>
      </div>
      <p className="mt-1.5 text-xs text-text-muted">{exception.detail}</p>
      {(exception.expectedValue !== null || exception.actualValue !== null) && (
        <div className="mt-2 flex items-center gap-3 font-mono text-xs tabular-nums">
          {exception.expectedValue !== null && (
            <span className="text-text-muted">
              expected <MoneyValue amount={exception.expectedValue} currency={currency} className="text-text-primary" />
            </span>
          )}
          {exception.actualValue !== null && (
            <span className="text-text-muted">
              actual <MoneyValue amount={exception.actualValue} currency={currency} className="text-text-primary" />
            </span>
          )}
          {exception.delta !== null && (
            <span className={isBlock ? "text-signal-block" : "text-signal-warn"}>
              Δ {Number(exception.delta) > 0 ? "+" : ""}
              {exception.delta}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

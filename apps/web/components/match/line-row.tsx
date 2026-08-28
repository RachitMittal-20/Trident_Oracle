import { MoneyValue } from "@/components/money-value";
import { cn } from "@/lib/utils";
import type { LineOutcome } from "@/lib/match-layout";

export interface LineRowProps {
  rowKey: string;
  description: string;
  qty: string;
  unitPrice: string;
  lineTotal: string;
  currency?: string;
  outcome?: LineOutcome;
  deltaField?: "qty" | "unitPrice" | null;
  deltaTone?: "warn" | "block";
  isHighlighted?: boolean;
  isDimmed?: boolean;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
}

const OUTCOME_BORDER: Record<LineOutcome, string> = {
  clean: "border-border",
  variance: "border-signal-warn/60",
  block: "border-signal-block/60",
  unmatched: "border-dashed border-signal-block/40",
};

export function LineRow({
  rowKey,
  description,
  qty,
  unitPrice,
  lineTotal,
  currency = "USD",
  outcome,
  deltaField,
  deltaTone,
  isHighlighted,
  isDimmed,
  onMouseEnter,
  onMouseLeave,
}: LineRowProps) {
  return (
    <div
      data-row-key={rowKey}
      data-outcome={outcome}
      className={cn(
        // The mount entrance (opacity 0 -> 1) is driven directly by
        // components/match/match-board.tsx's single orchestrated timeline
        // -- anime.js writes to the real `opacity` property frame-by-frame
        // (or instantly, under reduced motion), so that property must NOT
        // also carry a CSS transition, or the browser would additionally
        // animate every one of those writes on its own. Dimming for the
        // exception cross-highlight uses `filter: opacity()` instead --
        // a different CSS property -- specifically so its 120ms
        // transition can never interfere with the entrance.
        "flex items-center justify-between gap-2 rounded-md border border-l-2 bg-card px-3 py-2 text-xs opacity-0 transition-[filter,background-color] duration-120",
        outcome ? OUTCOME_BORDER[outcome] : "border-border",
        isHighlighted && "bg-bg-overlay",
        isDimmed && "[filter:opacity(30%)]",
      )}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      <span className="min-w-0 flex-1 truncate text-text-primary">{description}</span>
      <span className="flex w-16 shrink-0 items-baseline justify-end gap-1 font-mono tabular-nums text-text-muted">
        {deltaField === "qty" && (
          <span
            data-delta-badge={rowKey}
            className={cn(
              "text-[10px] font-medium",
              deltaTone === "block" ? "text-signal-block" : "text-signal-warn",
            )}
          />
        )}
        {qty}
      </span>
      <span className="flex w-20 shrink-0 items-baseline justify-end gap-1 font-mono tabular-nums text-text-muted">
        {deltaField === "unitPrice" && (
          <span
            data-delta-badge={rowKey}
            className={cn(
              "text-[10px] font-medium",
              deltaTone === "block" ? "text-signal-block" : "text-signal-warn",
            )}
          />
        )}
        <MoneyValue amount={unitPrice} currency={currency} />
      </span>
      <span className="w-20 shrink-0 text-right font-mono tabular-nums text-text-primary">
        <MoneyValue amount={lineTotal} currency={currency} />
      </span>
    </div>
  );
}

import { AnimatedMoneyValue } from "@/components/animated-money-value";
import { cn } from "@/lib/utils";

export interface SummaryStripProps {
  ordered: string;
  received: string;
  invoiced: string;
  currency: string;
}

export function SummaryStrip({ ordered, received, invoiced, currency }: SummaryStripProps) {
  const varianceValue = Number(invoiced) - Number(ordered);
  const hasVariance = Math.abs(varianceValue) > 0.005;

  return (
    <div className="flex items-center gap-8 rounded-lg border border-border bg-card px-5 py-4">
      <div className="flex flex-col gap-1">
        <span className="text-xs text-text-muted">Ordered</span>
        <AnimatedMoneyValue amount={ordered} currency={currency} className="text-lg font-semibold" />
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-xs text-text-muted">Received</span>
        <AnimatedMoneyValue amount={received} currency={currency} className="text-lg font-semibold" />
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-xs text-text-muted">Invoiced</span>
        <AnimatedMoneyValue amount={invoiced} currency={currency} className="text-lg font-semibold" />
      </div>
      {hasVariance && (
        <div className="ml-auto flex flex-col items-end gap-1">
          <span className="text-xs text-text-muted">Variance (invoiced vs. ordered)</span>
          <span
            className={cn(
              "font-mono text-lg font-semibold tabular-nums",
              varianceValue > 0 ? "text-signal-block" : "text-signal-warn",
            )}
          >
            {varianceValue > 0 ? "+" : ""}
            <AnimatedMoneyValue amount={varianceValue.toFixed(2)} currency={currency} />
          </span>
        </div>
      )}
    </div>
  );
}

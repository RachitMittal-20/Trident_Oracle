import { forwardRef } from "react";

import { MoneyValue } from "@/components/money-value";
import { cn } from "@/lib/utils";
import type { PipelineCard } from "@/lib/pipeline-events";

export interface PipelineCardViewProps {
  card: PipelineCard;
}

/**
 * Purely presentational -- position, entrance, travel, and exit are all
 * driven imperatively by PipelineBoard via this element's
 * `data-invoice-id` attribute (an animejs target selector), never through
 * React-computed inline styles. That split is what lets the same DOM node
 * survive an entire transition sequence (shake, then move; flash, then
 * drop) without React re-rendering a conflicting transform mid-animation.
 */
export const PipelineCardView = forwardRef<HTMLDivElement, PipelineCardViewProps>(
  function PipelineCardView({ card }, ref) {
    const isHolding = card.status === "PENDING_APPROVAL";
    const isFailed = card.status === "EXTRACTION_FAILED";

    return (
      <div
        ref={ref}
        data-invoice-id={card.invoiceId}
        data-status={card.status}
        data-stage={card.stage}
        className={cn(
          "absolute left-0 top-0 flex w-38 flex-col gap-0.5 rounded-md border bg-card px-2.5 py-1.5 text-[11px] opacity-0 shadow-sm",
          isHolding && "border-l-2 border-l-signal-warn",
          isFailed && "border-l-2 border-l-signal-block",
          !isHolding && !isFailed && "border-border",
        )}
      >
        <div data-flash className="pointer-events-none absolute inset-0 rounded-md bg-signal-block opacity-0" />
        <div data-settle className="pointer-events-none absolute inset-0 rounded-md bg-signal-clean opacity-0" />
        <span className="relative truncate font-medium text-text-primary">
          {card.vendorName ?? "Processing…"}
        </span>
        <div className="relative flex items-center justify-between gap-2 font-mono text-[10px] text-text-muted">
          <span className="truncate">{card.invoiceNumber ?? "—"}</span>
          {card.amount !== null ? (
            <MoneyValue amount={card.amount} currency={card.currency} className="text-[10px]" />
          ) : (
            <span>—</span>
          )}
        </div>
      </div>
    );
  },
);

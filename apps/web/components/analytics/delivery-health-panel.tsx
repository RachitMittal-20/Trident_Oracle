"use client";

import { cn } from "@/lib/utils";
import type { DeliveryHealth } from "@/lib/analytics-api";

export function DeliveryHealthPanel({ health }: { health: DeliveryHealth }) {
  const hasDeadLetters = health.deadLetterCount > 0;

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <h3 className="mb-3 text-xs font-medium text-text-muted">Delivery health</h3>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div>
          <p className="text-xs text-text-muted">Success rate</p>
          <p className="font-mono text-xl font-semibold tabular-nums text-text-primary">
            {health.successRatePct === null ? "—" : `${Number(health.successRatePct).toFixed(1)}%`}
          </p>
          <p className="text-xs text-text-muted">
            {health.sentDeliveries} / {health.totalDeliveries} sent
          </p>
        </div>
        <div>
          <p className="text-xs text-text-muted">Mean attempts</p>
          <p className="font-mono text-xl font-semibold tabular-nums text-text-primary">
            {Number(health.meanAttempts).toFixed(2)}
          </p>
          <p className="text-xs text-text-muted">max {health.maxAttempts}</p>
        </div>
        <div className="col-span-2 sm:col-span-2">
          <p className="text-xs text-text-muted">Dead letters</p>
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "size-2 rounded-full",
                hasDeadLetters ? "bg-signal-block" : "bg-signal-clean",
              )}
              aria-hidden="true"
            />
            <p
              className={cn(
                "font-mono text-xl font-semibold tabular-nums",
                hasDeadLetters ? "text-signal-block" : "text-text-primary",
              )}
            >
              {health.deadLetterCount}
            </p>
          </div>
          <p className="text-xs text-text-muted">
            {hasDeadLetters ? "needs attention" : "clean"}
          </p>
        </div>
      </div>
    </div>
  );
}

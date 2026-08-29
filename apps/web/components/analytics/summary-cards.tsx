"use client";

import { useRef } from "react";
import { animate } from "animejs";

import { formatMoneyPlain } from "@/components/money-value";
import { StatCard } from "@/components/stat-card";
import { formatDurationSeconds } from "@/lib/format";
import { DURATION, useAnimeTimeline, useReducedMotion, withMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";
import type { AnalyticsSummary } from "@/lib/analytics-api";

const STAGGER_MS = 80;

const SEVERITY_LABEL: Record<string, string> = { block: "block", warn: "warn", info: "info" };
const SEVERITY_DOT: Record<string, string> = {
  block: "bg-signal-block",
  warn: "bg-signal-warn",
  info: "bg-text-muted",
};

function ValueAtRiskCard({ value, delay }: { value: string; delay: number }) {
  const valueRef = useRef<HTMLSpanElement>(null);
  const reducedMotion = useReducedMotion();
  const numeric = Number(value);

  useAnimeTimeline(
    () =>
      withMotion(reducedMotion, () => {
        const counter = { current: 0 };
        return animate(counter, {
          current: numeric,
          duration: DURATION.slow,
          delay,
          ease: "easeOutExpo",
          onUpdate: () => {
            if (valueRef.current) {
              valueRef.current.textContent = formatMoneyPlain(counter.current.toFixed(2));
            }
          },
        });
      }),
    [numeric, delay, reducedMotion],
  );

  return (
    <div className="col-span-2 rounded-lg border border-signal-block/30 bg-card p-4 sm:row-span-1">
      <div className="text-xs font-medium text-text-muted">Value at risk</div>
      <div className="mt-2">
        <span
          ref={valueRef}
          className="font-mono text-3xl font-bold tabular-nums text-signal-block"
        >
          {reducedMotion ? formatMoneyPlain(value) : formatMoneyPlain("0")}
        </span>
      </div>
      <p className="mt-1 text-xs text-text-muted">Total on open blocking exceptions</p>
    </div>
  );
}

function ExceptionsCard({
  bySeverity,
  delay,
}: {
  bySeverity: Record<string, number>;
  delay: number;
}) {
  const total = Object.values(bySeverity).reduce((sum, n) => sum + n, 0);
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <StatCard label="Exceptions raised" value={total} delay={delay} className="border-0 p-0" />
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
        {(["block", "warn", "info"] as const).map((severity) =>
          bySeverity[severity] ? (
            <span key={severity} className="inline-flex items-center gap-1 text-xs text-text-muted">
              <span className={cn("size-1.5 rounded-full", SEVERITY_DOT[severity])} />
              {bySeverity[severity]} {SEVERITY_LABEL[severity]}
            </span>
          ) : null,
        )}
      </div>
    </div>
  );
}

export function SummaryCards({ summary }: { summary: AnalyticsSummary }) {
  const meanConfidencePct =
    summary.meanExtractionConfidence === null ? null : Number(summary.meanExtractionConfidence) * 100;
  const autoPostRate = summary.autoPostRatePct === null ? null : Number(summary.autoPostRatePct);

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-7">
      <StatCard
        label="Invoices processed"
        value={summary.invoicesProcessed}
        delta={summary.invoicesProcessedDelta}
        delay={0 * STAGGER_MS}
      />
      <StatCard
        label="Auto-post rate"
        value={autoPostRate ?? 0}
        delay={1 * STAGGER_MS}
        formatValue={(v) => (autoPostRate === null ? "—" : `${v.toFixed(1)}%`)}
      />
      <StatCard
        label="Mean extraction confidence"
        value={meanConfidencePct ?? 0}
        delay={2 * STAGGER_MS}
        formatValue={(v) => (meanConfidencePct === null ? "—" : `${v.toFixed(1)}%`)}
      />
      <ExceptionsCard bySeverity={summary.exceptionsBySeverity} delay={3 * STAGGER_MS} />
      <StatCard
        label="Mean time to decision"
        value={summary.meanSecondsToDecision ?? 0}
        delay={4 * STAGGER_MS}
        formatValue={(v) =>
          summary.meanSecondsToDecision === null ? "—" : formatDurationSeconds(v)
        }
      />
      <ValueAtRiskCard value={summary.valueAtRisk} delay={5 * STAGGER_MS} />
    </div>
  );
}

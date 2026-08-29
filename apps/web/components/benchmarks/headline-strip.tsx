"use client";

import { StatCard } from "@/components/stat-card";
import { formatDurationSeconds } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { EvalRunDetail } from "@/lib/benchmarks-api";

const STAGGER_MS = 80;

function RunHeadline({ run, delayOffset }: { run: EvalRunDetail; delayOffset: number }) {
  const exactMatchPct = run.overallExactMatchRate === null ? null : run.overallExactMatchRate * 100;
  const meanLatencySeconds = run.meanLatencyMs === null ? null : Number(run.meanLatencyMs) / 1000;
  const costPer1000 = run.costPer1000Usd;

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-sm font-semibold text-text-primary">{run.dataset}</span>
        <span className="text-sm text-text-muted">{run.backend}</span>
        <span className="font-mono text-xs text-text-muted">{run.modelVersion ?? "n/a"}</span>
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard
          label="Sample count"
          value={run.sampleCount}
          delay={delayOffset + 0 * STAGGER_MS}
        />
        <StatCard
          label="Exact-match rate"
          value={exactMatchPct ?? 0}
          delay={delayOffset + 1 * STAGGER_MS}
          formatValue={(v) => (exactMatchPct === null ? "—" : `${v.toFixed(1)}%`)}
        />
        <StatCard
          label="Mean latency"
          value={meanLatencySeconds ?? 0}
          delay={delayOffset + 2 * STAGGER_MS}
          formatValue={(v) =>
            meanLatencySeconds === null ? "—" : formatDurationSeconds(v)
          }
        />
        <StatCard
          label="Cost / 1,000 invoices"
          value={costPer1000 ?? 0}
          delay={delayOffset + 3 * STAGGER_MS}
          formatValue={(v) => (costPer1000 === null ? "—" : `$${v.toFixed(2)}`)}
        />
      </div>
    </div>
  );
}

export function HeadlineStrip({ runs }: { runs: EvalRunDetail[] }) {
  return (
    <div className={cn("grid gap-4", runs.length > 1 ? "md:grid-cols-2" : "grid-cols-1")}>
      {runs.map((run, i) => (
        <RunHeadline key={run.id} run={run} delayOffset={i * 4 * STAGGER_MS} />
      ))}
    </div>
  );
}

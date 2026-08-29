"use client";

import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { DURATION, useReducedMotion } from "@/lib/motion";
import { useInView } from "@/lib/use-in-view";
import type { EvalRunDetail } from "@/lib/benchmarks-api";

const RUN_COLOR = ["var(--accent)", "var(--signal-warn)"];

export function LatencyDistribution({ runs }: { runs: EvalRunDetail[] }) {
  const [ref, inView] = useInView<HTMLDivElement>();
  const reducedMotion = useReducedMotion();
  const shouldRender = reducedMotion || inView;

  const rows = ["p50", "p95", "p99"].map((label) => {
    const row: Record<string, string | number | null> = { label };
    for (const run of runs) {
      const value =
        label === "p50" ? run.latencyP50Ms : label === "p95" ? run.latencyP95Ms : run.latencyP99Ms;
      row[run.id] = value === null ? null : Number(value);
    }
    return row;
  });

  return (
    <div ref={ref} className="rounded-lg border border-border bg-card p-4">
      <h3 className="mb-3 text-xs font-medium text-text-muted">Latency distribution (ms)</h3>
      <div className="h-64">
        {shouldRender ? (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: "var(--text-muted)" }} />
              <YAxis tick={{ fontSize: 11, fill: "var(--text-muted)" }} />
              <Tooltip
                contentStyle={{
                  background: "var(--bg-overlay)",
                  border: "1px solid var(--border)",
                  fontSize: 12,
                }}
              />
              {runs.length > 1 && (
                <Legend
                  wrapperStyle={{ fontSize: 11 }}
                  formatter={(_value, entry) => {
                    const run = runs.find((r) => r.id === (entry as { dataKey?: string }).dataKey);
                    return run ? run.backend : _value;
                  }}
                />
              )}
              {runs.map((run, i) => (
                <Bar
                  key={run.id}
                  dataKey={run.id}
                  name={run.backend}
                  fill={RUN_COLOR[i % RUN_COLOR.length]}
                  isAnimationActive={!reducedMotion}
                  animationDuration={DURATION.slow}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        ) : null}
      </div>
    </div>
  );
}

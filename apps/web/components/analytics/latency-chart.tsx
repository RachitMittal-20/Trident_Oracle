"use client";

import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { ChartCard } from "@/components/analytics/chart-card";
import { DURATION } from "@/lib/motion";
import type { LatencyResponse } from "@/lib/analytics-api";

const STAGE_LABEL: Record<keyof LatencyResponse, string> = {
  extraction: "Extraction",
  matching: "Matching",
  notification: "Notification",
};

export function LatencyChart({ data }: { data: LatencyResponse }) {
  const rows = (Object.keys(STAGE_LABEL) as (keyof LatencyResponse)[]).map((stage) => ({
    stage: STAGE_LABEL[stage],
    p50: data[stage].p50,
    p95: data[stage].p95,
    p99: data[stage].p99,
  }));

  return (
    <ChartCard title="Processing latency by stage (ms)">
      {({ animate }) => (
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="stage" tick={{ fontSize: 11, fill: "var(--text-muted)" }} />
            <YAxis tick={{ fontSize: 11, fill: "var(--text-muted)" }} />
            <Tooltip
              contentStyle={{
                background: "var(--bg-overlay)",
                border: "1px solid var(--border)",
                fontSize: 12,
              }}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar dataKey="p50" name="p50" fill="var(--signal-clean)" isAnimationActive={animate} animationDuration={DURATION.slow} />
            <Bar dataKey="p95" name="p95" fill="var(--signal-warn)" isAnimationActive={animate} animationDuration={DURATION.slow} />
            <Bar dataKey="p99" name="p99" fill="var(--signal-block)" isAnimationActive={animate} animationDuration={DURATION.slow} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

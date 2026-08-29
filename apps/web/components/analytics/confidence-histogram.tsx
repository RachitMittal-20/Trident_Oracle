"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { ChartCard } from "@/components/analytics/chart-card";
import { DURATION } from "@/lib/motion";
import type { ConfidenceBucket } from "@/lib/analytics-api";

export function ConfidenceHistogram({ data }: { data: ConfidenceBucket[] }) {
  const rows = data.map((bucket) => ({
    label: `${Math.round(bucket.bucketStart * 100)}-${Math.round(bucket.bucketEnd * 100)}%`,
    count: bucket.count,
  }));

  return (
    <ChartCard title="Extraction confidence distribution">
      {({ animate }) => (
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="label" tick={{ fontSize: 10, fill: "var(--text-muted)" }} interval={0} />
            <YAxis tick={{ fontSize: 11, fill: "var(--text-muted)" }} allowDecimals={false} />
            <Tooltip
              contentStyle={{
                background: "var(--bg-overlay)",
                border: "1px solid var(--border)",
                fontSize: 12,
              }}
            />
            <Bar
              dataKey="count"
              fill="var(--accent)"
              isAnimationActive={animate}
              animationDuration={DURATION.slow}
            />
          </BarChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

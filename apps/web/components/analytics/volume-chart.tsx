"use client";

import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { ChartCard } from "@/components/analytics/chart-card";
import { DURATION } from "@/lib/motion";
import type { VolumePoint } from "@/lib/analytics-api";

const OUTCOME_ORDER = ["auto_posted", "approved", "pending", "rejected", "failed"] as const;
const OUTCOME_LABEL: Record<(typeof OUTCOME_ORDER)[number], string> = {
  auto_posted: "Auto-posted",
  approved: "Approved",
  pending: "Pending",
  rejected: "Rejected",
  failed: "Failed",
};
const OUTCOME_COLOR: Record<(typeof OUTCOME_ORDER)[number], string> = {
  auto_posted: "var(--signal-clean)",
  approved: "var(--accent)",
  pending: "var(--text-muted)",
  rejected: "var(--signal-block)",
  failed: "var(--signal-warn)",
};

function pivot(points: VolumePoint[]): Record<string, number | string>[] {
  const byDay = new Map<string, Record<string, number | string>>();
  for (const point of points) {
    const row = byDay.get(point.day) ?? { day: point.day };
    row[point.outcome] = point.count;
    byDay.set(point.day, row);
  }
  return Array.from(byDay.values()).sort((a, b) => (a.day as string).localeCompare(b.day as string));
}

export function VolumeChart({ data }: { data: VolumePoint[] }) {
  const rows = pivot(data);

  return (
    <ChartCard title="Invoice volume over time">
      {({ animate }) => (
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="day" tick={{ fontSize: 11, fill: "var(--text-muted)" }} />
            <YAxis tick={{ fontSize: 11, fill: "var(--text-muted)" }} allowDecimals={false} />
            <Tooltip
              contentStyle={{
                background: "var(--bg-overlay)",
                border: "1px solid var(--border)",
                fontSize: 12,
              }}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} formatter={(value) => OUTCOME_LABEL[value as (typeof OUTCOME_ORDER)[number]] ?? value} />
            {OUTCOME_ORDER.map((outcome) => (
              <Bar
                key={outcome}
                dataKey={outcome}
                stackId="volume"
                fill={OUTCOME_COLOR[outcome]}
                isAnimationActive={animate}
                animationDuration={DURATION.slow}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

"use client";

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { ChartCard } from "@/components/analytics/chart-card";
import { DURATION } from "@/lib/motion";
import type { AutoPostTrendPoint } from "@/lib/analytics-api";

export function AutoPostTrendChart({ data }: { data: AutoPostTrendPoint[] }) {
  const rows = data.map((point) => ({
    day: point.day,
    ratePct: point.ratePct === null ? null : Number(point.ratePct),
  }));

  return (
    <ChartCard title="Auto-post rate trend">
      {({ animate }) => (
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="day" tick={{ fontSize: 11, fill: "var(--text-muted)" }} />
            <YAxis
              domain={[0, 100]}
              tickFormatter={(v: number) => `${v}%`}
              tick={{ fontSize: 11, fill: "var(--text-muted)" }}
            />
            <Tooltip
              contentStyle={{
                background: "var(--bg-overlay)",
                border: "1px solid var(--border)",
                fontSize: 12,
              }}
              formatter={(value) => [`${value}%`, "Auto-post rate"]}
            />
            <Line
              type="monotone"
              dataKey="ratePct"
              stroke="var(--signal-clean)"
              strokeWidth={2}
              dot={{ r: 3 }}
              connectNulls
              isAnimationActive={animate}
              animationDuration={DURATION.slow}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

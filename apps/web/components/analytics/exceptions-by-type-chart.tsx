"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { ChartCard } from "@/components/analytics/chart-card";
import { DURATION } from "@/lib/motion";
import type { ExceptionTypeCount } from "@/lib/analytics-api";

const TYPE_LABEL: Record<string, string> = {
  NO_PO: "No purchase order",
  NO_GRN: "No goods receipt",
  DUPLICATE_INVOICE: "Duplicate invoice",
  SUSPECTED_DUPLICATE: "Suspected duplicate",
  PRICE_VARIANCE: "Price variance",
  QTY_SHORT: "Quantity short",
  QTY_OVER: "Quantity over",
  UNMATCHED_LINE: "Unmatched line",
  ARITHMETIC_ERROR: "Arithmetic error",
  TAX_MISMATCH: "Tax mismatch",
  DATE_ANOMALY: "Date anomaly",
};

export function ExceptionsByTypeChart({ data }: { data: ExceptionTypeCount[] }) {
  const rows = data.map((row) => ({
    label: TYPE_LABEL[row.exceptionType] ?? row.exceptionType,
    count: row.count,
  }));

  return (
    <ChartCard title="Exceptions by type">
      {({ animate }) => (
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} layout="vertical" margin={{ left: 24 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
            <XAxis type="number" tick={{ fontSize: 11, fill: "var(--text-muted)" }} allowDecimals={false} />
            <YAxis
              type="category"
              dataKey="label"
              width={140}
              tick={{ fontSize: 11, fill: "var(--text-muted)" }}
            />
            <Tooltip
              contentStyle={{
                background: "var(--bg-overlay)",
                border: "1px solid var(--border)",
                fontSize: 12,
              }}
            />
            <Bar
              dataKey="count"
              fill="var(--signal-warn)"
              isAnimationActive={animate}
              animationDuration={DURATION.slow}
            />
          </BarChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

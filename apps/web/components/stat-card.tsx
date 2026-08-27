"use client";

import { useRef } from "react";
import { animate } from "animejs";

import { cn } from "@/lib/utils";
import { DURATION, EASING, useAnimeTimeline, useReducedMotion, withMotion } from "@/lib/motion";

export interface StatCardProps {
  label: string;
  /** Final numeric value to count up to. Use MoneyValue instead for currency. */
  value: number;
  /** Signed delta shown next to the value, e.g. +12 or -3. Omit to hide. */
  delta?: number;
  formatValue?: (value: number) => string;
  className?: string;
}

const defaultFormat = (value: number) => Math.round(value).toLocaleString("en-US");

export function StatCard({ label, value, delta, formatValue = defaultFormat, className }: StatCardProps) {
  const valueRef = useRef<HTMLSpanElement>(null);
  const reducedMotion = useReducedMotion();

  useAnimeTimeline(
    () =>
      withMotion(reducedMotion, () => {
        const counter = { current: 0 };
        return animate(counter, {
          current: value,
          duration: DURATION.slow,
          ease: EASING.entrance,
          onUpdate: () => {
            if (valueRef.current) {
              valueRef.current.textContent = formatValue(counter.current);
            }
          },
        });
      }),
    [value, reducedMotion],
  );

  const deltaTone =
    delta === undefined ? null : delta > 0 ? "text-signal-clean" : delta < 0 ? "text-signal-block" : "text-text-muted";

  return (
    <div className={cn("rounded-lg border border-border bg-card p-4", className)}>
      <div className="text-xs font-medium text-text-muted">{label}</div>
      <div className="mt-2 flex items-baseline gap-2">
        <span ref={valueRef} className="font-mono text-2xl font-semibold tabular-nums text-text-primary">
          {reducedMotion ? formatValue(value) : formatValue(0)}
        </span>
        {delta !== undefined && (
          <span className={cn("font-mono text-xs tabular-nums", deltaTone)}>
            {delta > 0 ? "+" : ""}
            {formatValue(delta)}
          </span>
        )}
      </div>
    </div>
  );
}

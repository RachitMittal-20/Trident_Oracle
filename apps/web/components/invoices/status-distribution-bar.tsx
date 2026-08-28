"use client";

import { useRef } from "react";
import { animate, stagger } from "animejs";

import { DURATION, EASING, useAnimeTimeline, useReducedMotion, withMotion } from "@/lib/motion";
import { SOLID_TONE_CLASSES, STATUS_CONFIG, type InvoiceStatus } from "@/components/status-pill";

export interface StatusDistributionBarProps {
  statusCounts: Record<string, number>;
}

const STATUS_ORDER = Object.keys(STATUS_CONFIG) as InvoiceStatus[];

export function StatusDistributionBar({ statusCounts }: StatusDistributionBarProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const reducedMotion = useReducedMotion();
  const total = Object.values(statusCounts).reduce((sum, n) => sum + n, 0);

  const segments = STATUS_ORDER
    .map((status) => ({ status, count: statusCounts[status] ?? 0 }))
    .filter((s) => s.count > 0);

  // CLAUDE.md: "Animate transform and opacity only -- never width, height,
  // top, left." Each segment is already laid out at its final flex-basis
  // percentage; only its transform scales in from 0, pinned to the left
  // edge, so no box-model property is what's animating.
  useAnimeTimeline(
    () =>
      withMotion(reducedMotion, () =>
        animate(containerRef.current!.querySelectorAll("[data-status-segment]"), {
          scaleX: [0, 1],
          duration: DURATION.slow,
          ease: EASING.entrance,
          delay: stagger(40),
        }),
      ),
    [statusCounts, reducedMotion],
  );

  if (total === 0) return null;

  return (
    <div className="space-y-2">
      <div ref={containerRef} className="flex h-2 w-full overflow-hidden rounded-full bg-bg-overlay">
        {segments.map(({ status, count }) => (
          <div
            key={status}
            data-status-segment
            title={`${STATUS_CONFIG[status].label}: ${count}`}
            className={SOLID_TONE_CLASSES[STATUS_CONFIG[status].tone]}
            style={{ flexBasis: `${(count / total) * 100}%`, transformOrigin: "left" }}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
        {segments.map(({ status, count }) => (
          <span key={status} className="inline-flex items-center gap-1.5">
            <span className={`size-1.5 rounded-full ${SOLID_TONE_CLASSES[STATUS_CONFIG[status].tone]}`} />
            {STATUS_CONFIG[status].label} · {count}
          </span>
        ))}
      </div>
    </div>
  );
}

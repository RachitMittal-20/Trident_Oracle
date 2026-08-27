"use client";

import { useRef } from "react";
import { animate } from "animejs";

import { cn } from "@/lib/utils";
import { DURATION, EASING, useAnimeTimeline, useReducedMotion, withMotion } from "@/lib/motion";

export interface ConfidenceBarProps {
  /** 0-1 confidence score. */
  value: number;
  className?: string;
}

function toneClass(value: number): string {
  if (value >= 0.85) return "bg-signal-clean";
  if (value >= 0.6) return "bg-signal-warn";
  return "bg-signal-block";
}

export function ConfidenceBar({ value, className }: ConfidenceBarProps) {
  const clamped = Math.min(1, Math.max(0, value));
  const fillRef = useRef<HTMLDivElement>(null);
  const reducedMotion = useReducedMotion();

  // CLAUDE.md: "Animate transform and opacity only -- never width, height,
  // top, left." The fill is a full-width element scaled via transform, not
  // resized; transform-origin pins the scale to the left edge.
  useAnimeTimeline(
    () =>
      withMotion(reducedMotion, () =>
        animate(fillRef.current!, {
          scaleX: [0, clamped],
          duration: DURATION.slow,
          ease: EASING.entrance,
        }),
      ),
    [clamped, reducedMotion],
  );

  return (
    <div
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-border", className)}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped * 100)}
    >
      <div
        ref={fillRef}
        className={cn("h-full w-full origin-left rounded-full", toneClass(clamped))}
        style={{ transform: reducedMotion ? `scaleX(${clamped})` : "scaleX(0)" }}
      />
    </div>
  );
}

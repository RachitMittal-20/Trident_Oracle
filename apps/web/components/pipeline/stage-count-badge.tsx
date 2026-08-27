"use client";

import { useRef } from "react";
import { animate } from "animejs";

import { DURATION, EASING, useAnimeTimeline, useReducedMotion, withMotion } from "@/lib/motion";
import { NODE_POSITION } from "@/lib/pipeline-layout";
import type { PipelineRailStage } from "@/lib/pipeline-events";

export interface StageCountBadgeProps {
  stage: Exclude<PipelineRailStage, "FAILED">;
  count: number;
}

/** Ticks up/down via an animejs number tween whenever `count` changes --
 * same counter technique as StatCard, just re-triggered on every change
 * rather than once on mount. */
export function StageCountBadge({ stage, count }: StageCountBadgeProps) {
  const valueRef = useRef<HTMLSpanElement>(null);
  const displayedRef = useRef(0);
  const reducedMotion = useReducedMotion();
  const pos = NODE_POSITION[stage];

  useAnimeTimeline(
    () =>
      withMotion(reducedMotion, () => {
        const counter = { current: displayedRef.current };
        return animate(counter, {
          current: count,
          duration: DURATION.base,
          ease: EASING.stateChange,
          onUpdate: () => {
            displayedRef.current = counter.current;
            if (valueRef.current) {
              valueRef.current.textContent = `${Math.round(counter.current)}`;
            }
          },
        });
      }),
    [count, reducedMotion],
  );

  if (reducedMotion && valueRef.current && displayedRef.current !== count) {
    displayedRef.current = count;
    valueRef.current.textContent = `${count}`;
  }

  return (
    <div
      className="absolute flex h-5 min-w-5 -translate-x-1/2 items-center justify-center rounded-full bg-bg-overlay px-1.5 font-mono text-[11px] font-medium tabular-nums text-text-primary"
      style={{ left: pos.x, top: pos.y - 46 }}
    >
      <span ref={valueRef}>0</span>
    </div>
  );
}

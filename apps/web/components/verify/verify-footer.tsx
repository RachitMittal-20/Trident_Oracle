"use client";

import { useRef } from "react";
import { animate } from "animejs";

import { Button } from "@/components/ui/button";
import { DURATION, EASING, useAnimeTimeline, useReducedMotion, withMotion } from "@/lib/motion";

export interface VerifyFooterProps {
  belowThresholdCount: number;
  onRerunMatch: () => void;
  rerunning: boolean;
}

export function VerifyFooter({ belowThresholdCount, onRerunMatch, rerunning }: VerifyFooterProps) {
  const countRef = useRef<HTMLSpanElement>(null);
  const displayedRef = useRef(belowThresholdCount);
  const reducedMotion = useReducedMotion();

  useAnimeTimeline(
    () =>
      withMotion(reducedMotion, () => {
        const counter = { current: displayedRef.current };
        return animate(counter, {
          current: belowThresholdCount,
          duration: DURATION.base,
          ease: EASING.stateChange,
          onUpdate: () => {
            displayedRef.current = counter.current;
            if (countRef.current) {
              countRef.current.textContent = `${Math.round(counter.current)}`;
            }
          },
        });
      }),
    [belowThresholdCount, reducedMotion],
  );

  if (reducedMotion && countRef.current && displayedRef.current !== belowThresholdCount) {
    displayedRef.current = belowThresholdCount;
    countRef.current.textContent = `${belowThresholdCount}`;
  }

  return (
    <div className="sticky bottom-0 flex items-center justify-between border-t border-border bg-bg-raised px-4 py-3">
      <span className="text-sm text-text-muted">
        <span ref={countRef} className="font-mono tabular-nums text-text-primary">
          {belowThresholdCount}
        </span>{" "}
        field{belowThresholdCount === 1 ? "" : "s"} below confidence threshold
      </span>
      <Button onClick={onRerunMatch} disabled={rerunning}>
        {rerunning ? "Starting…" : "Re-run Match"}
      </Button>
    </div>
  );
}

"use client";

import { useRef } from "react";
import { animate } from "animejs";

import { formatMoneyPlain } from "@/components/money-value";
import { DURATION, EASING, useAnimeTimeline, useReducedMotion, withMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";

export interface AnimatedMoneyValueProps {
  amount: string;
  currency?: string;
  className?: string;
}

/** MoneyValue that counts up from 0 on mount -- the summary strip's
 * ordered/received/invoiced totals. Uses the same plain-string formatter
 * MoneyValue itself renders with, written imperatively frame-by-frame
 * (see components/verify/editable-value.tsx for why: mixing that with
 * MoneyValue's own React-rendered children would let a re-render clobber
 * the in-progress count). */
export function AnimatedMoneyValue({ amount, currency = "USD", className }: AnimatedMoneyValueProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const reducedMotion = useReducedMotion();
  const target = Number(amount);

  useAnimeTimeline(
    () =>
      withMotion(reducedMotion, () => {
        const counter = { current: 0 };
        return animate(counter, {
          current: target,
          duration: DURATION.slow,
          ease: EASING.entrance,
          onUpdate: () => {
            if (ref.current) {
              ref.current.textContent = formatMoneyPlain(counter.current.toFixed(2), currency);
            }
          },
        });
      }),
    [target, currency, reducedMotion],
  );

  return (
    <span ref={ref} className={cn("font-mono tabular-nums", className)}>
      {reducedMotion ? formatMoneyPlain(amount, currency) : formatMoneyPlain("0", currency)}
    </span>
  );
}

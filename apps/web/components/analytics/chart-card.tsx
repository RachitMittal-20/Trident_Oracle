"use client";

import type { ReactNode } from "react";

import { useReducedMotion } from "@/lib/motion";
import { useInView } from "@/lib/use-in-view";
import { cn } from "@/lib/utils";

export interface ChartRenderArgs {
  /** false until this card has scrolled into view (or reduced motion is on,
   * in which case it's true immediately) -- gates when the chart actually
   * mounts, so Recharts' own entrance animation plays exactly once, on
   * first view, rather than on every re-render. */
  shouldRender: boolean;
  /** Whether Recharts should actually animate the draw-in, vs. snapping
   * straight to the final state. */
  animate: boolean;
}

export interface ChartCardProps {
  title: string;
  className?: string;
  children: (args: ChartRenderArgs) => ReactNode;
}

export function ChartCard({ title, className, children }: ChartCardProps) {
  const [ref, inView] = useInView<HTMLDivElement>();
  const reducedMotion = useReducedMotion();
  const shouldRender = reducedMotion || inView;

  return (
    <div ref={ref} className={cn("rounded-lg border border-border bg-card p-4", className)}>
      <h3 className="mb-3 text-xs font-medium text-text-muted">{title}</h3>
      <div className="h-64">
        {shouldRender ? children({ shouldRender, animate: !reducedMotion }) : null}
      </div>
    </div>
  );
}

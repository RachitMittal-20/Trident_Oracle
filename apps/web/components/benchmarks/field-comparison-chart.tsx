"use client";

import { useEffect } from "react";
import { animate, stagger } from "animejs";

import { DURATION, EASING, useReducedMotion, withMotion } from "@/lib/motion";
import { useInView } from "@/lib/use-in-view";
import type { EvalRunDetail } from "@/lib/benchmarks-api";

const RUN_COLOR = ["var(--accent)", "var(--signal-warn)"];

interface FieldRow {
  fieldPath: string;
  values: (number | null)[]; // one per run, 0-100
  gap: number;
}

function buildRows(runs: EvalRunDetail[]): FieldRow[] {
  const allPaths = Array.from(new Set(runs.flatMap((r) => r.fields.map((f) => f.fieldPath))));
  const rows: FieldRow[] = allPaths.map((fieldPath) => {
    const values = runs.map((run) => {
      const field = run.fields.find((f) => f.fieldPath === fieldPath);
      if (!field || field.exactMatchRate === null) return null;
      return Number(field.exactMatchRate) * 100;
    });
    const present = values.filter((v): v is number => v !== null);
    const gap = present.length === 2 ? Math.abs(present[0]! - present[1]!) : 0;
    return { fieldPath, values, gap };
  });

  if (runs.length > 1) {
    rows.sort((a, b) => b.gap - a.gap);
  } else {
    rows.sort((a, b) => (a.values[0] ?? 0) - (b.values[0] ?? 0));
  }
  return rows;
}

export function FieldComparisonChart({ runs }: { runs: EvalRunDetail[] }) {
  const [ref, inView] = useInView<HTMLDivElement>();
  const reducedMotion = useReducedMotion();
  const shouldRender = reducedMotion || inView;
  const rows = buildRows(runs);

  useEffect(() => {
    const container = ref.current;
    if (!container || !shouldRender) return;
    const bars = container.querySelectorAll<HTMLElement>("[data-comparison-bar]");
    if (bars.length === 0) return;

    if (reducedMotion) {
      for (const bar of bars) bar.style.transform = "scaleX(1)";
      return;
    }

    for (const bar of bars) bar.style.transform = "scaleX(0)";
    withMotion(reducedMotion, () =>
      animate(bars, {
        scaleX: [0, 1],
        duration: DURATION.base,
        ease: EASING.entrance,
        delay: stagger(40),
      }),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-run only when this card enters view or reduced-motion changes
  }, [shouldRender, reducedMotion]);

  return (
    <div ref={ref} className="rounded-lg border border-border bg-card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-xs font-medium text-text-muted">
          Per-field exact-match rate{runs.length > 1 ? " -- sorted by gap" : ""}
        </h3>
        {runs.length > 1 && (
          <div className="flex items-center gap-3 text-xs text-text-muted">
            {runs.map((run, i) => (
              <span key={run.id} className="inline-flex items-center gap-1.5">
                <span
                  className="size-2 rounded-sm"
                  style={{ background: RUN_COLOR[i % RUN_COLOR.length] }}
                />
                {run.backend}
              </span>
            ))}
          </div>
        )}
      </div>

      {shouldRender ? (
        <div className="flex flex-col gap-3">
          {rows.map((row) => (
            <div key={row.fieldPath} className="flex flex-col gap-1">
              <div className="flex items-center justify-between text-xs">
                <span className="font-mono text-text-muted">{row.fieldPath}</span>
                {runs.length > 1 && row.gap > 0 && (
                  <span className="font-mono text-text-muted">gap {row.gap.toFixed(1)}pp</span>
                )}
              </div>
              {row.values.map((value, i) => (
                <div key={i} className="flex items-center gap-2">
                  <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-bg-overlay">
                    <div
                      data-comparison-bar
                      className="h-full origin-left rounded-full"
                      style={{
                        width: `${value ?? 0}%`,
                        background: RUN_COLOR[i % RUN_COLOR.length],
                        transform: "scaleX(0)",
                      }}
                    />
                  </div>
                  <span className="w-10 shrink-0 font-mono text-xs text-text-muted">
                    {value === null ? "—" : `${value.toFixed(0)}%`}
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
      ) : (
        <div className="h-64" />
      )}
    </div>
  );
}

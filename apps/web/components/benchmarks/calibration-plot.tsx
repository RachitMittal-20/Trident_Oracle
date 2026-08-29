"use client";

import { useEffect, useRef } from "react";
import { animate, stagger } from "animejs";

import { DURATION, useReducedMotion, withMotion } from "@/lib/motion";
import { useInView } from "@/lib/use-in-view";
import type { EvalRunDetail } from "@/lib/benchmarks-api";

const SIZE = 320;
const PAD = 36;
const PLOT = SIZE - PAD * 2;
const RUN_COLOR = ["var(--accent)", "var(--signal-warn)"];

function toXY(value: number): number {
  return PAD + value * PLOT;
}

function buildCaption(run: EvalRunDetail): string {
  const buckets = run.calibration.filter((b) => b.n > 0 && b.meanConfidence !== null && b.actualAccuracy !== null);
  if (buckets.length === 0) {
    return "No confidence data reported in this run -- nothing to calibrate.";
  }
  const totalN = buckets.reduce((sum, b) => sum + b.n, 0);
  const weightedGap =
    buckets.reduce((sum, b) => sum + (Number(b.meanConfidence) - Number(b.actualAccuracy)) * b.n, 0) /
    totalN;
  const pct = Math.abs(weightedGap * 100);

  if (pct < 3) {
    return `${run.backend} is well-calibrated on ${run.dataset}: reported confidence tracks actual accuracy within ${pct.toFixed(1)} percentage points on average.`;
  }
  if (weightedGap > 0) {
    return `${run.backend} is overconfident on ${run.dataset} by about ${pct.toFixed(0)} percentage points on average -- reported confidence runs ahead of actual accuracy, which means tolerance_policies thresholds tuned against its confidence score will auto-post more than they should.`;
  }
  return `${run.backend} is underconfident on ${run.dataset} by about ${pct.toFixed(0)} percentage points on average -- it's actually more accurate than its own reported confidence suggests, which costs unnecessary manual review, not incorrect auto-posting.`;
}

function RunCalibration({ run, color, animateIn }: { run: EvalRunDetail; color: string; animateIn: boolean }) {
  const points = run.calibration.filter(
    (b) => b.n > 0 && b.meanConfidence !== null && b.actualAccuracy !== null,
  );
  return (
    <g data-calibration-points={animateIn ? "pending" : undefined}>
      {points.map((bucket) => {
        const x = toXY(Number(bucket.meanConfidence));
        const y = toXY(1 - Number(bucket.actualAccuracy));
        const radius = Math.min(10, 3 + Math.sqrt(bucket.n));
        return (
          <circle
            key={`${bucket.bucketLow}`}
            data-calibration-point
            cx={x}
            cy={y}
            r={radius}
            fill={color}
            fillOpacity={0.85}
            style={{ opacity: animateIn ? 0 : 1 }}
          />
        );
      })}
    </g>
  );
}

export function CalibrationPlot({ runs }: { runs: EvalRunDetail[] }) {
  const [ref, inView] = useInView<HTMLDivElement>();
  const reducedMotion = useReducedMotion();
  const shouldRender = reducedMotion || inView;
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || !shouldRender) return;

    const diagonal = svg.querySelector<SVGPathElement>("[data-calibration-diagonal]");
    const points = svg.querySelectorAll<SVGCircleElement>("[data-calibration-point]");

    if (reducedMotion) {
      if (diagonal) diagonal.style.strokeDashoffset = "0";
      for (const point of points) point.style.opacity = "1";
      return;
    }

    if (diagonal) {
      const length = diagonal.getTotalLength();
      diagonal.style.strokeDasharray = `${length}`;
      diagonal.style.strokeDashoffset = `${length}`;
    }
    for (const point of points) point.style.opacity = "0";

    withMotion(reducedMotion, () =>
      animate(diagonal ? [diagonal] : [], {
        strokeDashoffset: [diagonal?.getTotalLength() ?? 0, 0],
        duration: DURATION.slow,
        ease: "easeInOutQuad",
        complete: () => {
          withMotion(reducedMotion, () =>
            animate(points, {
              opacity: [0, 1],
              duration: DURATION.base,
              delay: stagger(60),
            }),
          );
        },
      }),
    );
  }, [shouldRender, reducedMotion, runs]);

  return (
    <div ref={ref} className="rounded-lg border border-border bg-card p-4">
      <h3 className="mb-3 text-xs font-medium text-text-muted">Confidence calibration</h3>
      {shouldRender ? (
        <>
          <svg ref={svgRef} viewBox={`0 0 ${SIZE} ${SIZE}`} className="w-full max-w-md">
            {/* Under-confident region: actual accuracy above the diagonal --
                the triangle containing the top-left corner (top-left,
                bottom-left, top-right). */}
            <polygon
              points={`${PAD},${PAD} ${PAD},${SIZE - PAD} ${SIZE - PAD},${PAD}`}
              fill="var(--signal-clean)"
              fillOpacity={0.06}
            />
            {/* Over-confident region: actual accuracy below the diagonal --
                the triangle containing the bottom-right corner (bottom-left,
                bottom-right, top-right). */}
            <polygon
              points={`${PAD},${SIZE - PAD} ${SIZE - PAD},${SIZE - PAD} ${SIZE - PAD},${PAD}`}
              fill="var(--signal-block)"
              fillOpacity={0.06}
            />

            <line
              x1={PAD} y1={SIZE - PAD} x2={SIZE - PAD} y2={SIZE - PAD}
              stroke="var(--border)" strokeWidth={1}
            />
            <line x1={PAD} y1={PAD} x2={PAD} y2={SIZE - PAD} stroke="var(--border)" strokeWidth={1} />

            <path
              data-calibration-diagonal
              d={`M ${PAD} ${SIZE - PAD} L ${SIZE - PAD} ${PAD}`}
              fill="none"
              stroke="var(--text-muted)"
              strokeWidth={1.5}
              strokeDasharray="4 4"
            />

            {runs.map((run, i) => (
              <RunCalibration
                key={run.id}
                run={run}
                color={RUN_COLOR[i % RUN_COLOR.length]!}
                animateIn={!reducedMotion}
              />
            ))}

            <text x={SIZE / 2} y={SIZE - 8} textAnchor="middle" fontSize={10} fill="var(--text-muted)">
              Reported confidence
            </text>
            <text
              x={12} y={SIZE / 2} textAnchor="middle" fontSize={10} fill="var(--text-muted)"
              transform={`rotate(-90 12 ${SIZE / 2})`}
            >
              Actual accuracy
            </text>
          </svg>
          <div className="mt-3 flex items-center gap-4 text-xs text-text-muted">
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2 rounded-full" style={{ background: "var(--signal-clean)" }} />
              Under-confident
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2 rounded-full" style={{ background: "var(--signal-block)" }} />
              Over-confident
            </span>
          </div>
          <div className="mt-3 space-y-1">
            {runs.map((run) => (
              <p key={run.id} className="text-sm text-text-primary">
                {buildCaption(run)}
              </p>
            ))}
          </div>
        </>
      ) : (
        <div className="h-80" />
      )}
    </div>
  );
}

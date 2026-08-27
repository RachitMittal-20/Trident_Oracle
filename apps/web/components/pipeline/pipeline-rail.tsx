"use client";

import { useEffect, useRef } from "react";
import { animate } from "animejs";

import { CONTAINER, NODE_POSITION, RAIL_PATH_D, RAIL_SEGMENTS, RAIL_STAGES } from "@/lib/pipeline-layout";
import { useReducedMotion } from "@/lib/motion";

const STAGE_LABEL: Record<(typeof RAIL_STAGES)[number], string> = {
  QUEUED: "Queued",
  EXTRACTING: "Extracting",
  MATCHING: "Matching",
  DECIDED: "Decided",
};

/**
 * The SVG shell only -- the visible rail (drawn in once on mount) plus the
 * four node circles and the invisible node-to-node segments PipelineBoard
 * targets by id (`#segment-...`) with animejs's createMotionPath. Card
 * positioning, travel, and node-pulse-on-arrival all live in PipelineBoard,
 * which reaches into this SVG's DOM by CSS selector rather than through
 * React props -- that's what lets a single orchestrator drive both the
 * SVG nodes and the HTML card/badge overlay through one animation timeline.
 */
export function PipelineRail() {
  const pathRef = useRef<SVGPathElement>(null);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    const pathEl = pathRef.current;
    if (!pathEl) return;
    const length = pathEl.getTotalLength();
    pathEl.style.strokeDasharray = `${length}`;

    if (reducedMotion) {
      pathEl.style.strokeDashoffset = "0";
      return;
    }

    pathEl.style.strokeDashoffset = `${length}`;
    // The one exception to CLAUDE.md's 600ms motion cap: the pipeline
    // rail's one-time draw-in, ~900ms, easeOutExpo.
    const animation = animate(pathEl, {
      strokeDashoffset: [length, 0],
      duration: 900,
      ease: "easeOutExpo",
    });
    return () => {
      animation.revert();
    };
  }, [reducedMotion]);

  return (
    <svg
      className="absolute left-0 top-0"
      width={CONTAINER.width}
      height={CONTAINER.height}
      viewBox={`0 0 ${CONTAINER.width} ${CONTAINER.height}`}
      fill="none"
      aria-hidden="true"
    >
      {RAIL_SEGMENTS.map((segment) => (
        <path key={segment.id} id={segment.id} d={segment.d} fill="none" stroke="none" />
      ))}
      <path ref={pathRef} d={RAIL_PATH_D} className="stroke-border" strokeWidth={2} fill="none" />
      {RAIL_STAGES.map((stage) => {
        const pos = NODE_POSITION[stage];
        return (
          <g key={stage} id={`node-${stage}`} style={{ transformBox: "fill-box", transformOrigin: "center" }}>
            <circle cx={pos.x} cy={pos.y} r={22} className="fill-bg-raised stroke-border" strokeWidth={2} />
            <text
              x={pos.x}
              y={pos.y + 42}
              textAnchor="middle"
              className="fill-text-muted"
              fontSize={11}
              fontWeight={500}
            >
              {STAGE_LABEL[stage]}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

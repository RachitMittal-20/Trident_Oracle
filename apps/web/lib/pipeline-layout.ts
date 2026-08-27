import type { PipelineRailStage } from "@/lib/pipeline-events";

/**
 * Every coordinate below lives in one fixed pixel space -- the rail SVG,
 * its four nodes, the holding column, and the failures tray are all
 * rendered inside a single CONTAINER-sized box (see PipelineBoard). That
 * means a card's on-mount position, its travel along the rail, and its
 * departure to a side panel are all just translateX/translateY within one
 * coordinate system, never a measure-two-DOM-rects handoff between
 * separately-laid-out containers.
 */
export const CONTAINER = { width: 1200, height: 340 } as const;

const RAIL_Y = 120;

export const NODE_X = {
  QUEUED: 100,
  EXTRACTING: 380,
  MATCHING: 660,
  DECIDED: 940,
} as const;

export const RAIL_STAGES: readonly Exclude<PipelineRailStage, "FAILED">[] = [
  "QUEUED",
  "EXTRACTING",
  "MATCHING",
  "DECIDED",
];

export const NODE_POSITION: Record<Exclude<PipelineRailStage, "FAILED">, { x: number; y: number }> = {
  QUEUED: { x: NODE_X.QUEUED, y: RAIL_Y },
  EXTRACTING: { x: NODE_X.EXTRACTING, y: RAIL_Y },
  MATCHING: { x: NODE_X.MATCHING, y: RAIL_Y },
  DECIDED: { x: NODE_X.DECIDED, y: RAIL_Y },
};

/** The full rail, drawn once on mount. A gentle alternating curve -- still
 * reads as one continuous rail, but with enough shape that each
 * inter-node segment below can be targeted individually by animejs's
 * createMotionPath for a card's travel animation. */
export const RAIL_PATH_D =
  `M${NODE_X.QUEUED},${RAIL_Y} ` +
  `C${NODE_X.QUEUED + 80},${RAIL_Y - 40} ${NODE_X.EXTRACTING - 80},${RAIL_Y - 40} ${NODE_X.EXTRACTING},${RAIL_Y} ` +
  `C${NODE_X.EXTRACTING + 80},${RAIL_Y + 40} ${NODE_X.MATCHING - 80},${RAIL_Y + 40} ${NODE_X.MATCHING},${RAIL_Y} ` +
  `C${NODE_X.MATCHING + 80},${RAIL_Y - 40} ${NODE_X.DECIDED - 80},${RAIL_Y - 40} ${NODE_X.DECIDED},${RAIL_Y}`;

/** The same curve, split into the three node-to-node segments a card
 * actually travels one at a time, plus the reverse of each -- rendered
 * invisibly (see PipelineRail) so animejs's createMotionPath can target
 * exactly the segment one transition covers via a CSS id selector. */
export const RAIL_SEGMENTS: { id: string; d: string }[] = [
  {
    id: "segment-queued-extracting",
    d: `M${NODE_X.QUEUED},${RAIL_Y} C${NODE_X.QUEUED + 80},${RAIL_Y - 40} ${NODE_X.EXTRACTING - 80},${RAIL_Y - 40} ${NODE_X.EXTRACTING},${RAIL_Y}`,
  },
  {
    id: "segment-extracting-matching",
    d: `M${NODE_X.EXTRACTING},${RAIL_Y} C${NODE_X.EXTRACTING + 80},${RAIL_Y + 40} ${NODE_X.MATCHING - 80},${RAIL_Y + 40} ${NODE_X.MATCHING},${RAIL_Y}`,
  },
  {
    id: "segment-matching-decided",
    d: `M${NODE_X.MATCHING},${RAIL_Y} C${NODE_X.MATCHING + 80},${RAIL_Y - 40} ${NODE_X.DECIDED - 80},${RAIL_Y - 40} ${NODE_X.DECIDED},${RAIL_Y}`,
  },
  {
    // NEEDS_VERIFICATION sends an invoice from MATCHING back into MATCHING
    // itself (a re-run, per core.state_machine) -- this segment covers the
    // one case a card's stage number decreases, DECIDED -> MATCHING.
    id: "segment-decided-matching",
    d: `M${NODE_X.DECIDED},${RAIL_Y} C${NODE_X.DECIDED - 80},${RAIL_Y - 40} ${NODE_X.MATCHING + 80},${RAIL_Y - 40} ${NODE_X.MATCHING},${RAIL_Y}`,
  },
];

export function segmentIdFor(fromStage: PipelineRailStage, toStage: PipelineRailStage): string | null {
  const key = `segment-${fromStage.toLowerCase()}-${toStage.toLowerCase()}`;
  return RAIL_SEGMENTS.some((segment) => segment.id === key) ? key : null;
}

export function nodePositionForStage(stage: Exclude<PipelineRailStage, "FAILED">): { x: number; y: number } {
  return NODE_POSITION[stage];
}

export const CARD_SIZE = { width: 152, height: 52 } as const;

/** Right-hand panel for PENDING_APPROVAL cards. */
export const HOLDING_COLUMN = { x: 1030, y: 20, width: 150, height: 300 } as const;

/** Bottom strip for EXTRACTION_FAILED cards. */
export const FAILURES_TRAY = { x: 100, y: 260, width: 840, height: 60 } as const;

/** Cards stack downward beneath a node (or across a side panel) so more
 * than one in flight at the same stage don't overlap. */
export function stackedPosition(
  base: { x: number; y: number },
  index: number,
  direction: "down" | "right" = "down",
): { x: number; y: number } {
  const step = 30;
  return direction === "down"
    ? { x: base.x, y: base.y + index * step }
    : { x: base.x + index * (CARD_SIZE.width + 12), y: base.y };
}

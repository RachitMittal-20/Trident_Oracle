"use client";

import { useEffect, useRef, useState } from "react";
import { animate, createTimeline, svg, utils } from "animejs";

import { DURATION, EASING, useReducedMotion } from "@/lib/motion";
import {
  FAILURES_TRAY,
  HOLDING_COLUMN,
  NODE_POSITION,
  RAIL_STAGES,
  segmentIdFor,
  stackedPosition,
  CONTAINER,
} from "@/lib/pipeline-layout";
import type { PipelineCard, PipelineRailStage, PipelineTransition } from "@/lib/pipeline-events";
import { usePipelineStream } from "@/lib/pipeline-events";
import { ConnectionIndicator } from "@/components/pipeline/connection-indicator";
import { EventLog } from "@/components/pipeline/event-log";
import { PipelineCardView } from "@/components/pipeline/pipeline-card-view";
import { PipelineRail } from "@/components/pipeline/pipeline-rail";
import { StageCountBadge } from "@/components/pipeline/stage-count-badge";
import { UploadDropzone } from "@/components/pipeline/upload-dropzone";

const TERMINAL_SETTLE_STATUSES = new Set(["AUTO_POSTED", "POSTED", "APPROVED"]);

function targetSelector(invoiceId: string): string {
  return `[data-invoice-id="${invoiceId}"]`;
}

/** How many cards are already occupying this card's slot (its rail node,
 * or the holding column / failures tray), read straight from the DOM
 * rather than React state -- this only needs to be approximately right,
 * to keep simultaneous cards from stacking exactly on top of each other. */
function occupancyAhead(invoiceId: string, selector: string): number {
  const nodes = Array.from(document.querySelectorAll<HTMLElement>(selector));
  const index = nodes.findIndex((node) => node.dataset.invoiceId === invoiceId);
  return index === -1 ? nodes.length : index;
}

function slotPosition(card: PipelineCard): { x: number; y: number } {
  if (card.status === "PENDING_APPROVAL") {
    const index = occupancyAhead(card.invoiceId, '[data-status="PENDING_APPROVAL"]');
    return stackedPosition({ x: HOLDING_COLUMN.x, y: HOLDING_COLUMN.y }, index, "down");
  }
  if (card.status === "EXTRACTION_FAILED") {
    const index = occupancyAhead(card.invoiceId, '[data-status="EXTRACTION_FAILED"]');
    return stackedPosition({ x: FAILURES_TRAY.x, y: FAILURES_TRAY.y }, index, "right");
  }
  const stage = card.stage === "FAILED" ? "DECIDED" : card.stage;
  const index = occupancyAhead(card.invoiceId, `[data-stage="${stage}"]`);
  return stackedPosition(NODE_POSITION[stage], index, "down");
}

export interface PipelineBoardProps {
  tenantId: string;
  apiBaseUrl: string;
}

export function PipelineBoard({ tenantId, apiBaseUrl }: PipelineBoardProps) {
  const { cards, transitions, log, connectionState } = usePipelineStream(tenantId, apiBaseUrl);
  const reducedMotion = useReducedMotion();
  const [visibleIds, setVisibleIds] = useState<Set<string>>(new Set());
  const seenIdsRef = useRef<Set<string>>(new Set());
  const lastProcessedSeqRef = useRef(0);

  const visibleCards = Object.values(cards).filter((card) => visibleIds.has(card.invoiceId));

  // New invoices (from the snapshot, or a fresh "queued" event with no
  // fromStatus) materialize at their slot: a fade + scale entrance,
  // transform/opacity only.
  useEffect(() => {
    for (const card of Object.values(cards)) {
      if (seenIdsRef.current.has(card.invoiceId)) continue;
      seenIdsRef.current.add(card.invoiceId);
      setVisibleIds((prev) => new Set(prev).add(card.invoiceId));

      requestAnimationFrame(() => {
        const el = document.querySelector<HTMLElement>(targetSelector(card.invoiceId));
        if (!el) return;
        const pos = slotPosition(card);
        if (reducedMotion) {
          el.style.transform = `translate(${pos.x}px, ${pos.y}px) scale(1)`;
          el.style.opacity = "1";
          return;
        }
        animate(el, {
          translateX: [pos.x, pos.x],
          translateY: [pos.y, pos.y],
          scale: [0.85, 1],
          opacity: [0, 1],
          duration: DURATION.base,
          ease: EASING.entrance,
        });
      });
    }
  }, [cards, reducedMotion]);

  // Every new transition drives exactly one animation sequence: travel
  // along the rail (or to a side panel), a node pulse on arrival, and --
  // for a terminal outcome -- the settle/flash/shake treatment, ending in
  // removal from `visibleIds`.
  useEffect(() => {
    const fresh = transitions.filter((transition) => transition.seq > lastProcessedSeqRef.current);
    if (fresh.length === 0) return;
    lastProcessedSeqRef.current = transitions[transitions.length - 1]?.seq ?? lastProcessedSeqRef.current;

    for (const transition of fresh) {
      if (transition.fromStatus === null) continue; // handled by the entrance effect above
      runTransition(transition, reducedMotion, () => {
        setVisibleIds((prev) => {
          const next = new Set(prev);
          next.delete(transition.invoiceId);
          return next;
        });
      });
    }
  }, [transitions, reducedMotion]);

  const countsByStage: Record<(typeof RAIL_STAGES)[number], number> = {
    QUEUED: 0,
    EXTRACTING: 0,
    MATCHING: 0,
    DECIDED: 0,
  };
  for (const card of visibleCards) {
    if (card.stage !== "FAILED") {
      countsByStage[card.stage] += 1;
    }
  }

  const holdingCards = visibleCards.filter((card) => card.status === "PENDING_APPROVAL");
  const failedCards = visibleCards.filter((card) => card.status === "EXTRACTION_FAILED");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <ConnectionIndicator state={connectionState} />
        <UploadDropzone tenantId={tenantId} apiBaseUrl={apiBaseUrl} />
      </div>

      <div className="relative" style={{ width: CONTAINER.width, height: CONTAINER.height }}>
        <PipelineRail />

        {RAIL_STAGES.map((stage) => (
          <StageCountBadge key={stage} stage={stage} count={countsByStage[stage]} />
        ))}

        <div
          className="absolute rounded-lg border border-dashed border-border/60"
          style={{
            left: HOLDING_COLUMN.x - 10,
            top: HOLDING_COLUMN.y - 10,
            width: HOLDING_COLUMN.width,
            height: HOLDING_COLUMN.height,
          }}
        >
          <span className="absolute -top-6 left-0 text-xs font-medium text-text-muted">
            Pending approval
          </span>
          {holdingCards.length === 0 && (
            <span className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-xs text-text-muted">
              —
            </span>
          )}
        </div>
        <div
          className="absolute rounded-lg border border-dashed border-border/60"
          style={{
            left: FAILURES_TRAY.x - 10,
            top: FAILURES_TRAY.y - 10,
            width: FAILURES_TRAY.width,
            height: FAILURES_TRAY.height,
          }}
        >
          <span className="absolute -top-6 left-0 text-xs font-medium text-text-muted">
            Extraction failures
          </span>
          {failedCards.length === 0 && (
            <span className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-xs text-text-muted">
              —
            </span>
          )}
        </div>

        {visibleCards.map((card) => (
          <PipelineCardView key={card.invoiceId} card={card} />
        ))}
      </div>

      <EventLog entries={log} />
    </div>
  );
}

function runTransition(transition: PipelineTransition, reducedMotion: boolean, onRemoved: () => void): void {
  const el = document.querySelector<HTMLElement>(targetSelector(transition.invoiceId));
  if (!el) return;

  const isTerminalSettle = TERMINAL_SETTLE_STATUSES.has(transition.toStatus);
  const isRejected = transition.toStatus === "REJECTED";
  const isFailed = transition.toStatus === "EXTRACTION_FAILED";
  const isHolding = transition.toStatus === "PENDING_APPROVAL";
  const card = transition.card;

  const holdingPos = stackedPosition(
    { x: HOLDING_COLUMN.x, y: HOLDING_COLUMN.y },
    occupancyAhead(transition.invoiceId, '[data-status="PENDING_APPROVAL"]'),
    "down",
  );
  const failTrayPos = stackedPosition(
    { x: FAILURES_TRAY.x, y: FAILURES_TRAY.y },
    occupancyAhead(transition.invoiceId, '[data-status="EXTRACTION_FAILED"]'),
    "right",
  );
  const decidedPos = NODE_POSITION.DECIDED;
  const fallbackPos = card ? slotPosition(card) : decidedPos;

  if (reducedMotion) {
    const pos = isHolding ? holdingPos : isFailed ? failTrayPos : fallbackPos;
    el.style.transform = `translate(${pos.x}px, ${pos.y}px) scale(1)`;
    if (isTerminalSettle || isRejected) {
      el.style.opacity = "0";
      onRemoved();
    }
    return;
  }

  if (isFailed) {
    createTimeline()
      .add(el, {
        translateX: ["+=6", "-=6", "+=6", "-=6"],
        duration: 240,
        ease: EASING.stateChange,
      })
      .add(el, {
        translateX: failTrayPos.x,
        translateY: failTrayPos.y,
        duration: 420,
        ease: EASING.stateChange,
      });
    return;
  }

  if (isHolding) {
    animate(el, {
      translateX: holdingPos.x,
      translateY: holdingPos.y,
      duration: 420,
      ease: EASING.stateChange,
    });
    return;
  }

  if (isRejected) {
    const flashEl = el.querySelector<HTMLElement>("[data-flash]");
    const timeline = createTimeline();
    if (flashEl) {
      timeline.add(flashEl, { opacity: [0, 0.7, 0], duration: 260, ease: EASING.stateChange });
    }
    timeline.add(
      el,
      { translateY: "+=40", opacity: [1, 0], duration: 320, ease: EASING.stateChange },
      flashEl ? "-=100" : 0,
    );
    timeline.then(() => onRemoved());
    return;
  }

  // On-rail move (forward or backward): follow the curve when a drawn
  // segment covers this exact hop, otherwise fall back to a plain tween
  // (e.g. a stacking-index reshuffle within the same stage).
  const fromStage = transition.fromStatus ? stageForStatusFallback(transition.fromStatus) : null;
  const toStage: PipelineRailStage = transition.stage === "FAILED" ? "DECIDED" : transition.stage;
  const segmentId = fromStage ? segmentIdFor(fromStage, toStage) : null;
  const segmentPath = segmentId ? utils.$(`#${segmentId}`)[0] : undefined;

  const afterArrival = () => {
    pulseNode(transition.stage);
    if (!isTerminalSettle) return;
    const settleEl = el.querySelector<HTMLElement>("[data-settle]");
    const timeline = createTimeline();
    if (settleEl) {
      timeline.add(settleEl, { opacity: [0, 0.5, 0], duration: 320, ease: EASING.stateChange });
    }
    timeline.add(el, { translateY: "+=40", opacity: [1, 0], duration: 320, ease: EASING.stateChange }, "+=200");
    timeline.then(() => onRemoved());
  };

  if (segmentPath) {
    const motion = svg.createMotionPath(`#${segmentId}`);
    animate(el, {
      translateX: motion.translateX,
      translateY: motion.translateY,
      duration: 420,
      ease: EASING.stateChange,
    }).then(afterArrival);
    return;
  }

  animate(el, {
    translateX: fallbackPos.x,
    translateY: fallbackPos.y,
    duration: 420,
    ease: EASING.stateChange,
  }).then(afterArrival);
}

function stageForStatusFallback(status: string): PipelineRailStage {
  // Only used to pick a motion-path segment id -- a coarse remap, not the
  // authoritative one (that's packages/core/core/pipeline_stage.py,
  // mirrored server-side; the event's own `stage` field is authoritative
  // for where a card actually ends up).
  if (status === "RECEIVED") return "QUEUED";
  if (status === "EXTRACTING") return "EXTRACTING";
  if (status === "EXTRACTED" || status === "MATCHING") return "MATCHING";
  if (status === "EXTRACTION_FAILED") return "FAILED";
  return "DECIDED";
}

function pulseNode(stage: PipelineRailStage): void {
  if (stage === "FAILED") return;
  const node = utils.$(`#node-${stage} circle`)[0];
  if (!node) return;
  animate(node, {
    scale: [1, 1.15, 1],
    duration: DURATION.base,
    ease: EASING.stateChange,
  });
}

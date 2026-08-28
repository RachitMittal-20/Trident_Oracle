"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createTimeline, stagger, utils } from "animejs";

import { ConnectionIndicator } from "@/components/pipeline/connection-indicator";
import { DecisionFooter } from "@/components/match/decision-footer";
import { ExceptionCard } from "@/components/match/exception-card";
import { LineRow } from "@/components/match/line-row";
import { MatchMethodsPanel } from "@/components/match/match-methods-panel";
import { SummaryStrip } from "@/components/match/summary-strip";
import { ErrorState } from "@/components/states";
import { DURATION, EASING, useReducedMotion } from "@/lib/motion";
import { exceptionForInvoiceLine, outcomeForInvoiceLine, type LineOutcome } from "@/lib/match-layout";
import {
  decideInvoiceMatch,
  fetchMatchView,
  MatchApiError,
  type MatchView,
} from "@/lib/match-api";

export interface MatchBoardProps {
  invoiceId: string;
  tenantId: string;
  actorUserId: string;
  apiBaseUrl: string;
}

interface ConnectorSpec {
  id: string;
  d: string;
  tone: LineOutcome;
  poLineId: string | null;
}

function outcomeStroke(outcome: LineOutcome): string {
  switch (outcome) {
    case "clean":
      return "stroke-signal-clean";
    case "variance":
      return "stroke-signal-warn";
    case "block":
      return "stroke-signal-block";
    default:
      return "stroke-signal-clean";
  }
}

function deltaFieldForException(exceptionType: string): "qty" | "unitPrice" | null {
  if (exceptionType === "QTY_OVER" || exceptionType === "QTY_SHORT") return "qty";
  if (exceptionType === "PRICE_VARIANCE") return "unitPrice";
  return null;
}

export function MatchBoard({ invoiceId, tenantId, actorUserId, apiBaseUrl }: MatchBoardProps) {
  const [view, setView] = useState<MatchView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hoveredExceptionId, setHoveredExceptionId] = useState<string | null>(null);
  const [deciding, setDeciding] = useState(false);
  const [decidedStatus, setDecidedStatus] = useState<"APPROVED" | "REJECTED" | null>(null);
  // Set once this actor's own decision has been recorded, independent of
  // whether the invoice itself is fully settled yet -- a dual-approval
  // invoice can still be PENDING_APPROVAL after this caller approves
  // (apps/api/api/match_view.py::decide_invoice waits for every required
  // approver), but this caller has nothing further to do either way.
  const [myApproval, setMyApproval] = useState<{ received: number; required: number } | null>(
    null,
  );
  const [connectors, setConnectors] = useState<ConnectorSpec[]>([]);
  const boardRef = useRef<HTMLDivElement>(null);
  const animatedRef = useRef(false);
  const reducedMotion = useReducedMotion();

  const load = useCallback(() => {
    setError(null);
    fetchMatchView(apiBaseUrl, invoiceId, tenantId)
      .then(setView)
      .catch((err: unknown) => {
        setError(err instanceof MatchApiError ? err.message : "Could not load this invoice's match.");
      });
  }, [apiBaseUrl, invoiceId, tenantId]);

  useEffect(() => {
    load();
  }, [load]);

  const getRow = (key: string): HTMLElement | null =>
    boardRef.current?.querySelector<HTMLElement>(`[data-row-key="${key}"]`) ?? null;

  // Measure row positions once the real layout exists, build connector
  // paths from them, then run the single rows-then-connectors timeline.
  useEffect(() => {
    if (!view || animatedRef.current) return;
    const boardEl = boardRef.current;
    if (!boardEl) return;
    animatedRef.current = true;

    const containerRect = boardEl.getBoundingClientRect();
    const relRect = (el: HTMLElement) => {
      const rect = el.getBoundingClientRect();
      return {
        left: rect.left - containerRect.left,
        right: rect.right - containerRect.left,
        midY: rect.top - containerRect.top + rect.height / 2,
      };
    };

    const specs: ConnectorSpec[] = [];
    for (const poLine of view.poLines) {
      const orderedEl = getRow(`ordered-${poLine.id}`);
      const receivedEl = getRow(`received-${poLine.id}`);
      if (!orderedEl || !receivedEl) continue;
      const ordered = relRect(orderedEl);
      const received = relRect(receivedEl);
      specs.push({
        id: `seg-a-${poLine.id}`,
        d: `M${ordered.right},${ordered.midY} L${received.left},${received.midY}`,
        tone: "clean",
        poLineId: poLine.id,
      });
    }
    for (const line of view.invoiceLines) {
      if (!line.matchedPoLineId) continue;
      const receivedEl = getRow(`received-${line.matchedPoLineId}`);
      const invoicedEl = getRow(`invoiced-${line.id}`);
      if (!receivedEl || !invoicedEl) continue;
      const received = relRect(receivedEl);
      const invoiced = relRect(invoicedEl);
      const midX = (received.right + invoiced.left) / 2;
      const outcome = outcomeForInvoiceLine(line, view.exceptions);
      specs.push({
        id: `seg-b-${line.id}`,
        d: `M${received.right},${received.midY} C${midX},${received.midY} ${midX},${invoiced.midY} ${invoiced.left},${invoiced.midY}`,
        tone: outcome,
        poLineId: line.matchedPoLineId,
      });
      // The ordered<->received segment for this line's po_line shares the
      // same outcome -- a matched line reads as one continuous thread.
      const segA = specs.find((s) => s.id === `seg-a-${line.matchedPoLineId}`);
      if (segA) segA.tone = outcome;
    }
    setConnectors(specs);

    requestAnimationFrame(() => {
      runEntranceTimeline(view, reducedMotion);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once when view first arrives
  }, [view]);

  if (error) {
    return (
      <div className="p-10">
        <ErrorState description={error} onRetry={load} />
      </div>
    );
  }

  if (!view) {
    return <div className="p-10 text-sm text-text-muted">Loading…</div>;
  }

  const orderedTotal = view.poLines
    .reduce((sum, line) => sum + Number(line.lineTotal ?? 0), 0)
    .toFixed(2);
  const receivedTotal = view.poLines
    .reduce((sum, line) => sum + Number(line.qtyReceived ?? 0) * Number(line.unitPrice ?? 0), 0)
    .toFixed(2);
  const invoicedTotal = view.invoiceLines
    .reduce((sum, line) => sum + Number(line.lineTotal ?? 0), 0)
    .toFixed(2);

  const canDecide =
    view.invoice.status === "PENDING_APPROVAL" && decidedStatus === null && myApproval === null;

  // Hovering an exception card (or its own row) highlights the matching
  // row in all three columns, plus its connector, and dims everything
  // else -- the cross-highlight is derived from one hovered id so all
  // four surfaces (ordered row, received row, invoiced row, connector)
  // agree on what's "related" without four separate pieces of state.
  const hoveredException = view.exceptions.find((exc) => exc.id === hoveredExceptionId) ?? null;
  const hoveredPoLineId = hoveredException?.poLineId ?? null;
  const hoveredInvoiceLineId = hoveredException?.invoiceLineId ?? null;

  const handleDecide = async (decision: "approved" | "rejected") => {
    setDeciding(true);
    try {
      const result = await decideInvoiceMatch(apiBaseUrl, invoiceId, tenantId, decision, actorUserId);
      setMyApproval({ received: result.approvalsReceived, required: result.approvalsRequired });
      if (result.status === "approved") {
        runApprovalTimeline(reducedMotion, () => {
          setDecidedStatus("APPROVED");
          setDeciding(false);
        });
      } else if (result.status === "rejected") {
        setDecidedStatus("REJECTED");
        setDeciding(false);
      } else {
        // "pending": this caller's own approval was recorded, but the
        // invoice needs more -- it has not transitioned, so decidedStatus
        // stays null and the footer still reads PENDING_APPROVAL.
        setDeciding(false);
      }
    } catch (err) {
      setDeciding(false);
      setError(err instanceof MatchApiError ? err.message : "Could not record that decision.");
    }
  };

  return (
    <div className="flex flex-col gap-6 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-text-primary">
          {view.invoice.invoiceNumber ?? view.invoice.id} — match
        </h1>
        <ConnectionIndicator state="open" />
      </div>

      <SummaryStrip
        ordered={orderedTotal}
        received={receivedTotal}
        invoiced={invoicedTotal}
        currency={view.invoice.currency}
      />

      <div className="flex gap-6">
        <div className="flex-1">
          <div ref={boardRef} className="relative grid grid-cols-3 gap-20">
            <svg className="pointer-events-none absolute inset-0 h-full w-full overflow-visible">
              {connectors.map((connector) => {
                const isRelated =
                  connector.poLineId !== null && connector.poLineId === hoveredPoLineId;
                const isDimmed = hoveredExceptionId !== null && !isRelated;
                const baseOpacity = connector.tone === "clean" ? 0.4 : 1;
                return (
                  <path
                    key={connector.id}
                    id={connector.id}
                    data-connector={connector.id}
                    data-po-line-id={connector.poLineId ?? undefined}
                    d={connector.d}
                    fill="none"
                    strokeWidth={2}
                    className={outcomeStroke(connector.tone)}
                    style={{
                      opacity: isDimmed ? 0.15 : isRelated ? 1 : baseOpacity,
                      transition: "opacity 120ms ease-in-out, stroke 200ms ease-in-out",
                    }}
                  />
                );
              })}
            </svg>

            <div className="flex flex-col gap-2">
              <h2 className="px-1 text-xs font-semibold uppercase tracking-wide text-text-muted">
                Ordered (PO {view.po?.poNumber ?? "—"})
              </h2>
              {view.poLines.map((line) => {
                const isHighlighted = hoveredPoLineId !== null && hoveredPoLineId === line.id;
                const isDimmed = hoveredPoLineId !== null && hoveredPoLineId !== line.id;
                return (
                  <LineRow
                    key={line.id}
                    rowKey={`ordered-${line.id}`}
                    description={line.description}
                    qty={line.qtyOrdered ?? "0"}
                    unitPrice={line.unitPrice ?? "0"}
                    lineTotal={line.lineTotal ?? "0"}
                    currency={view.invoice.currency}
                    isHighlighted={isHighlighted}
                    isDimmed={isDimmed}
                  />
                );
              })}
            </div>

            <div className="flex flex-col gap-2">
              <h2 className="px-1 text-xs font-semibold uppercase tracking-wide text-text-muted">
                Received (GRN)
              </h2>
              {view.poLines.map((line) => {
                const isHighlighted = hoveredPoLineId !== null && hoveredPoLineId === line.id;
                const isDimmed = hoveredPoLineId !== null && hoveredPoLineId !== line.id;
                return (
                  <LineRow
                    key={line.id}
                    rowKey={`received-${line.id}`}
                    description={line.description}
                    qty={line.qtyReceived ?? "0"}
                    unitPrice={line.unitPrice ?? "0"}
                    lineTotal={(Number(line.qtyReceived ?? 0) * Number(line.unitPrice ?? 0)).toFixed(2)}
                    currency={view.invoice.currency}
                    isHighlighted={isHighlighted}
                    isDimmed={isDimmed}
                  />
                );
              })}
            </div>

            <div className="flex flex-col gap-2">
              <h2 className="px-1 text-xs font-semibold uppercase tracking-wide text-text-muted">
                Invoiced
              </h2>
              {view.invoiceLines.map((line) => {
                const outcome = outcomeForInvoiceLine(line, view.exceptions);
                const exception = exceptionForInvoiceLine(line, view.exceptions);
                const relatedExceptionId = exception?.id ?? null;
                const isHighlighted = hoveredInvoiceLineId !== null && hoveredInvoiceLineId === line.id;
                const isDimmed = hoveredExceptionId !== null && hoveredInvoiceLineId !== line.id;
                return (
                  <LineRow
                    key={line.id}
                    rowKey={`invoiced-${line.id}`}
                    description={line.description}
                    qty={line.qty ?? "0"}
                    unitPrice={line.unitPrice ?? "0"}
                    lineTotal={line.lineTotal ?? "0"}
                    currency={view.invoice.currency}
                    outcome={outcome}
                    deltaField={exception ? deltaFieldForException(exception.exceptionType) : null}
                    deltaTone={exception?.severity === "block" ? "block" : "warn"}
                    isHighlighted={isHighlighted}
                    isDimmed={isDimmed}
                    onMouseEnter={() => relatedExceptionId && setHoveredExceptionId(relatedExceptionId)}
                    onMouseLeave={() => setHoveredExceptionId(null)}
                  />
                );
              })}
            </div>
          </div>

          <div className="mt-6">
            <MatchMethodsPanel lines={view.invoiceLines} />
          </div>
        </div>

        <div data-exception-rail className="flex w-72 shrink-0 flex-col gap-2">
          <h2 className="px-1 text-xs font-semibold uppercase tracking-wide text-text-muted">
            Exceptions
          </h2>
          {view.exceptions.filter((exc) => exc.status === "open").length === 0 && (
            <p className="px-1 text-xs text-text-muted">No open exceptions.</p>
          )}
          {view.exceptions
            .filter((exc) => exc.status === "open")
            .map((exception) => (
              <ExceptionCard
                key={exception.id}
                exception={exception}
                currency={view.invoice.currency}
                isHovered={hoveredExceptionId === exception.id}
                onHoverChange={setHoveredExceptionId}
              />
            ))}
        </div>
      </div>

      <DecisionFooter
        status={decidedStatus ?? view.invoice.status}
        result={view.matchRun?.result ?? null}
        reason={view.matchRun?.reason ?? null}
        canDecide={canDecide}
        deciding={deciding}
        pendingApproval={decidedStatus === null ? myApproval : null}
        onApprove={() => void handleDecide("approved")}
        onReject={() => void handleDecide("rejected")}
      />
    </div>
  );
}

function runEntranceTimeline(view: MatchView, reducedMotion: boolean): void {
  const allRows = utils.$("[data-row-key]");
  const unmatchedRows = utils.$('[data-outcome="unmatched"]');
  const normalRows = allRows.filter((el) => !unmatchedRows.includes(el));
  const connectorPaths = utils.$("[data-connector]") as unknown as SVGPathElement[];
  const blockRows = utils.$('[data-outcome="block"]');

  if (reducedMotion) {
    for (const el of allRows) (el as HTMLElement).style.opacity = "1";
    for (const path of connectorPaths) path.style.strokeDashoffset = "0";
    for (const line of view.invoiceLines) {
      const exception = exceptionForInvoiceLine(line, view.exceptions);
      if (!exception) continue;
      const badge = document.querySelector(`[data-delta-badge="invoiced-${line.id}"]`);
      if (badge && exception.delta !== null) {
        badge.textContent = `${Number(exception.delta) > 0 ? "+" : ""}${exception.delta}`;
      }
    }
    return;
  }

  connectorPaths.forEach((path) => {
    const length = path.getTotalLength();
    path.style.strokeDasharray = `${length}`;
    path.style.strokeDashoffset = `${length}`;
  });

  const timeline = createTimeline();
  if (normalRows.length > 0) {
    timeline.add(
      normalRows,
      {
        opacity: [0, 1],
        translateY: [8, 0],
        duration: DURATION.base,
        ease: EASING.entrance,
        delay: stagger(25),
      },
      0,
    );
  }
  if (unmatchedRows.length > 0) {
    timeline.add(
      unmatchedRows,
      {
        opacity: [0, 1],
        translateX: [24, 0],
        duration: DURATION.base,
        ease: EASING.entrance,
        delay: stagger(25),
      },
      0,
    );
  }
  if (connectorPaths.length > 0) {
    timeline.add(
      connectorPaths,
      {
        strokeDashoffset: 0,
        duration: 260,
        ease: "easeOutExpo",
        delay: stagger(45),
      },
      "+=100",
    );
  }
  if (blockRows.length > 0) {
    timeline.add(
      blockRows,
      {
        translateX: ["+=6", "-=6", "+=6", "-=6"],
        duration: 240,
        ease: EASING.stateChange,
      },
      "<",
    );
  }

  for (const line of view.invoiceLines) {
    const exception = exceptionForInvoiceLine(line, view.exceptions);
    if (!exception || exception.delta === null) continue;
    const badge = document.querySelector(`[data-delta-badge="invoiced-${line.id}"]`);
    if (!badge) continue;
    const target = Number(exception.delta);
    const counter = { current: 0 };
    timeline.add(
      counter,
      {
        current: target,
        duration: DURATION.base,
        ease: EASING.entrance,
        onUpdate: () => {
          badge.textContent = `${target > 0 ? "+" : ""}${counter.current.toFixed(2)}`;
        },
      },
      "<",
    );
  }
}

function runApprovalTimeline(reducedMotion: boolean, onDone: () => void): void {
  const connectorPaths = utils.$("[data-connector]");
  const cards = utils.$("[data-exception-id]");

  if (reducedMotion) {
    for (const path of connectorPaths) {
      (path as SVGPathElement).classList.add("stroke-signal-clean");
    }
    onDone();
    return;
  }

  const timeline = createTimeline();
  if (cards.length > 0) {
    timeline.add(
      cards,
      {
        opacity: [1, 0],
        scale: [1, 0.92],
        duration: DURATION.base,
        ease: EASING.stateChange,
        delay: stagger(35),
      },
      0,
    );
  }
  for (const path of connectorPaths) {
    (path as SVGPathElement).classList.remove("stroke-signal-warn", "stroke-signal-block");
    (path as SVGPathElement).classList.add("stroke-signal-clean");
  }
  timeline.add(
    connectorPaths,
    { opacity: [0.3, 0.4], duration: DURATION.fast, ease: EASING.stateChange },
    0,
  );
  timeline.then(() => onDone());
}

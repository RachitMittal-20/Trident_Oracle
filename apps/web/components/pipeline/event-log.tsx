"use client";

import { useEffect, useRef } from "react";
import { animate, stagger } from "animejs";

import { DURATION, EASING, useReducedMotion } from "@/lib/motion";
import type { PipelineLogEntry } from "@/lib/pipeline-events";

export interface EventLogProps {
  entries: PipelineLogEntry[];
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleTimeString("en-US", { hour12: false });
}

/** Newest first, entering with a staggered fade (35ms) -- only the rows
 * that are actually new since the last render animate in; already-settled
 * rows never replay. */
export function EventLog({ entries }: EventLogProps) {
  const listRef = useRef<HTMLUListElement>(null);
  const seenSeqsRef = useRef<Set<number>>(new Set());
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    const freshSeqs = entries.map((entry) => entry.seq).filter((seq) => !seenSeqsRef.current.has(seq));
    for (const seq of freshSeqs) {
      seenSeqsRef.current.add(seq);
    }
    if (freshSeqs.length === 0 || !listRef.current) return;

    const selector = freshSeqs.map((seq) => `[data-seq="${seq}"]`).join(",");
    const rows = listRef.current.querySelectorAll<HTMLElement>(selector);
    if (rows.length === 0) return;

    if (reducedMotion) {
      rows.forEach((row) => {
        row.style.opacity = "1";
      });
      return;
    }
    animate(rows, {
      opacity: [0, 1],
      translateY: [-4, 0],
      delay: stagger(35),
      duration: DURATION.fast,
      ease: EASING.entrance,
    });
  }, [entries, reducedMotion]);

  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="border-b border-border px-4 py-2 text-xs font-medium text-text-muted">
        Event log
      </div>
      <ul ref={listRef} className="max-h-64 overflow-y-auto p-2 font-mono text-xs">
        {entries.length === 0 && <li className="px-2 py-6 text-center text-text-muted">No events yet</li>}
        {entries.map((entry) => (
          <li
            key={entry.seq}
            data-seq={entry.seq}
            className="flex items-baseline gap-3 rounded px-2 py-1 opacity-0 tabular-nums"
          >
            <span className="shrink-0 text-text-muted">{formatTimestamp(entry.timestamp)}</span>
            <span className="truncate text-text-primary">{entry.message}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

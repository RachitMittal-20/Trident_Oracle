"use client";

import { useEffect, useRef, useState } from "react";
import type { InvoiceStatus } from "@/components/status-pill";

/** Mirrors packages/core/core/pipeline_stage.py::PipelineStage exactly. */
export type PipelineRailStage = "QUEUED" | "EXTRACTING" | "MATCHING" | "DECIDED" | "FAILED";

export interface PipelineCard {
  invoiceId: string;
  invoiceNumber: string | null;
  amount: string | null;
  currency: string;
  status: InvoiceStatus;
  stage: PipelineRailStage;
  vendorName: string | null;
  createdAt: string;
  updatedAt: string;
}

/** One invoice_event from GET /v1/events/stream, camelCased. */
export interface PipelineTransition {
  /** Monotonic, client-assigned -- lets effects tell "already animated this
   * one" apart from "new since last render" without relying on object identity. */
  seq: number;
  invoiceId: string;
  fromStatus: InvoiceStatus | null;
  toStatus: InvoiceStatus;
  stage: PipelineRailStage;
  occurredAt: string;
  card: PipelineCard | null;
}

export interface PipelineLogEntry {
  seq: number;
  timestamp: string;
  message: string;
}

export type ConnectionState = "connecting" | "open" | "reconnecting";

interface WireCard {
  invoice_id: string;
  invoice_number: string | null;
  amount: string | null;
  currency: string;
  status: InvoiceStatus;
  stage: PipelineRailStage;
  vendor_name: string | null;
  created_at: string;
  updated_at: string;
}

interface WireSnapshot {
  invoices: WireCard[];
}

interface WireInvoiceEvent {
  invoice_id: string;
  from_status: InvoiceStatus | null;
  to_status: InvoiceStatus;
  stage: PipelineRailStage;
  occurred_at: string;
  card: WireCard | null;
}

function toCard(wire: WireCard): PipelineCard {
  return {
    invoiceId: wire.invoice_id,
    invoiceNumber: wire.invoice_number,
    amount: wire.amount,
    currency: wire.currency,
    status: wire.status,
    stage: wire.stage,
    vendorName: wire.vendor_name,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
  };
}

function describeTransition(event: WireInvoiceEvent): string {
  const shortId = event.invoice_id.slice(0, 8);
  if (event.from_status === null) {
    return `${shortId}  ·  queued`;
  }
  return `${shortId}  ·  ${event.from_status} -> ${event.to_status}`;
}

export interface PipelineStreamState {
  cards: Record<string, PipelineCard>;
  transitions: PipelineTransition[];
  log: PipelineLogEntry[];
  connectionState: ConnectionState;
}

const MAX_LOG_ENTRIES = 200;
const MAX_TRANSITIONS = 200;

/**
 * Owns the one real EventSource connection to GET /v1/events/stream for
 * this tenant. There is no simulated or looping fallback here -- if the
 * connection is down, `connectionState` reports "reconnecting" and nothing
 * animates until the browser's native EventSource retry succeeds and a
 * fresh snapshot arrives.
 */
export function usePipelineStream(tenantId: string, apiBaseUrl: string): PipelineStreamState {
  const [cards, setCards] = useState<Record<string, PipelineCard>>({});
  const [transitions, setTransitions] = useState<PipelineTransition[]>([]);
  const [log, setLog] = useState<PipelineLogEntry[]>([]);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const seqRef = useRef(0);

  useEffect(() => {
    const url = `${apiBaseUrl}/v1/events/stream?tenant_id=${encodeURIComponent(tenantId)}`;
    const source = new EventSource(url);
    let hasOpenedOnce = false;

    source.onopen = () => {
      hasOpenedOnce = true;
      setConnectionState("open");
    };
    source.onerror = () => {
      setConnectionState(hasOpenedOnce ? "reconnecting" : "connecting");
    };

    source.addEventListener("snapshot", (event) => {
      const data = JSON.parse((event as MessageEvent).data) as WireSnapshot;
      const next: Record<string, PipelineCard> = {};
      for (const wireCard of data.invoices) {
        next[wireCard.invoice_id] = toCard(wireCard);
      }
      setCards(next);
    });

    source.addEventListener("invoice_event", (event) => {
      const raw = JSON.parse((event as MessageEvent).data) as WireInvoiceEvent;
      seqRef.current += 1;
      const seq = seqRef.current;

      if (raw.card !== null) {
        const card = toCard(raw.card);
        setCards((prev) => ({ ...prev, [raw.invoice_id]: card }));
      }
      setTransitions((prev) => [
        ...prev.slice(-(MAX_TRANSITIONS - 1)),
        {
          seq,
          invoiceId: raw.invoice_id,
          fromStatus: raw.from_status,
          toStatus: raw.to_status,
          stage: raw.stage,
          occurredAt: raw.occurred_at,
          card: raw.card ? toCard(raw.card) : null,
        },
      ]);
      setLog((prev) => [
        { seq, timestamp: raw.occurred_at, message: describeTransition(raw) },
        ...prev.slice(0, MAX_LOG_ENTRIES - 1),
      ]);
    });

    return () => {
      source.close();
    };
  }, [tenantId, apiBaseUrl]);

  return { cards, transitions, log, connectionState };
}

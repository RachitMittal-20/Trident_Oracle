"use client";

import { useState } from "react";
import { ChevronDownIcon } from "lucide-react";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ConfidenceBar } from "@/components/confidence-bar";
import { cn } from "@/lib/utils";
import type { MatchInvoiceLine } from "@/lib/match-api";

const METHOD_LABEL: Record<string, string> = {
  sku: "SKU (exact)",
  fuzzy: "Fuzzy (description)",
  llm: "LLM fallback",
  unmatched: "Unmatched",
};

export interface MatchMethodsPanelProps {
  lines: MatchInvoiceLine[];
}

export function MatchMethodsPanel({ lines }: MatchMethodsPanelProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-lg border border-border bg-card">
      <button
        type="button"
        className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm font-medium text-text-primary"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
      >
        How this was matched
        <ChevronDownIcon className={cn("size-4 text-text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="border-t border-border px-4 py-3">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Invoice line</TableHead>
                <TableHead>Method</TableHead>
                <TableHead>Confidence</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {lines.map((line) => (
                <TableRow key={line.id}>
                  <TableCell>{line.description}</TableCell>
                  <TableCell>{METHOD_LABEL[line.matchMethod ?? ""] ?? line.matchMethod ?? "—"}</TableCell>
                  <TableCell>
                    {line.matchConfidence !== null ? (
                      <div className="flex items-center gap-2">
                        <ConfidenceBar value={Number(line.matchConfidence)} className="max-w-20" />
                        <span className="font-mono text-xs tabular-nums text-text-muted">
                          {(Number(line.matchConfidence) * 100).toFixed(0)}%
                        </span>
                      </div>
                    ) : (
                      <span className="text-text-muted">—</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

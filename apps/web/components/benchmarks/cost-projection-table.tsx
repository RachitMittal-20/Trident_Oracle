"use client";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { EvalRunDetail } from "@/lib/benchmarks-api";

function paidTierNote(backend: string): string {
  if (backend === "gemini") return "Gemini Flash published per-token rate";
  if (backend === "tesseract") return "local OCR -- no API, no tier";
  return "no external API cost";
}

export function CostProjectionTable({ runs }: { runs: EvalRunDetail[] }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <h3 className="mb-3 text-xs font-medium text-text-muted">
        Cost per 1,000 invoices
      </h3>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Backend</TableHead>
            <TableHead className="text-right">Free tier</TableHead>
            <TableHead className="text-right">Paid tier</TableHead>
            <TableHead>Note</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {runs.map((run) => {
            const paidCost = run.backend === "gemini" ? run.costPer1000Usd : 0;
            return (
              <TableRow key={run.id}>
                <TableCell className="text-text-primary">{run.backend}</TableCell>
                <TableCell className="text-right font-mono tabular-nums text-signal-clean">
                  $0.00
                </TableCell>
                <TableCell className="text-right font-mono tabular-nums text-text-primary">
                  {paidCost === null ? "—" : `$${paidCost.toFixed(2)}`}
                </TableCell>
                <TableCell className="text-xs text-text-muted">
                  {run.backend === "gemini"
                    ? "free tier is rate-limited, not free of API calls -- see GEMINI_RATE_LIMIT_RPM"
                    : paidTierNote(run.backend)}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

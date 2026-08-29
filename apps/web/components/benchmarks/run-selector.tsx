"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { EvalRunSummary } from "@/lib/benchmarks-api";

function runLabel(run: EvalRunSummary): string {
  const when = new Date(run.startedAt).toLocaleDateString();
  return `${run.dataset} / ${run.backend} · n=${run.sampleCount} · ${when}`;
}

export interface RunSelectorProps {
  runs: EvalRunSummary[];
  runAId: string | null;
  runBId: string | null;
  onChangeA: (id: string | null) => void;
  onChangeB: (id: string | null) => void;
}

export function RunSelector({ runs, runAId, runBId, onChangeA, onChangeB }: RunSelectorProps) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="flex items-center gap-2">
        <span className="text-xs text-text-muted">Run A</span>
        <Select value={runAId ?? undefined} onValueChange={(v) => onChangeA(v || null)}>
          <SelectTrigger className="w-72">
            <SelectValue placeholder="Select a run" />
          </SelectTrigger>
          <SelectContent>
            {runs.map((run) => (
              <SelectItem key={run.id} value={run.id}>
                {runLabel(run)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-xs text-text-muted">Compare to</span>
        <Select
          value={runBId ?? "none"}
          onValueChange={(v) => onChangeB(!v || v === "none" ? null : v)}
        >
          <SelectTrigger className="w-72">
            <SelectValue placeholder="No comparison" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">No comparison</SelectItem>
            {runs
              .filter((run) => run.id !== runAId)
              .map((run) => (
                <SelectItem key={run.id} value={run.id}>
                  {runLabel(run)}
                </SelectItem>
              ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  );
}

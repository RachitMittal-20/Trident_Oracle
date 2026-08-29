"use client";

import { useEffect, useState } from "react";

import { CalibrationPlot } from "@/components/benchmarks/calibration-plot";
import { CostProjectionTable } from "@/components/benchmarks/cost-projection-table";
import { FailureGallery } from "@/components/benchmarks/failure-gallery";
import { FieldComparisonChart } from "@/components/benchmarks/field-comparison-chart";
import { HeadlineStrip } from "@/components/benchmarks/headline-strip";
import { LatencyDistribution } from "@/components/benchmarks/latency-distribution";
import { RunSelector } from "@/components/benchmarks/run-selector";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import {
  BenchmarksApiError,
  fetchFailures,
  fetchRunDetail,
  fetchRuns,
  type EvalFailureDocument,
  type EvalRunDetail,
  type EvalRunSummary,
} from "@/lib/benchmarks-api";

export interface BenchmarksPageClientProps {
  apiBaseUrl: string;
}

export function BenchmarksPageClient({ apiBaseUrl }: BenchmarksPageClientProps) {
  const [runs, setRuns] = useState<EvalRunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runAId, setRunAId] = useState<string | null>(null);
  const [runBId, setRunBId] = useState<string | null>(null);
  const [runDetails, setRunDetails] = useState<EvalRunDetail[]>([]);
  const [failures, setFailures] = useState<EvalFailureDocument[]>([]);

  const loadRuns = () => {
    setError(null);
    fetchRuns(apiBaseUrl)
      .then((rows) => {
        setRuns(rows);
        if (rows.length > 0) setRunAId(rows[0]!.id);
      })
      .catch((err: unknown) => {
        setError(err instanceof BenchmarksApiError ? err.message : "Could not load eval runs.");
      });
  };

  useEffect(() => {
    loadRuns();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once on mount
  }, [apiBaseUrl]);

  useEffect(() => {
    if (!runAId) {
      setRunDetails([]);
      setFailures([]);
      return;
    }
    const ids = [runAId, ...(runBId ? [runBId] : [])];
    Promise.all(ids.map((id) => fetchRunDetail(apiBaseUrl, id)))
      .then(setRunDetails)
      .catch((err: unknown) => {
        setError(err instanceof BenchmarksApiError ? err.message : "Could not load run detail.");
      });
    fetchFailures(apiBaseUrl, runAId, 12)
      .then(setFailures)
      .catch(() => setFailures([]));
  }, [apiBaseUrl, runAId, runBId]);

  if (error) {
    return (
      <div className="p-10">
        <ErrorState description={error} onRetry={loadRuns} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 p-6">
      <h1 className="text-lg font-semibold text-text-primary">Benchmarks</h1>

      {runs === null ? (
        <LoadingState rows={4} />
      ) : runs.length === 0 ? (
        <EmptyState
          title="No eval runs yet"
          description="Run `python -m evals run --backend ... --dataset ... --n ...` to populate this dashboard."
        />
      ) : (
        <>
          <RunSelector
            runs={runs}
            runAId={runAId}
            runBId={runBId}
            onChangeA={setRunAId}
            onChangeB={setRunBId}
          />

          {runDetails.length > 0 && (
            <>
              <HeadlineStrip runs={runDetails} />
              <FieldComparisonChart runs={runDetails} />
              <CalibrationPlot runs={runDetails} />
              <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                <LatencyDistribution runs={runDetails} />
                <CostProjectionTable runs={runDetails} />
              </div>
              <FailureGallery documents={failures} />
            </>
          )}
        </>
      )}
    </div>
  );
}

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { BulkActionBar } from "@/components/exceptions/bulk-action-bar";
import { ExceptionsGrid } from "@/components/exceptions/exceptions-grid";
import { FilterBar } from "@/components/exceptions/filter-bar";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { useExceptionsUrlState } from "@/lib/exceptions-url";
import {
  ExceptionsApiError,
  fetchExceptions,
  resolveException,
  type ExceptionCard,
  type VendorOption,
} from "@/lib/exceptions-api";

export interface ExceptionsPageClientProps {
  tenantId: string;
  actorUserId: string;
  apiBaseUrl: string;
}

export function ExceptionsPageClient({ tenantId, actorUserId, apiBaseUrl }: ExceptionsPageClientProps) {
  const { filters, setFilter } = useExceptionsUrlState();
  const router = useRouter();

  const [exceptions, setExceptions] = useState<ExceptionCard[] | null>(null);
  const [vendors, setVendors] = useState<VendorOption[]>([]);
  const [autoPostedThisWeek, setAutoPostedThisWeek] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [resolvingIds, setResolvingIds] = useState<Set<string>>(new Set());
  const [bulkResolving, setBulkResolving] = useState(false);
  const [entranceGeneration, setEntranceGeneration] = useState(0);
  const lastClickedIndexRef = useRef<number | null>(null);

  const filterSignature = JSON.stringify(filters);

  const load = useCallback(() => {
    setError(null);
    fetchExceptions(apiBaseUrl, tenantId, filters)
      .then((data) => {
        setExceptions(data.exceptions);
        setVendors(data.vendors);
        setAutoPostedThisWeek(data.autoPostedThisWeek);
        setEntranceGeneration((g) => g + 1);
      })
      .catch((err: unknown) => {
        setError(err instanceof ExceptionsApiError ? err.message : "Could not load exceptions.");
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- filters is captured via filterSignature
  }, [apiBaseUrl, tenantId, filterSignature]);

  useEffect(() => {
    load();
  }, [load]);

  const handleToggleSelect = (id: string, event: React.MouseEvent) => {
    if (!exceptions) return;
    const index = exceptions.findIndex((exc) => exc.id === id);
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (event.shiftKey && lastClickedIndexRef.current !== null) {
        const [start, end] = [lastClickedIndexRef.current, index].sort((a, b) => a - b);
        for (let i = start; i <= end; i++) {
          next.add(exceptions[i]!.id);
        }
      } else if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
    lastClickedIndexRef.current = index;
  };

  const doResolve = async (id: string) => {
    setResolvingIds((prev) => new Set(prev).add(id));
    try {
      await resolveException(apiBaseUrl, tenantId, id, actorUserId);
      setExceptions((prev) => (prev ? prev.filter((exc) => exc.id !== id) : prev));
      setSelectedIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    } catch (err) {
      setError(err instanceof ExceptionsApiError ? err.message : "Could not resolve that exception.");
    } finally {
      setResolvingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const handleResolveSelected = async () => {
    setBulkResolving(true);
    const ids = Array.from(selectedIds);
    await Promise.all(ids.map((id) => doResolve(id)));
    setBulkResolving(false);
  };

  if (error) {
    return (
      <div className="p-10">
        <ErrorState description={error} onRetry={load} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 p-6 pb-24">
      <h1 className="text-lg font-semibold text-text-primary">Exceptions</h1>

      <FilterBar filters={filters} vendors={vendors} onChange={setFilter} />

      {exceptions === null ? (
        <LoadingState rows={4} />
      ) : exceptions.length === 0 ? (
        <EmptyState
          title="No open exceptions"
          description={`${autoPostedThisWeek} invoice${autoPostedThisWeek === 1 ? "" : "s"} auto-posted this week.`}
        />
      ) : (
        <ExceptionsGrid
          exceptions={exceptions}
          entranceGeneration={entranceGeneration}
          selectedIds={selectedIds}
          resolvingIds={resolvingIds}
          onToggleSelect={handleToggleSelect}
          onResolve={(id) => void doResolve(id)}
          onOpenInvoice={(invoiceId) => router.push(`/invoices/${invoiceId}/match`)}
        />
      )}

      <BulkActionBar
        count={selectedIds.size}
        resolving={bulkResolving}
        onResolveAll={() => void handleResolveSelected()}
        onClear={() => setSelectedIds(new Set())}
      />
    </div>
  );
}

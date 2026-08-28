"use client";

import { useCallback, useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { ExceptionsFilters, ExceptionSeverity } from "@/lib/exceptions-api";

/**
 * Filters live entirely in the URL (CLAUDE.md prompt: "so views are
 * shareable") -- this hook is the single place that reads them out of
 * useSearchParams and writes them back via router.replace, so no filter
 * state exists anywhere else that could drift from what's shareable.
 */
export function useExceptionsUrlState(): {
  filters: ExceptionsFilters;
  setFilter: (key: keyof ExceptionsFilters, value: string | undefined) => void;
} {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const filters = useMemo<ExceptionsFilters>(
    () => ({
      status: searchParams.get("status") ?? undefined,
      severity: (searchParams.get("severity") as ExceptionSeverity | null) ?? undefined,
      exceptionType: searchParams.get("type") ?? undefined,
      vendorId: searchParams.get("vendor") ?? undefined,
      dateFrom: searchParams.get("from") ?? undefined,
      dateTo: searchParams.get("to") ?? undefined,
      sort: (searchParams.get("sort") as ExceptionsFilters["sort"]) ?? undefined,
      order: (searchParams.get("order") as ExceptionsFilters["order"]) ?? undefined,
    }),
    [searchParams],
  );

  const paramKey: Record<keyof ExceptionsFilters, string> = {
    status: "status",
    severity: "severity",
    exceptionType: "type",
    vendorId: "vendor",
    dateFrom: "from",
    dateTo: "to",
    sort: "sort",
    order: "order",
  };

  const setFilter = useCallback(
    (key: keyof ExceptionsFilters, value: string | undefined) => {
      const next = new URLSearchParams(searchParams.toString());
      const urlKey = paramKey[key];
      if (value) next.set(urlKey, value);
      else next.delete(urlKey);
      router.replace(`${pathname}?${next.toString()}`, { scroll: false });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- paramKey is a stable literal
    [router, pathname, searchParams],
  );

  return { filters, setFilter };
}

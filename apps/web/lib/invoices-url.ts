"use client";

import { useCallback, useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { InvoiceSortField, InvoicesQuery } from "@/lib/invoices-api";

const PARAM_KEY: Record<keyof InvoicesQuery, string> = {
  status: "status",
  sort: "sort",
  order: "order",
  page: "page",
  pageSize: "page_size",
};

export function useInvoicesUrlState(): {
  query: InvoicesQuery;
  setQuery: (patch: Partial<InvoicesQuery>) => void;
} {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const query = useMemo<InvoicesQuery>(() => {
    const page = Number(searchParams.get("page") ?? "1");
    return {
      status: searchParams.get("status") ?? undefined,
      sort: (searchParams.get("sort") as InvoiceSortField | null) ?? undefined,
      order: (searchParams.get("order") as InvoicesQuery["order"]) ?? undefined,
      page: Number.isFinite(page) && page > 0 ? page : 1,
      pageSize: 25,
    };
  }, [searchParams]);

  const setQuery = useCallback(
    (patch: Partial<InvoicesQuery>) => {
      const next = new URLSearchParams(searchParams.toString());
      for (const [key, value] of Object.entries(patch)) {
        const urlKey = PARAM_KEY[key as keyof InvoicesQuery];
        if (value === undefined || value === null || value === "") next.delete(urlKey);
        else next.set(urlKey, String(value));
      }
      // Any change other than an explicit page change resets pagination --
      // a stale page number after a filter/sort change would just 404 into
      // an out-of-range offset.
      if (!("page" in patch)) next.delete("page");
      router.replace(`${pathname}?${next.toString()}`, { scroll: false });
    },
    [router, pathname, searchParams],
  );

  return { query, setQuery };
}

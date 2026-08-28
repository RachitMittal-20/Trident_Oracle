"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { InvoicesTable } from "@/components/invoices/invoices-table";
import { StatusDistributionBar } from "@/components/invoices/status-distribution-bar";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { STATUS_CONFIG, type InvoiceStatus } from "@/components/status-pill";
import { useInvoicesUrlState } from "@/lib/invoices-url";
import {
  InvoicesApiError,
  fetchInvoices,
  type InvoiceSortField,
  type InvoicesPage,
} from "@/lib/invoices-api";

export interface InvoicesPageClientProps {
  tenantId: string;
  apiBaseUrl: string;
}

const ALL_STATUSES = Object.keys(STATUS_CONFIG) as InvoiceStatus[];

export function InvoicesPageClient({ tenantId, apiBaseUrl }: InvoicesPageClientProps) {
  const { query, setQuery } = useInvoicesUrlState();
  const router = useRouter();

  const [data, setData] = useState<InvoicesPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setError(null);
    fetchInvoices(apiBaseUrl, tenantId, query)
      .then(setData)
      .catch((err: unknown) => {
        setError(err instanceof InvoicesApiError ? err.message : "Could not load invoices.");
      });
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- query captures its own fields; re-fetch whenever it changes
  }, [apiBaseUrl, tenantId, query.status, query.sort, query.order, query.page, query.pageSize]);

  const handleSortChange = (field: InvoiceSortField) => {
    if (query.sort === field) {
      setQuery({ order: query.order === "asc" ? "desc" : "asc" });
    } else {
      setQuery({ sort: field, order: "desc" });
    }
  };

  const handleOpenInvoice = (invoiceId: string, status: InvoiceStatus) => {
    if (status === "NEEDS_VERIFICATION") {
      router.push(`/invoices/${invoiceId}/verify`);
    } else {
      router.push(`/invoices/${invoiceId}/match`);
    }
  };

  if (error) {
    return (
      <div className="p-10">
        <ErrorState description={error} onRetry={load} />
      </div>
    );
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1;
  const page = query.page ?? 1;

  return (
    <div className="flex flex-col gap-6 p-6">
      <h1 className="text-lg font-semibold text-text-primary">Invoices</h1>

      {data && <StatusDistributionBar statusCounts={data.statusCounts} />}

      <div className="flex items-center gap-2">
        <Select
          value={query.status ?? "all"}
          onValueChange={(v) => setQuery({ status: !v || v === "all" ? undefined : v })}
        >
          <SelectTrigger className="w-48"><SelectValue placeholder="Status" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            {ALL_STATUSES.map((status) => (
              <SelectItem key={status} value={status}>
                {STATUS_CONFIG[status].label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {data === null ? (
        <LoadingState rows={6} />
      ) : data.items.length === 0 ? (
        <EmptyState
          title="No invoices found"
          description={query.status ? "No invoices match this status filter." : "Nothing has come in yet."}
        />
      ) : (
        <>
          <InvoicesTable
            items={data.items}
            query={query}
            onSortChange={handleSortChange}
            onOpenInvoice={handleOpenInvoice}
          />
          <div className="flex items-center justify-between text-sm text-text-muted">
            <span>
              Page {page} of {totalPages} · {data.total} invoice{data.total === 1 ? "" : "s"}
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setQuery({ page: page - 1 })}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => setQuery({ page: page + 1 })}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

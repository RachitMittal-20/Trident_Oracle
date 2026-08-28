import type { InvoiceStatus } from "@/components/status-pill";

export interface InvoiceListItem {
  id: string;
  invoiceNumber: string | null;
  invoiceDate: string | null;
  currency: string;
  total: string | null;
  status: InvoiceStatus;
  createdAt: string;
  vendorId: string | null;
  vendorName: string | null;
}

export interface InvoicesPage {
  items: InvoiceListItem[];
  total: number;
  page: number;
  pageSize: number;
  statusCounts: Record<string, number>;
}

interface WireInvoiceListItem {
  id: string;
  invoice_number: string | null;
  invoice_date: string | null;
  currency: string;
  total: string | null;
  status: InvoiceStatus;
  created_at: string;
  vendor_id: string | null;
  vendor_name: string | null;
}

interface WireInvoicesPage {
  items: WireInvoiceListItem[];
  total: number;
  page: number;
  page_size: number;
  status_counts: Record<string, number>;
}

function toItem(wire: WireInvoiceListItem): InvoiceListItem {
  return {
    id: wire.id,
    invoiceNumber: wire.invoice_number,
    invoiceDate: wire.invoice_date,
    currency: wire.currency,
    total: wire.total,
    status: wire.status,
    createdAt: wire.created_at,
    vendorId: wire.vendor_id,
    vendorName: wire.vendor_name,
  };
}

export class InvoicesApiError extends Error {}

export type InvoiceSortField = "invoice_date" | "total" | "status" | "created_at" | "invoice_number";

export interface InvoicesQuery {
  status?: string;
  sort?: InvoiceSortField;
  order?: "asc" | "desc";
  page?: number;
  pageSize?: number;
}

export async function fetchInvoices(
  apiBaseUrl: string,
  tenantId: string,
  query: InvoicesQuery,
): Promise<InvoicesPage> {
  const params = new URLSearchParams({ tenant_id: tenantId });
  if (query.status) params.set("status", query.status);
  if (query.sort) params.set("sort", query.sort);
  if (query.order) params.set("order", query.order);
  if (query.page) params.set("page", String(query.page));
  if (query.pageSize) params.set("page_size", String(query.pageSize));

  const response = await fetch(`${apiBaseUrl}/v1/invoices?${params.toString()}`);
  if (!response.ok) {
    throw new InvoicesApiError(`failed to load invoices: ${response.status}`);
  }
  const data = (await response.json()) as WireInvoicesPage;
  return {
    items: data.items.map(toItem),
    total: data.total,
    page: data.page,
    pageSize: data.page_size,
    statusCounts: data.status_counts,
  };
}

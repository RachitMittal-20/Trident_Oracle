"use client";

import { Fragment, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowDownIcon, ArrowUpIcon, ChevronRightIcon, ExternalLinkIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { MoneyValue } from "@/components/money-value";
import { StatusPill } from "@/components/status-pill";
import { cn } from "@/lib/utils";
import type { InvoiceListItem, InvoiceSortField, InvoicesQuery } from "@/lib/invoices-api";

const COLUMNS: { field: InvoiceSortField; label: string }[] = [
  { field: "invoice_number", label: "Invoice" },
  { field: "invoice_date", label: "Date" },
  { field: "status", label: "Status" },
  { field: "total", label: "Total" },
  { field: "created_at", label: "Received" },
];

// Statuses that don't yet have a detail route to open -- still expandable
// inline, just no "open" quick action.
function routeForStatus(status: InvoiceListItem["status"], invoiceId: string): string | null {
  if (status === "NEEDS_VERIFICATION") return `/invoices/${invoiceId}/verify`;
  if (
    status === "MATCHED_CLEAN" ||
    status === "EXCEPTIONS_RAISED" ||
    status === "PENDING_APPROVAL" ||
    status === "APPROVED" ||
    status === "REJECTED" ||
    status === "AUTO_POSTED" ||
    status === "POSTED"
  ) {
    return `/invoices/${invoiceId}/match`;
  }
  return null;
}

export interface InvoicesTableProps {
  items: InvoiceListItem[];
  query: InvoicesQuery;
  onSortChange: (sort: InvoiceSortField) => void;
  onOpenInvoice: (invoiceId: string, status: InvoiceListItem["status"]) => void;
}

export function InvoicesTable({ items, query, onSortChange, onOpenInvoice }: InvoicesTableProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-8" />
          {COLUMNS.map(({ field, label }) => {
            const active = query.sort === field || (!query.sort && field === "created_at");
            return (
              <TableHead key={field}>
                <button
                  type="button"
                  onClick={() => onSortChange(field)}
                  className={cn(
                    "inline-flex items-center gap-1 text-left hover:text-text-primary",
                    active ? "text-text-primary" : "text-text-muted",
                  )}
                >
                  {label}
                  {active &&
                    (query.order === "asc" ? (
                      <ArrowUpIcon className="size-3" />
                    ) : (
                      <ArrowDownIcon className="size-3" />
                    ))}
                </button>
              </TableHead>
            );
          })}
          <TableHead className="w-10" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((invoice) => {
          const expanded = expandedId === invoice.id;
          const route = routeForStatus(invoice.status, invoice.id);
          return (
            <Fragment key={invoice.id}>
              <motion.tr layout className="group/row border-b border-border last:border-0">
                <TableCell>
                  <button
                    type="button"
                    aria-label={expanded ? "Collapse" : "Expand"}
                    onClick={() => setExpandedId(expanded ? null : invoice.id)}
                    className="flex size-5 items-center justify-center text-text-muted hover:text-text-primary"
                  >
                    <ChevronRightIcon className={cn("size-3.5 transition-transform", expanded && "rotate-90")} />
                  </button>
                </TableCell>
                <TableCell className="font-mono text-xs text-text-primary">
                  {invoice.invoiceNumber ?? "—"}
                </TableCell>
                <TableCell className="text-text-muted">{invoice.invoiceDate ?? "—"}</TableCell>
                <TableCell>
                  <StatusPill status={invoice.status} />
                </TableCell>
                <TableCell>
                  {invoice.total ? (
                    <MoneyValue amount={invoice.total} currency={invoice.currency} />
                  ) : (
                    <span className="text-text-muted">—</span>
                  )}
                </TableCell>
                <TableCell className="text-text-muted">
                  {new Date(invoice.createdAt).toLocaleDateString()}
                </TableCell>
                <TableCell>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="opacity-0 group-hover/row:opacity-100"
                    disabled={!route}
                    aria-label="Open invoice"
                    onClick={() => onOpenInvoice(invoice.id, invoice.status)}
                  >
                    <ExternalLinkIcon className="size-3.5" />
                  </Button>
                </TableCell>
              </motion.tr>
              <AnimatePresence initial={false}>
                {expanded && (
                  <motion.tr layout>
                    <TableCell colSpan={7} className="whitespace-normal bg-bg-overlay/50 p-0">
                      <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.18, ease: "easeOut" }}
                        className="grid grid-cols-2 gap-4 p-4 sm:grid-cols-4"
                      >
                        <div>
                          <p className="text-xs text-text-muted">Vendor</p>
                          <p className="text-sm text-text-primary">{invoice.vendorName ?? "Unknown vendor"}</p>
                        </div>
                        <div>
                          <p className="text-xs text-text-muted">Invoice date</p>
                          <p className="text-sm text-text-primary">{invoice.invoiceDate ?? "—"}</p>
                        </div>
                        <div>
                          <p className="text-xs text-text-muted">Total</p>
                          <p className="text-sm text-text-primary">
                            {invoice.total ? (
                              <MoneyValue amount={invoice.total} currency={invoice.currency} />
                            ) : (
                              "—"
                            )}
                          </p>
                        </div>
                        <div className="flex items-end">
                          <Button variant="outline" size="sm" disabled={!route} onClick={() => onOpenInvoice(invoice.id, invoice.status)}>
                            Open invoice
                          </Button>
                        </div>
                      </motion.div>
                    </TableCell>
                  </motion.tr>
                )}
              </AnimatePresence>
            </Fragment>
          );
        })}
      </TableBody>
    </Table>
  );
}

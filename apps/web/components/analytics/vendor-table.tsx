"use client";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import type { VendorAnalyticsRow } from "@/lib/analytics-api";

const HIGH_EXCEPTION_RATE_PCT = 20;

export function VendorTable({ vendors }: { vendors: VendorAnalyticsRow[] }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <h3 className="mb-3 text-xs font-medium text-text-muted">
        Vendors, sorted by exception rate
      </h3>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Vendor</TableHead>
            <TableHead className="text-right">Invoices</TableHead>
            <TableHead className="text-right">Exception rate</TableHead>
            <TableHead className="text-right">Mean price variance</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {vendors.map((vendor) => {
            const rate = Number(vendor.exceptionRatePct);
            const flagged = rate > HIGH_EXCEPTION_RATE_PCT;
            return (
              <TableRow
                key={vendor.vendorId}
                className={cn(flagged && "border-l-2 border-l-signal-block")}
              >
                <TableCell className="text-text-primary">{vendor.vendorName}</TableCell>
                <TableCell className="text-right font-mono tabular-nums text-text-muted">
                  {vendor.invoiceCount}
                </TableCell>
                <TableCell
                  className={cn(
                    "text-right font-mono tabular-nums",
                    flagged ? "font-semibold text-signal-block" : "text-text-primary",
                  )}
                >
                  {rate.toFixed(2)}%
                </TableCell>
                <TableCell className="text-right font-mono tabular-nums text-text-muted">
                  {vendor.meanPriceVariancePct === null
                    ? "—"
                    : `${Number(vendor.meanPriceVariancePct).toFixed(1)}%`}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

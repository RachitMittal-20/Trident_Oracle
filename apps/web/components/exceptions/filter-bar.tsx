"use client";

import { ArrowDownIcon, ArrowUpIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ExceptionsFilters, VendorOption } from "@/lib/exceptions-api";

const EXCEPTION_TYPES = [
  "NO_PO",
  "NO_GRN",
  "DUPLICATE_INVOICE",
  "SUSPECTED_DUPLICATE",
  "PRICE_VARIANCE",
  "QTY_SHORT",
  "QTY_OVER",
  "UNMATCHED_LINE",
  "ARITHMETIC_ERROR",
  "TAX_MISMATCH",
  "DATE_ANOMALY",
];

export interface FilterBarProps {
  filters: ExceptionsFilters;
  vendors: VendorOption[];
  onChange: (key: keyof ExceptionsFilters, value: string | undefined) => void;
}

export function FilterBar({ filters, vendors, onChange }: FilterBarProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select value={filters.status ?? "open"} onValueChange={(v) => onChange("status", v ?? undefined)}>
        <SelectTrigger className="w-32"><SelectValue placeholder="Status" /></SelectTrigger>
        <SelectContent>
          <SelectItem value="open">Open</SelectItem>
          <SelectItem value="resolved">Resolved</SelectItem>
          <SelectItem value="dismissed">Dismissed</SelectItem>
        </SelectContent>
      </Select>

      <Select
        value={filters.severity ?? "all"}
        onValueChange={(v) => onChange("severity", !v || v === "all" ? undefined : v)}
      >
        <SelectTrigger className="w-32"><SelectValue placeholder="Severity" /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All severities</SelectItem>
          <SelectItem value="block">Block</SelectItem>
          <SelectItem value="warn">Warn</SelectItem>
          <SelectItem value="info">Info</SelectItem>
        </SelectContent>
      </Select>

      <Select
        value={filters.exceptionType ?? "all"}
        onValueChange={(v) => onChange("exceptionType", !v || v === "all" ? undefined : v)}
      >
        <SelectTrigger className="w-44"><SelectValue placeholder="Type" /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All types</SelectItem>
          {EXCEPTION_TYPES.map((t) => (
            <SelectItem key={t} value={t}>
              {t.replace(/_/g, " ")}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={filters.vendorId ?? "all"}
        onValueChange={(v) => onChange("vendorId", !v || v === "all" ? undefined : v)}
      >
        <SelectTrigger className="w-40"><SelectValue placeholder="Vendor" /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All vendors</SelectItem>
          {vendors.map((v) => (
            <SelectItem key={v.id} value={v.id}>
              {v.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Input
        type="date"
        value={filters.dateFrom ?? ""}
        onChange={(e) => onChange("dateFrom", e.target.value || undefined)}
        className="w-36"
        aria-label="From date"
      />
      <Input
        type="date"
        value={filters.dateTo ?? ""}
        onChange={(e) => onChange("dateTo", e.target.value || undefined)}
        className="w-36"
        aria-label="To date"
      />

      <div className="ml-auto flex items-center gap-1">
        <Select
          value={filters.sort ?? "age"}
          onValueChange={(v) => onChange("sort", v ?? undefined)}
        >
          <SelectTrigger className="w-32"><SelectValue placeholder="Sort" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="severity">Severity</SelectItem>
            <SelectItem value="age">Age</SelectItem>
            <SelectItem value="amount">Amount at risk</SelectItem>
          </SelectContent>
        </Select>
        <Button
          variant="outline"
          size="icon"
          aria-label="Toggle sort order"
          onClick={() => onChange("order", filters.order === "asc" ? "desc" : "asc")}
        >
          {filters.order === "asc" ? <ArrowUpIcon /> : <ArrowDownIcon />}
        </Button>
      </div>
    </div>
  );
}

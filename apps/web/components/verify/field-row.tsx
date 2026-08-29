"use client";

import { ConfidenceBar } from "@/components/confidence-bar";
import { EditableValue } from "@/components/verify/editable-value";
import { cn } from "@/lib/utils";
import type { ParsedFieldPath } from "@/lib/field-paths";

export interface FieldRowProps {
  parsed: ParsedFieldPath;
  value: string;
  confidence: number;
  currency: string;
  humanCorrected: boolean;
  isHovered: boolean;
  onHoverChange: (fieldPath: string | null) => void;
  onClick: (fieldPath: string) => void;
  onSave: (fieldPath: string, newValue: string) => Promise<void>;
}

export function FieldRow({
  parsed,
  value,
  confidence,
  currency,
  humanCorrected,
  isHovered,
  onHoverChange,
  onClick,
  onSave,
}: FieldRowProps) {
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`Highlight ${parsed.label} on the invoice image`}
      className={cn(
        "flex items-center justify-between gap-3 rounded-md px-2 py-1.5 outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/50",
        isHovered && "bg-bg-overlay",
      )}
      onMouseEnter={() => onHoverChange(parsed.fieldPath)}
      onMouseLeave={() => onHoverChange(null)}
      onFocus={() => onHoverChange(parsed.fieldPath)}
      onBlur={() => onHoverChange(null)}
      onClick={() => onClick(parsed.fieldPath)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick(parsed.fieldPath);
        }
      }}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="flex items-center gap-1.5 text-xs text-text-muted">
          {parsed.label}
          {humanCorrected && <span className="text-signal-clean">·corrected</span>}
        </span>
        <ConfidenceBar value={confidence} className="max-w-32" />
      </div>
      <EditableValue
        value={value}
        isMoney={parsed.isMoney}
        currency={currency}
        onSave={(newValue) => onSave(parsed.fieldPath, newValue)}
      />
    </div>
  );
}

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
      className={cn(
        "flex items-center justify-between gap-3 rounded-md px-2 py-1.5 transition-colors",
        isHovered && "bg-bg-overlay",
      )}
      onMouseEnter={() => onHoverChange(parsed.fieldPath)}
      onMouseLeave={() => onHoverChange(null)}
      onClick={() => onClick(parsed.fieldPath)}
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

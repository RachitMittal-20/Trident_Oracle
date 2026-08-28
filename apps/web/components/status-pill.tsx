import { cn } from "@/lib/utils";

/**
 * Mirrors packages/core/core/models.py::InvoiceStatus exactly -- keep this
 * list in sync with that enum, not the other way around.
 */
export type InvoiceStatus =
  | "RECEIVED"
  | "EXTRACTING"
  | "EXTRACTION_FAILED"
  | "EXTRACTED"
  | "MATCHING"
  | "MATCHED_CLEAN"
  | "NEEDS_VERIFICATION"
  | "EXCEPTIONS_RAISED"
  | "AUTO_POSTED"
  | "PENDING_APPROVAL"
  | "APPROVED"
  | "REJECTED"
  | "POSTED";

export type StatusTone = "clean" | "warn" | "block" | "neutral";

/** Statuses mid-pipeline get the subtle pulse; everything else holds still. */
const TRANSITIONAL_STATUSES = new Set<InvoiceStatus>(["EXTRACTING", "MATCHING"]);

export const STATUS_CONFIG: Record<InvoiceStatus, { label: string; tone: StatusTone }> = {
  RECEIVED: { label: "Received", tone: "neutral" },
  EXTRACTING: { label: "Extracting", tone: "neutral" },
  EXTRACTION_FAILED: { label: "Extraction Failed", tone: "block" },
  EXTRACTED: { label: "Extracted", tone: "neutral" },
  MATCHING: { label: "Matching", tone: "neutral" },
  MATCHED_CLEAN: { label: "Matched Clean", tone: "clean" },
  NEEDS_VERIFICATION: { label: "Needs Verification", tone: "warn" },
  EXCEPTIONS_RAISED: { label: "Exceptions Raised", tone: "block" },
  AUTO_POSTED: { label: "Auto-Posted", tone: "clean" },
  PENDING_APPROVAL: { label: "Pending Approval", tone: "warn" },
  APPROVED: { label: "Approved", tone: "clean" },
  REJECTED: { label: "Rejected", tone: "block" },
  POSTED: { label: "Posted", tone: "clean" },
};

const TONE_CLASSES: Record<StatusTone, string> = {
  clean: "bg-signal-clean/10 text-signal-clean",
  warn: "bg-signal-warn/10 text-signal-warn",
  block: "bg-signal-block/10 text-signal-block",
  neutral: "bg-text-muted/10 text-text-muted",
};

export const SOLID_TONE_CLASSES: Record<StatusTone, string> = {
  clean: "bg-signal-clean",
  warn: "bg-signal-warn",
  block: "bg-signal-block",
  neutral: "bg-text-muted",
};

const DOT_TONE_CLASSES: Record<StatusTone, string> = {
  clean: "bg-signal-clean",
  warn: "bg-signal-warn",
  block: "bg-signal-block",
  neutral: "bg-text-muted",
};

export interface StatusPillProps {
  status: InvoiceStatus;
  className?: string;
}

export function StatusPill({ status, className }: StatusPillProps) {
  const config = STATUS_CONFIG[status];
  const transitional = TRANSITIONAL_STATUSES.has(status);

  return (
    <span
      className={cn(
        "inline-flex h-5 w-fit shrink-0 items-center gap-1.5 rounded-4xl px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        TONE_CLASSES[config.tone],
        className,
      )}
    >
      <span className="relative flex size-1.5 shrink-0">
        {transitional && (
          <span
            className={cn(
              "absolute inline-flex size-full rounded-full opacity-75 motion-safe:animate-ping",
              DOT_TONE_CLASSES[config.tone],
            )}
          />
        )}
        <span className={cn("relative inline-flex size-1.5 rounded-full", DOT_TONE_CLASSES[config.tone])} />
      </span>
      {config.label}
    </span>
  );
}

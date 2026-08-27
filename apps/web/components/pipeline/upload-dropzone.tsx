"use client";

import { useRef, useState } from "react";
import { animate } from "animejs";
import { UploadIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { DURATION, EASING, useReducedMotion, withMotion } from "@/lib/motion";

export interface UploadDropzoneProps {
  tenantId: string;
  apiBaseUrl: string;
}

type UploadState = "idle" | "uploading" | "error";

/**
 * A real upload: drop (or pick) a file and it POSTs to
 * POST /v1/invoices/upload exactly as the rest of the app does. The new
 * card that "materializes at the QUEUED node" (per spec) is not drawn by
 * this component at all -- it's the same real SSE invoice_event every
 * other invoice produces, arriving because this upload really created a
 * RECEIVED row.
 */
export function UploadDropzone({ tenantId, apiBaseUrl }: UploadDropzoneProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [state, setState] = useState<UploadState>("idle");
  const borderPathRef = useRef<SVGRectElement>(null);
  const reducedMotion = useReducedMotion();

  const setBorderTrace = (drawn: boolean) => {
    const rect = borderPathRef.current;
    if (!rect) return;
    const length = rect.getTotalLength ? rect.getTotalLength() : 0;
    rect.style.strokeDasharray = `${length}`;
    withMotion(reducedMotion, () =>
      animate(rect, {
        strokeDashoffset: [rect.style.strokeDashoffset || `${length}`, drawn ? 0 : length],
        duration: DURATION.base,
        ease: EASING.entrance,
      }),
    );
    if (reducedMotion) {
      rect.style.strokeDashoffset = drawn ? "0" : `${length}`;
    }
  };

  const setExpanded = (expanded: boolean, containerEl: HTMLElement | null) => {
    if (!containerEl) return;
    withMotion(reducedMotion, () =>
      animate(containerEl, {
        scale: expanded ? 1.04 : 1,
        duration: DURATION.fast,
        ease: EASING.stateChange,
      }),
    );
    if (reducedMotion) {
      containerEl.style.transform = expanded ? "scale(1.04)" : "scale(1)";
    }
  };

  const upload = async (file: File) => {
    setState("uploading");
    try {
      const formData = new FormData();
      formData.append("tenant_id", tenantId);
      formData.append("file", file);
      const response = await fetch(`${apiBaseUrl}/v1/invoices/upload`, {
        method: "POST",
        body: formData,
      });
      if (!response.ok && response.status !== 409) {
        throw new Error(`upload failed: ${response.status}`);
      }
      setState("idle");
    } catch {
      setState("error");
    }
  };

  return (
    <label
      className={cn(
        "relative flex w-56 shrink-0 cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-border bg-card px-4 py-3 text-center text-xs text-text-muted transition-colors",
        isDragging && "border-accent text-text-primary",
        state === "error" && "border-signal-block text-signal-block",
      )}
      onDragOver={(event) => {
        event.preventDefault();
        if (!isDragging) {
          setIsDragging(true);
          setBorderTrace(true);
          setExpanded(true, event.currentTarget);
        }
      }}
      onDragLeave={(event) => {
        setIsDragging(false);
        setBorderTrace(false);
        setExpanded(false, event.currentTarget);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setIsDragging(false);
        setBorderTrace(false);
        setExpanded(false, event.currentTarget);
        const file = event.dataTransfer.files[0];
        if (file) void upload(file);
      }}
    >
      <svg className="pointer-events-none absolute inset-0 h-full w-full" fill="none">
        <rect
          ref={borderPathRef}
          x={1}
          y={1}
          width="calc(100% - 2px)"
          height="calc(100% - 2px)"
          rx={8}
          className="stroke-accent"
          strokeWidth={2}
          style={{ strokeDashoffset: 0, opacity: isDragging ? 1 : 0 }}
        />
      </svg>
      <UploadIcon className="size-4" aria-hidden="true" />
      <span>
        {state === "uploading"
          ? "Uploading…"
          : state === "error"
            ? "Upload failed — try again"
            : "Drop an invoice, or click to browse"}
      </span>
      <input
        type="file"
        accept="application/pdf,image/png,image/jpeg"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void upload(file);
          event.target.value = "";
        }}
      />
    </label>
  );
}

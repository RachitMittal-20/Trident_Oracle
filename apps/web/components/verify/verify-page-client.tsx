"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { DocumentViewer, type DocumentViewerHandle } from "@/components/verify/document-viewer";
import { FieldsPanel } from "@/components/verify/fields-panel";
import { VerifyFooter } from "@/components/verify/verify-footer";
import { ErrorState } from "@/components/states";
import { parseFieldPath } from "@/lib/field-paths";
import {
  correctInvoiceField,
  fetchInvoiceForVerification,
  rerunMatch,
  VerifyApiError,
  type VerificationInvoice,
} from "@/lib/verify-api";

export interface VerifyPageClientProps {
  invoiceId: string;
  tenantId: string;
  apiBaseUrl: string;
}

function applyCorrection(
  invoice: VerificationInvoice,
  fieldPath: string,
  newValue: string,
): VerificationInvoice {
  const parsed = parseFieldPath(fieldPath);
  if (!parsed) return invoice;

  const fieldConfidences = invoice.fieldConfidences.map((fc) =>
    fc.fieldPath === fieldPath ? { ...fc, confidence: "1.0000", humanCorrected: true } : fc,
  );

  if (parsed.group === "line" && parsed.lineIndex !== null) {
    const lineNo = parsed.lineIndex + 1;
    const lines = invoice.lines.map((line) =>
      line.lineNo === lineNo ? { ...line, [toLineCamel(parsed.column)]: newValue } : line,
    );
    return { ...invoice, lines, fieldConfidences };
  }

  const key = toHeaderCamel(parsed.column);
  return { ...invoice, [key]: newValue, fieldConfidences };
}

function toLineCamel(column: string): string {
  return column.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
}

function toHeaderCamel(column: string): string {
  return column.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
}

export function VerifyPageClient({ invoiceId, tenantId, apiBaseUrl }: VerifyPageClientProps) {
  const router = useRouter();
  const [invoice, setInvoice] = useState<VerificationInvoice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hoveredFieldPath, setHoveredFieldPath] = useState<string | null>(null);
  const [rerunning, setRerunning] = useState(false);
  const documentViewerRef = useRef<DocumentViewerHandle>(null);

  const load = useCallback(() => {
    setError(null);
    fetchInvoiceForVerification(apiBaseUrl, invoiceId, tenantId)
      .then(setInvoice)
      .catch((err: unknown) => {
        setError(err instanceof VerifyApiError ? err.message : "Could not load this invoice.");
      });
  }, [apiBaseUrl, invoiceId, tenantId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleSaveField = useCallback(
    async (fieldPath: string, newValue: string) => {
      const result = await correctInvoiceField(apiBaseUrl, invoiceId, tenantId, fieldPath, newValue);
      setInvoice((prev) => (prev ? applyCorrection(prev, result.fieldPath, result.value) : prev));
    },
    [apiBaseUrl, invoiceId, tenantId],
  );

  const handleClickField = useCallback((fieldPath: string) => {
    documentViewerRef.current?.scrollToField(fieldPath);
  }, []);

  const handleRerunMatch = useCallback(async () => {
    setRerunning(true);
    try {
      await rerunMatch(apiBaseUrl, invoiceId, tenantId);
      // Deliberate hand-off: the user watches their corrected invoice flow
      // through the pipeline, rather than staying on a screen that now has
      // nothing left to show them.
      router.push("/pipeline");
    } catch (err) {
      setRerunning(false);
      setError(err instanceof VerifyApiError ? err.message : "Could not start the match re-run.");
    }
  }, [apiBaseUrl, invoiceId, tenantId, router]);

  if (error) {
    return (
      <div className="p-10">
        <ErrorState description={error} onRetry={load} />
      </div>
    );
  }

  if (!invoice) {
    return <div className="p-10 text-sm text-text-muted">Loading…</div>;
  }

  const threshold = invoice.policyMinFieldConfidence !== null ? Number(invoice.policyMinFieldConfidence) : null;
  const belowThresholdCount =
    threshold === null
      ? 0
      : invoice.fieldConfidences.filter((fc) => Number(fc.confidence) < threshold).length;

  return (
    <div className="flex h-screen flex-col">
      <div className="flex flex-1 gap-4 overflow-hidden p-4">
        <div className="w-[60%]">
          <DocumentViewer
            ref={documentViewerRef}
            fileUrl={invoice.fileUrl}
            fields={invoice.fieldConfidences}
            threshold={threshold}
            hoveredFieldPath={hoveredFieldPath}
            onHoverField={setHoveredFieldPath}
            onClickField={handleClickField}
          />
        </div>
        <div className="w-[40%]">
          <FieldsPanel
            invoice={invoice}
            hoveredFieldPath={hoveredFieldPath}
            onHoverField={setHoveredFieldPath}
            onClickField={handleClickField}
            onSaveField={handleSaveField}
          />
        </div>
      </div>
      <VerifyFooter
        belowThresholdCount={belowThresholdCount}
        onRerunMatch={() => void handleRerunMatch()}
        rerunning={rerunning}
      />
    </div>
  );
}

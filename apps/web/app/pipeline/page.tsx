"use client";

import { PipelineBoard } from "@/components/pipeline/pipeline-board";

// Same auth-placeholder pattern as every other endpoint in this codebase
// (see apps/api/api/main.py's upload_invoice docstring) -- a real
// authentication system would replace both of these with values derived
// from the signed-in session, not env vars read at build time.
const TENANT_ID = process.env.NEXT_PUBLIC_DEMO_TENANT_ID ?? "";
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function PipelinePage() {
  if (!TENANT_ID) {
    return (
      <div className="p-10 text-sm text-signal-block">
        NEXT_PUBLIC_DEMO_TENANT_ID is not set (apps/web/.env.local).
      </div>
    );
  }

  return (
    <div className="p-10">
      <h1 className="mb-6 text-lg font-semibold text-text-primary">Pipeline</h1>
      <PipelineBoard tenantId={TENANT_ID} apiBaseUrl={API_BASE_URL} />
    </div>
  );
}

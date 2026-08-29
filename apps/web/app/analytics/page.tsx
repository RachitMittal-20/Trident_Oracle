"use client";

import { AnalyticsPageClient } from "@/components/analytics/analytics-page-client";

// Same auth-placeholder pattern as /pipeline, /exceptions, and /invoices --
// a real auth system would replace this with the signed-in session's tenant.
const TENANT_ID = process.env.NEXT_PUBLIC_DEMO_TENANT_ID ?? "";
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function AnalyticsPage() {
  if (!TENANT_ID) {
    return (
      <div className="p-10 text-sm text-signal-block">
        NEXT_PUBLIC_DEMO_TENANT_ID must be set (apps/web/.env.local).
      </div>
    );
  }

  return <AnalyticsPageClient tenantId={TENANT_ID} apiBaseUrl={API_BASE_URL} />;
}

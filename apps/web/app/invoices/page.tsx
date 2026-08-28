"use client";

import { Suspense } from "react";

import { InvoicesPageClient } from "@/components/invoices/invoices-page-client";

// Same auth-placeholder pattern as /pipeline, /exceptions, and
// /invoices/[id]/match -- a real auth system would replace both of these
// with the signed-in session's tenant and user.
const TENANT_ID = process.env.NEXT_PUBLIC_DEMO_TENANT_ID ?? "";
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function InvoicesPage() {
  if (!TENANT_ID) {
    return (
      <div className="p-10 text-sm text-signal-block">
        NEXT_PUBLIC_DEMO_TENANT_ID must be set (apps/web/.env.local).
      </div>
    );
  }

  return (
    <Suspense fallback={null}>
      <InvoicesPageClient tenantId={TENANT_ID} apiBaseUrl={API_BASE_URL} />
    </Suspense>
  );
}

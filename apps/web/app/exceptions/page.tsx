"use client";

import { Suspense } from "react";

import { ExceptionsPageClient } from "@/components/exceptions/exceptions-page-client";

// Same auth-placeholder pattern as /pipeline and /invoices/[id]/match --
// a real auth system would replace both of these with the signed-in
// session's tenant and user.
const TENANT_ID = process.env.NEXT_PUBLIC_DEMO_TENANT_ID ?? "";
const USER_ID = process.env.NEXT_PUBLIC_DEMO_USER_ID ?? "";
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function ExceptionsPage() {
  if (!TENANT_ID || !USER_ID) {
    return (
      <div className="p-10 text-sm text-signal-block">
        NEXT_PUBLIC_DEMO_TENANT_ID and NEXT_PUBLIC_DEMO_USER_ID must both be set
        (apps/web/.env.local).
      </div>
    );
  }

  return (
    <Suspense fallback={null}>
      <ExceptionsPageClient tenantId={TENANT_ID} actorUserId={USER_ID} apiBaseUrl={API_BASE_URL} />
    </Suspense>
  );
}

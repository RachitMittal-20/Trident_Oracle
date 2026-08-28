"use client";

import { useParams } from "next/navigation";

import { VerifyPageClient } from "@/components/verify/verify-page-client";

// Same auth-placeholder pattern as /pipeline (see that page's own comment,
// and apps/api/api/main.py's upload_invoice docstring) -- a real auth
// system would replace this with the signed-in session's tenant.
const TENANT_ID = process.env.NEXT_PUBLIC_DEMO_TENANT_ID ?? "";
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function VerifyPage() {
  const params = useParams<{ id: string }>();

  if (!TENANT_ID) {
    return (
      <div className="p-10 text-sm text-signal-block">
        NEXT_PUBLIC_DEMO_TENANT_ID is not set (apps/web/.env.local).
      </div>
    );
  }

  return <VerifyPageClient invoiceId={params.id} tenantId={TENANT_ID} apiBaseUrl={API_BASE_URL} />;
}

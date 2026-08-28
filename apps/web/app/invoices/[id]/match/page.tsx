"use client";

import { useParams } from "next/navigation";

import { MatchBoard } from "@/components/match/match-board";

// Same auth-placeholder pattern as /pipeline and /invoices/[id]/verify --
// a real auth system would replace both of these with the signed-in
// session's tenant and user. NEXT_PUBLIC_DEMO_USER_ID must be a real
// users.id row with role 'admin' or 'approver' (apps/api/api/match_view.py)
// for the Approve/Reject actions to be permitted.
const TENANT_ID = process.env.NEXT_PUBLIC_DEMO_TENANT_ID ?? "";
const USER_ID = process.env.NEXT_PUBLIC_DEMO_USER_ID ?? "";
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function MatchPage() {
  const params = useParams<{ id: string }>();

  if (!TENANT_ID || !USER_ID) {
    return (
      <div className="p-10 text-sm text-signal-block">
        NEXT_PUBLIC_DEMO_TENANT_ID and NEXT_PUBLIC_DEMO_USER_ID must both be set
        (apps/web/.env.local).
      </div>
    );
  }

  return (
    <MatchBoard invoiceId={params.id} tenantId={TENANT_ID} actorUserId={USER_ID} apiBaseUrl={API_BASE_URL} />
  );
}

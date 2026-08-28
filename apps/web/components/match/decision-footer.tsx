import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface DecisionFooterProps {
  status: string;
  result: "clean" | "exceptions" | "blocked" | null;
  reason: string | null;
  canDecide: boolean;
  deciding: boolean;
  /** Set once this caller has approved but the invoice is still waiting
   * on at least one more required approver (dual approval). */
  pendingApproval: { received: number; required: number } | null;
  onApprove: () => void;
  onReject: () => void;
}

const RESULT_TONE: Record<string, string> = {
  clean: "text-signal-clean",
  exceptions: "text-signal-warn",
  blocked: "text-signal-block",
};

export function DecisionFooter({
  status,
  result,
  reason,
  canDecide,
  deciding,
  pendingApproval,
  onApprove,
  onReject,
}: DecisionFooterProps) {
  return (
    <div className="sticky bottom-0 flex items-center justify-between gap-4 border-t border-border bg-bg-raised px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className={cn("text-xs font-semibold uppercase tracking-wide", result && RESULT_TONE[result])}>
            {status.replace(/_/g, " ")}
          </span>
          {pendingApproval && pendingApproval.received < pendingApproval.required && (
            <span className="text-xs font-medium text-signal-warn">
              {pendingApproval.received} of {pendingApproval.required} approvals received —
              waiting on {pendingApproval.required - pendingApproval.received} more approver
              {pendingApproval.required - pendingApproval.received === 1 ? "" : "s"}
            </span>
          )}
        </div>
        {reason && <p className="mt-0.5 truncate text-xs text-text-muted" title={reason}>{reason}</p>}
      </div>
      {canDecide && (
        <div className="flex shrink-0 gap-2">
          <Button variant="outline" onClick={onReject} disabled={deciding}>
            Reject
          </Button>
          <Button onClick={onApprove} disabled={deciding}>
            {deciding ? "Deciding…" : "Approve"}
          </Button>
        </div>
      )}
    </div>
  );
}

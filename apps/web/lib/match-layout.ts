import type { MatchException, MatchInvoiceLine } from "@/lib/match-api";

export type LineOutcome = "clean" | "variance" | "block" | "unmatched";

/**
 * Both functions below only consider *open* exceptions -- once an
 * exception is resolved or dismissed (apps/api/api/match_view.py's
 * decide_invoice, on approve/reject), the invoice line it was raised
 * against should read as settled on any later load of this screen, not
 * keep showing red/amber connectors for a decision that's already been
 * made. The live approve transition (components/match/match-board.tsx's
 * runApprovalTimeline) recolors connectors imperatively for the user who
 * just clicked Approve; this is what keeps a fresh page load consistent
 * with that same settled state.
 */
function openExceptionsForLine(line: MatchInvoiceLine, exceptions: MatchException[]): MatchException[] {
  return exceptions.filter((exc) => exc.invoiceLineId === line.id && exc.status === "open");
}

export function outcomeForInvoiceLine(
  line: MatchInvoiceLine,
  exceptions: MatchException[],
): LineOutcome {
  if (line.matchedPoLineId === null) return "unmatched";
  const lineExceptions = openExceptionsForLine(line, exceptions);
  if (lineExceptions.some((exc) => exc.severity === "block")) return "block";
  if (lineExceptions.some((exc) => exc.severity === "warn")) return "variance";
  return "clean";
}

export function exceptionForInvoiceLine(
  line: MatchInvoiceLine,
  exceptions: MatchException[],
): MatchException | null {
  const lineExceptions = openExceptionsForLine(line, exceptions);
  const blocking = lineExceptions.find((exc) => exc.severity === "block");
  return blocking ?? lineExceptions[0] ?? null;
}

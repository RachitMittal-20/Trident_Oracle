import { StatusPill, type InvoiceStatus } from "@/components/status-pill";

const ALL_STATUSES: InvoiceStatus[] = [
  "RECEIVED",
  "EXTRACTING",
  "EXTRACTION_FAILED",
  "EXTRACTED",
  "MATCHING",
  "MATCHED_CLEAN",
  "NEEDS_VERIFICATION",
  "EXCEPTIONS_RAISED",
  "AUTO_POSTED",
  "PENDING_APPROVAL",
  "APPROVED",
  "REJECTED",
  "POSTED",
];

export default function StatusPillDemoPage() {
  return (
    <div className="flex max-w-md flex-col gap-2 p-10">
      {ALL_STATUSES.map((status) => (
        <StatusPill key={status} status={status} />
      ))}
    </div>
  );
}

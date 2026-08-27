import { StatCard } from "@/components/stat-card";

export default function StatCardDemoPage() {
  return (
    <div className="grid max-w-2xl grid-cols-3 gap-4 p-10">
      <StatCard label="Invoices this week" value={128} delta={12} />
      <StatCard label="Exceptions raised" value={7} delta={-3} />
      <StatCard label="Auto-posted" value={94} />
    </div>
  );
}

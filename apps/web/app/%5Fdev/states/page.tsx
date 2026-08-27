"use client";

import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";

export default function StatesDemoPage() {
  return (
    <div className="max-w-md space-y-8 p-10">
      <EmptyState
        title="No invoices yet"
        description="Invoices will appear here once vendors start sending them in."
        action={<Button size="sm">Upload invoice</Button>}
      />
      <ErrorState description="Could not reach the matching service." onRetry={() => {}} />
      <LoadingState />
    </div>
  );
}

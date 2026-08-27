import { cn } from "@/lib/utils";
import type { ConnectionState } from "@/lib/pipeline-events";

const CONFIG: Record<ConnectionState, { label: string; dotClass: string; pulse: boolean }> = {
  open: { label: "Live", dotClass: "bg-signal-clean", pulse: false },
  connecting: { label: "Connecting…", dotClass: "bg-text-muted", pulse: true },
  reconnecting: { label: "Reconnecting…", dotClass: "bg-signal-warn", pulse: true },
};

export function ConnectionIndicator({ state }: { state: ConnectionState }) {
  const config = CONFIG[state];
  return (
    <div className="flex items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1 text-xs text-text-muted">
      <span className="relative flex size-1.5 shrink-0">
        {config.pulse && (
          <span className={cn("absolute inline-flex size-full rounded-full opacity-75 motion-safe:animate-ping", config.dotClass)} />
        )}
        <span className={cn("relative inline-flex size-1.5 rounded-full", config.dotClass)} />
      </span>
      {config.label}
    </div>
  );
}

import type { ReactNode } from "react";
import { InboxIcon, OctagonXIcon, Loader2Icon } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

interface StateShellProps {
  icon: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
  iconClassName?: string;
}

function StateShell({ icon, title, description, action, className, iconClassName }: StateShellProps) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border p-10 text-center", className)}>
      <div className={cn("flex size-10 items-center justify-center rounded-full bg-bg-overlay text-text-muted", iconClassName)}>
        {icon}
      </div>
      <div className="space-y-1">
        <p className="text-sm font-medium text-text-primary">{title}</p>
        {description && <p className="text-sm text-text-muted">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export interface EmptyStateProps {
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ title, description, action, className }: EmptyStateProps) {
  return (
    <StateShell
      icon={<InboxIcon className="size-5" aria-hidden="true" />}
      title={title}
      description={description}
      action={action}
      className={className}
    />
  );
}

export interface ErrorStateProps {
  title?: string;
  description?: string;
  onRetry?: () => void;
  className?: string;
}

export function ErrorState({ title = "Something went wrong", description, onRetry, className }: ErrorStateProps) {
  return (
    <StateShell
      icon={<OctagonXIcon className="size-5" aria-hidden="true" />}
      iconClassName="bg-signal-block/10 text-signal-block"
      title={title}
      description={description}
      action={
        onRetry && (
          <Button variant="outline" size="sm" onClick={onRetry}>
            Retry
          </Button>
        )
      }
      className={className}
    />
  );
}

export interface LoadingStateProps {
  /** Number of skeleton rows to render. */
  rows?: number;
  className?: string;
}

export function LoadingState({ rows = 3, className }: LoadingStateProps) {
  return (
    <div className={cn("space-y-3", className)} role="status" aria-label="Loading">
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-12 w-full" />
      ))}
      <span className="sr-only inline-flex items-center gap-1">
        <Loader2Icon className="size-3 animate-spin" aria-hidden="true" />
        Loading
      </span>
    </div>
  );
}

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface AppShellNavItem {
  label: string;
  href: string;
  icon?: ReactNode;
  active?: boolean;
}

export interface AppShellProps {
  navItems: AppShellNavItem[];
  topBar?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** Fixed sidebar + top bar + scrollable content area. No feature logic here. */
export function AppShell({ navItems, topBar, children, className }: AppShellProps) {
  return (
    <div className={cn("flex h-dvh bg-background text-foreground", className)}>
      <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-bg-raised">
        <div className="flex h-14 items-center border-b border-border px-4">
          <span className="text-sm font-semibold tracking-tight">Trident Oracle</span>
        </div>
        <nav className="flex-1 space-y-0.5 p-2">
          {navItems.map((item) => (
            <a
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium text-text-muted transition-colors hover:bg-bg-overlay hover:text-text-primary",
                item.active && "bg-bg-overlay text-text-primary",
              )}
            >
              {item.icon}
              {item.label}
            </a>
          ))}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center border-b border-border px-6">{topBar}</header>
        <main className="min-w-0 flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  );
}

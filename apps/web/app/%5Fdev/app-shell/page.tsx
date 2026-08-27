import { LayoutDashboardIcon, FileTextIcon, SettingsIcon } from "lucide-react";

import { AppShell } from "@/components/app-shell";

export default function AppShellDemoPage() {
  return (
    <AppShell
      navItems={[
        { label: "Dashboard", href: "#dashboard", icon: <LayoutDashboardIcon className="size-4" />, active: true },
        { label: "Invoices", href: "#invoices", icon: <FileTextIcon className="size-4" /> },
        { label: "Settings", href: "#settings", icon: <SettingsIcon className="size-4" /> },
      ]}
      topBar={<span className="text-sm font-medium text-text-primary">Dashboard</span>}
    >
      <div className="rounded-lg border border-dashed border-border p-10 text-center text-sm text-text-muted">
        Content area
      </div>
    </AppShell>
  );
}

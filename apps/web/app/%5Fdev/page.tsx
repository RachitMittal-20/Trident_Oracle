const DEMOS = [
  { href: "/_dev/app-shell", label: "AppShell" },
  { href: "/_dev/stat-card", label: "StatCard" },
  { href: "/_dev/status-pill", label: "StatusPill" },
  { href: "/_dev/confidence-bar", label: "ConfidenceBar" },
  { href: "/_dev/money-value", label: "MoneyValue" },
  { href: "/_dev/states", label: "EmptyState / ErrorState / LoadingState" },
];

export default function DevIndexPage() {
  return (
    <div className="mx-auto max-w-md space-y-2 p-10">
      <h1 className="text-lg font-semibold text-text-primary">Component demos</h1>
      <ul className="space-y-1">
        {DEMOS.map((demo) => (
          <li key={demo.href}>
            <a href={demo.href} className="text-sm text-accent hover:underline">
              {demo.label}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

import { ConfidenceBar } from "@/components/confidence-bar";

export default function ConfidenceBarDemoPage() {
  return (
    <div className="max-w-sm space-y-6 p-10">
      {[0.97, 0.72, 0.31].map((value) => (
        <div key={value} className="space-y-1.5">
          <div className="flex justify-between text-xs text-text-muted">
            <span>Confidence</span>
            <span className="font-mono tabular-nums">{Math.round(value * 100)}%</span>
          </div>
          <ConfidenceBar value={value} />
        </div>
      ))}
    </div>
  );
}

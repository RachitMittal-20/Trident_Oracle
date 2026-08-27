import { MoneyValue } from "@/components/money-value";

export default function MoneyValueDemoPage() {
  return (
    <div className="flex max-w-sm flex-col gap-2 p-10 text-lg">
      <MoneyValue amount="1234.5" />
      <MoneyValue amount="-89.99" />
      <MoneyValue amount="1000000" />
      <MoneyValue amount="42.1" currency="EUR" />
      <MoneyValue amount="999.99" currency="INR" />
    </div>
  );
}

import { cn } from "@/lib/utils";

export interface MoneyValueProps {
  /** Decimal amount as a string, e.g. "1234.50" -- never a float. */
  amount: string;
  currency?: string;
  className?: string;
}

/**
 * Formats a Decimal-as-string amount without ever parsing it into a JS
 * number (float precision would defeat the point of storing NUMERIC(14,2)).
 * Splits on the decimal point and grouping is applied to the integer part
 * only via string manipulation.
 */
function formatDecimalString(amount: string): { sign: string; integer: string; fraction: string } {
  const negative = amount.trim().startsWith("-");
  const unsigned = negative ? amount.trim().slice(1) : amount.trim();
  const [integerPart, fractionPart = "00"] = unsigned.split(".");
  const grouped = integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return {
    sign: negative ? "-" : "",
    integer: grouped,
    fraction: fractionPart.padEnd(2, "0").slice(0, 2),
  };
}

const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: "$",
  EUR: "€",
  GBP: "£",
  INR: "₹",
};

export function MoneyValue({ amount, currency = "USD", className }: MoneyValueProps) {
  const { sign, integer, fraction } = formatDecimalString(amount);
  const symbol = CURRENCY_SYMBOLS[currency] ?? `${currency} `;

  return (
    <span className={cn("font-mono tabular-nums", className)}>
      {sign}
      {symbol}
      {integer}
      <span className="text-muted-foreground">.{fraction}</span>
    </span>
  );
}

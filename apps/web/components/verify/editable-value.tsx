"use client";

import { useRef, useState } from "react";
import { animate } from "animejs";
import { CheckIcon, PencilIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { formatMoneyPlain, MoneyValue } from "@/components/money-value";
import { DURATION, EASING, useReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";

export interface EditableValueProps {
  value: string;
  isMoney: boolean;
  currency?: string;
  onSave: (newValue: string) => Promise<void>;
  className?: string;
}

/**
 * Click the pencil, edit, save. On a successful save the display value
 * animates into place -- a numeric field counts from its old value to the
 * new one (the same counter technique as StatCard); anything else
 * cross-fades. Both are transform/opacity-only and skipped entirely under
 * reduced motion (the new value just appears).
 *
 * React's own `displayValue` state is deliberately not updated until the
 * animation's `.then()` fires: the animated span's textContent is written
 * imperatively every frame, and if React re-rendered mid-animation with
 * the already-new value, its diff would immediately overwrite that
 * textContent and cut the count short. Holding state until the animation
 * finishes means React's eventual re-render lands on a value that already
 * matches what's on screen -- a no-op swap, not a visible jump.
 */
export function EditableValue({ value, isMoney, currency = "USD", onSave, className }: EditableValueProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const [displayValue, setDisplayValue] = useState(value);
  const valueRef = useRef<HTMLSpanElement>(null);
  const reducedMotion = useReducedMotion();

  const startEdit = () => {
    setDraft(displayValue);
    setEditing(true);
  };

  const cancelEdit = () => setEditing(false);

  const animateToNewValue = (from: string, to: string): Promise<void> => {
    const el = valueRef.current;
    if (!el || reducedMotion) return Promise.resolve();

    const fromNumber = isMoney ? Number(from) : NaN;
    const toNumber = isMoney ? Number(to) : NaN;

    if (isMoney && Number.isFinite(fromNumber) && Number.isFinite(toNumber)) {
      const counter = { current: fromNumber };
      return new Promise((resolve) => {
        animate(counter, {
          current: toNumber,
          duration: DURATION.base,
          ease: EASING.entrance,
          onUpdate: () => {
            if (el) el.textContent = formatMoneyPlain(counter.current.toFixed(2), currency);
          },
        }).then(() => resolve());
      });
    }

    // Non-numeric morph: write the new text in immediately (React hasn't
    // re-rendered yet, so this is the only thing touching el's content),
    // then fade/lift it into place.
    el.textContent = to;
    return new Promise((resolve) => {
      animate(el, {
        opacity: [0, 1],
        translateY: [-4, 0],
        duration: DURATION.base,
        ease: EASING.entrance,
      }).then(() => resolve());
    });
  };

  const commitEdit = async () => {
    if (draft === displayValue) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      await onSave(draft);
      setEditing(false);
      await animateToNewValue(displayValue, draft);
      setDisplayValue(draft);
    } finally {
      setSaving(false);
    }
  };

  if (editing) {
    return (
      <div className={cn("flex items-center gap-1", className)}>
        <Input
          autoFocus
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void commitEdit();
            if (event.key === "Escape") cancelEdit();
          }}
          className="h-7 font-mono text-xs"
          disabled={saving}
        />
        <Button variant="ghost" size="icon-xs" aria-label="Save" onClick={() => void commitEdit()} disabled={saving}>
          <CheckIcon />
        </Button>
        <Button variant="ghost" size="icon-xs" aria-label="Cancel" onClick={cancelEdit} disabled={saving}>
          <XIcon />
        </Button>
      </div>
    );
  }

  return (
    <div className={cn("group/editable flex items-center gap-1.5", className)}>
      <span ref={valueRef} className="font-mono tabular-nums text-text-primary">
        {isMoney ? <MoneyValue amount={displayValue} currency={currency} /> : displayValue}
      </span>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="Edit value"
        className="opacity-0 group-hover/editable:opacity-100"
        onClick={startEdit}
      >
        <PencilIcon />
      </Button>
    </div>
  );
}

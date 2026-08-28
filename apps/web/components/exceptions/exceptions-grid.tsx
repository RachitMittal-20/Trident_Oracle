"use client";

import { useEffect, useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { animate, stagger } from "animejs";

import { ExceptionGridCard } from "@/components/exceptions/exception-grid-card";
import { DURATION, EASING, useReducedMotion, withMotion } from "@/lib/motion";
import type { ExceptionCard } from "@/lib/exceptions-api";

export interface ExceptionsGridProps {
  exceptions: ExceptionCard[];
  /** Bumped only when filters/sort genuinely change (not when a single
   * card is removed by resolving) -- see app/exceptions/page.tsx. That
   * split is what lets resolving one card just reflow the rest via
   * Framer's `layout` prop, instead of replaying every remaining card's
   * entrance too. */
  entranceGeneration: number;
  selectedIds: Set<string>;
  resolvingIds: Set<string>;
  onToggleSelect: (id: string, event: React.MouseEvent) => void;
  onResolve: (id: string) => void;
  onOpenInvoice: (invoiceId: string) => void;
}

export function ExceptionsGrid({
  exceptions,
  entranceGeneration,
  selectedIds,
  resolvingIds,
  onToggleSelect,
  onResolve,
  onOpenInvoice,
}: ExceptionsGridProps) {
  const gridRef = useRef<HTMLDivElement>(null);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    const container = gridRef.current;
    if (!container) return;
    const cards = container.querySelectorAll("[data-exception-card]");
    if (cards.length === 0) return;

    if (reducedMotion) {
      for (const card of cards) {
        (card as HTMLElement).style.opacity = "1";
        (card as HTMLElement).style.filter = "blur(0px)";
        (card as HTMLElement).style.transform = "translateY(0px)";
      }
      return;
    }

    withMotion(reducedMotion, () =>
      animate(cards, {
        opacity: [0, 1],
        translateY: [12, 0],
        filter: ["blur(4px)", "blur(0px)"],
        duration: DURATION.base,
        ease: EASING.entrance,
        delay: stagger(35),
      }),
    );
    // Deliberately keyed only on entranceGeneration/reducedMotion, not `exceptions` --
    // resolving a single card should reflow via Framer's `layout` prop, not re-stagger.
  }, [entranceGeneration, reducedMotion]);

  return (
    <div
      ref={gridRef}
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
    >
      <AnimatePresence>
        {exceptions.map((exception) => (
          <motion.div
            key={exception.id}
            layout
            exit={{ opacity: 0, scale: 0.92 }}
            transition={{ duration: DURATION.base / 1000, ease: "easeInOut" }}
          >
            <ExceptionGridCard
              exception={exception}
              selected={selectedIds.has(exception.id)}
              resolving={resolvingIds.has(exception.id)}
              onToggleSelect={(event) => onToggleSelect(exception.id, event)}
              onResolve={() => onResolve(exception.id)}
              onOpenInvoice={() => onOpenInvoice(exception.invoiceId)}
            />
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

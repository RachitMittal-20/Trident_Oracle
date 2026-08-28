"use client";

import { AnimatePresence, motion } from "framer-motion";

import { Button } from "@/components/ui/button";

export interface BulkActionBarProps {
  count: number;
  resolving: boolean;
  onResolveAll: () => void;
  onClear: () => void;
}

export function BulkActionBar({ count, resolving, onResolveAll, onClear }: BulkActionBarProps) {
  return (
    <AnimatePresence>
      {count > 0 && (
        <motion.div
          initial={{ y: 64, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: 64, opacity: 0 }}
          transition={{ duration: 0.24, ease: "easeOut" }}
          className="fixed bottom-6 left-1/2 z-20 flex -translate-x-1/2 items-center gap-4 rounded-full border border-border bg-bg-overlay px-5 py-2.5 shadow-lg"
        >
          <span className="text-sm text-text-primary">
            {count} selected
          </span>
          <Button variant="ghost" size="sm" onClick={onClear}>
            Clear
          </Button>
          <Button size="sm" onClick={onResolveAll} disabled={resolving}>
            {resolving ? "Resolving…" : "Resolve selected"}
          </Button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

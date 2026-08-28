"use client";

import { useEffect, useRef } from "react";
import type { PDFDocumentProxy } from "pdfjs-dist";

export interface PdfPageProps {
  doc: PDFDocumentProxy;
  pageNumber: number;
  scale: number;
  className?: string;
  /** Fires once the canvas has its final pixel dimensions and pixels --
   * the field-box overlay (components/verify/field-box.tsx) measures its
   * own SVG rect on mount, which only produces a correct
   * stroke-dasharray/dashoffset if the page container around it already
   * has its real, final size. Rendering the overlay before this fires
   * would measure against a zero/default-sized canvas and never recover
   * (nothing re-triggers that measurement later). */
  onRendered?: () => void;
}

/** Renders one PDF page into its own canvas. Each page owns its render
 * lifecycle via its own ref -- no cross-component ref bookkeeping in the
 * parent viewer. */
export function PdfPage({ doc, pageNumber, scale, className, onRendered }: PdfPageProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let cancelled = false;
    let renderTask: { cancel: () => void } | null = null;

    (async () => {
      const page = await doc.getPage(pageNumber);
      if (cancelled) return;
      const viewport = page.getViewport({ scale });
      const canvas = canvasRef.current;
      if (!canvas) return;
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      const context = canvas.getContext("2d");
      if (!context) return;
      const task = page.render({ canvasContext: context, viewport, canvas });
      renderTask = task;
      await task.promise;
      if (!cancelled) onRendered?.();
    })();

    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- onRendered is a stable callback identity from the parent
  }, [doc, pageNumber, scale]);

  return <canvas ref={canvasRef} className={className} />;
}

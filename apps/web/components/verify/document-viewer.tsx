"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type WheelEvent,
} from "react";
import type { PDFDocumentProxy } from "pdfjs-dist";

import { FieldBox } from "@/components/verify/field-box";
import { PdfPage } from "@/components/verify/pdf-page";
import { isPdfPath, loadPdfDocument } from "@/lib/pdf";
import type { FieldConfidence } from "@/lib/verify-api";
import { Button } from "@/components/ui/button";
import { ZoomInIcon, ZoomOutIcon } from "lucide-react";

const RENDER_SCALE = 1.5;
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.15;

export interface DocumentViewerHandle {
  scrollToField: (fieldPath: string) => void;
}

export interface DocumentViewerProps {
  fileUrl: string;
  fields: FieldConfidence[];
  threshold: number | null;
  hoveredFieldPath: string | null;
  onHoverField: (fieldPath: string | null) => void;
  onClickField: (fieldPath: string) => void;
}

interface PositionedField {
  fieldPath: string;
  page: number;
  bbox: { x: number; y: number; w: number; h: number };
  belowThreshold: boolean;
  drawIndex: number;
}

function positionFields(fields: FieldConfidence[], threshold: number | null): PositionedField[] {
  const withBbox = fields
    .filter((field) => field.bbox !== null)
    .map((field) => ({
      fieldPath: field.fieldPath,
      page: field.bbox!.page,
      bbox: { x: field.bbox!.x, y: field.bbox!.y, w: field.bbox!.w, h: field.bbox!.h },
      belowThreshold: threshold !== null && Number(field.confidence) < threshold,
    }))
    // Reading order: top-to-bottom, left-to-right, page first.
    .sort((a, b) => a.page - b.page || a.bbox.y - b.bbox.y || a.bbox.x - b.bbox.x);

  return withBbox.map((field, index) => ({ ...field, drawIndex: index }));
}

export const DocumentViewer = forwardRef<DocumentViewerHandle, DocumentViewerProps>(
  function DocumentViewer(
    { fileUrl, fields, threshold, hoveredFieldPath, onHoverField, onClickField },
    ref,
  ) {
    const scrollRef = useRef<HTMLDivElement>(null);
    const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map());
    const [zoom, setZoom] = useState(1);
    const [pdfDoc, setPdfDoc] = useState<PDFDocumentProxy | null>(null);
    const [renderedPages, setRenderedPages] = useState<Set<number>>(new Set());
    const [imageLoaded, setImageLoaded] = useState(false);
    const isPdf = useMemo(() => isPdfPath(fileUrl), [fileUrl]);

    const markPageRendered = useCallback((pageNumber: number) => {
      setRenderedPages((prev) => new Set(prev).add(pageNumber));
    }, []);

    useEffect(() => {
      setRenderedPages(new Set());
      setImageLoaded(false);
      if (!isPdf) {
        setPdfDoc(null);
        return;
      }
      let cancelled = false;
      loadPdfDocument(fileUrl).then((doc) => {
        if (!cancelled) setPdfDoc(doc);
      });
      return () => {
        cancelled = true;
      };
    }, [fileUrl, isPdf]);

    const positioned = useMemo(() => positionFields(fields, threshold), [fields, threshold]);
    const fieldsByPage = useMemo(() => {
      const map = new Map<number, PositionedField[]>();
      for (const field of positioned) {
        const list = map.get(field.page) ?? [];
        list.push(field);
        map.set(field.page, list);
      }
      return map;
    }, [positioned]);

    useImperativeHandle(ref, () => ({
      scrollToField(fieldPath: string) {
        const field = positioned.find((f) => f.fieldPath === fieldPath);
        const scrollEl = scrollRef.current;
        const pageEl = field ? pageRefs.current.get(field.page) : undefined;
        if (!field || !scrollEl || !pageEl) return;

        const targetX = pageEl.offsetLeft + (field.bbox.x + field.bbox.w / 2) * pageEl.offsetWidth;
        const targetY = pageEl.offsetTop + (field.bbox.y + field.bbox.h / 2) * pageEl.offsetHeight;
        scrollEl.scrollTo({
          left: targetX - scrollEl.clientWidth / 2,
          top: targetY - scrollEl.clientHeight / 2,
          behavior: "smooth",
        });
      },
    }));

    const handleWheel = (event: WheelEvent<HTMLDivElement>) => {
      if (!event.ctrlKey && !event.metaKey) return; // trackpad pinch surfaces as wheel+ctrlKey
      event.preventDefault();
      setZoom((prev) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, prev - event.deltaY * 0.01)));
    };

    const registerPageRef = (pageNumber: number) => (el: HTMLDivElement | null) => {
      if (el) pageRefs.current.set(pageNumber, el);
      else pageRefs.current.delete(pageNumber);
    };

    return (
      <div className="relative flex h-full flex-col overflow-hidden rounded-lg border border-border bg-bg-raised">
        <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
          <span className="text-xs text-text-muted">Source document</span>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Zoom out"
              onClick={() => setZoom((z) => Math.max(MIN_ZOOM, z - ZOOM_STEP))}
            >
              <ZoomOutIcon />
            </Button>
            <span className="w-10 text-center font-mono text-xs tabular-nums text-text-muted">
              {Math.round(zoom * 100)}%
            </span>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Zoom in"
              onClick={() => setZoom((z) => Math.min(MAX_ZOOM, z + ZOOM_STEP))}
            >
              <ZoomInIcon />
            </Button>
          </div>
        </div>

        <div ref={scrollRef} onWheel={handleWheel} className="flex-1 overflow-auto p-6">
          <div
            className="flex flex-col items-center gap-4"
            style={{ transform: `scale(${zoom})`, transformOrigin: "top center" }}
          >
            {isPdf
              ? pdfDoc &&
                Array.from({ length: pdfDoc.numPages }, (_, i) => i + 1).map((pageNumber) => (
                  <div
                    key={pageNumber}
                    ref={registerPageRef(pageNumber)}
                    className="relative shadow-lg"
                  >
                    <PdfPage
                      doc={pdfDoc}
                      pageNumber={pageNumber}
                      scale={RENDER_SCALE}
                      onRendered={() => markPageRendered(pageNumber)}
                    />
                    {renderedPages.has(pageNumber) &&
                      (fieldsByPage.get(pageNumber) ?? []).map((field) => (
                        <FieldBox
                          key={field.fieldPath}
                          fieldPath={field.fieldPath}
                          bbox={field.bbox}
                          belowThreshold={field.belowThreshold}
                          drawIndex={field.drawIndex}
                          isHovered={hoveredFieldPath === field.fieldPath}
                          onHoverChange={onHoverField}
                          onClick={onClickField}
                        />
                      ))}
                  </div>
                ))
              : (
                  <div ref={registerPageRef(1)} className="relative shadow-lg">
                    {/* eslint-disable-next-line @next/next/no-img-element -- a signed, per-request URL, never a static asset Next's optimizer should cache */}
                    <img
                      src={fileUrl}
                      alt="Invoice source"
                      className="block max-w-none"
                      onLoad={() => setImageLoaded(true)}
                    />
                    {imageLoaded &&
                      (fieldsByPage.get(1) ?? []).map((field) => (
                        <FieldBox
                          key={field.fieldPath}
                          fieldPath={field.fieldPath}
                          bbox={field.bbox}
                          belowThreshold={field.belowThreshold}
                          drawIndex={field.drawIndex}
                          isHovered={hoveredFieldPath === field.fieldPath}
                          onHoverChange={onHoverField}
                          onClick={onClickField}
                        />
                      ))}
                  </div>
                )}
          </div>
        </div>
      </div>
    );
  },
);

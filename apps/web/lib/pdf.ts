"use client";

import type { PDFDocumentProxy } from "pdfjs-dist";

let workerConfigured = false;

/**
 * pdfjs-dist's default build references browser-only globals (DOMMatrix)
 * at module-evaluation time, which crashes Next.js's server-side render of
 * this "use client" component (SSR still evaluates client components once
 * for the initial HTML). A dynamic import() defers loading the module
 * until this function actually runs -- which only ever happens inside a
 * useEffect (components/verify/document-viewer.tsx,
 * components/verify/pdf-page.tsx), i.e. only in the browser.
 */
async function loadPdfjs() {
  const pdfjs = await import("pdfjs-dist");
  if (!workerConfigured) {
    pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
    workerConfigured = true;
  }
  return pdfjs;
}

export async function loadPdfDocument(url: string): Promise<PDFDocumentProxy> {
  const pdfjs = await loadPdfjs();
  return pdfjs.getDocument({ url }).promise;
}

export function isPdfPath(urlOrPath: string): boolean {
  const withoutQuery = urlOrPath.split(/[?#]/, 1)[0] ?? urlOrPath;
  return withoutQuery.toLowerCase().endsWith(".pdf");
}

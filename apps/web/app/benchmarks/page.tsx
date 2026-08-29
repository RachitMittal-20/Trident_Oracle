"use client";

import { BenchmarksPageClient } from "@/components/benchmarks/benchmarks-page-client";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function BenchmarksPage() {
  return <BenchmarksPageClient apiBaseUrl={API_BASE_URL} />;
}

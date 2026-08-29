"use client";

import { useEffect, useState } from "react";

import { AutoPostTrendChart } from "@/components/analytics/auto-post-trend-chart";
import { ConfidenceHistogram } from "@/components/analytics/confidence-histogram";
import { DeliveryHealthPanel } from "@/components/analytics/delivery-health-panel";
import { ExceptionsByTypeChart } from "@/components/analytics/exceptions-by-type-chart";
import { LatencyChart } from "@/components/analytics/latency-chart";
import { SummaryCards } from "@/components/analytics/summary-cards";
import { VendorTable } from "@/components/analytics/vendor-table";
import { VolumeChart } from "@/components/analytics/volume-chart";
import { ErrorState, LoadingState } from "@/components/states";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  AnalyticsApiError,
  fetchAutoPostTrend,
  fetchConfidenceDistribution,
  fetchDeliveryHealth,
  fetchExceptionsByType,
  fetchLatency,
  fetchSummary,
  fetchVendors,
  fetchVolumeOverTime,
  type AnalyticsSummary,
  type AutoPostTrendPoint,
  type ConfidenceBucket,
  type DeliveryHealth,
  type ExceptionTypeCount,
  type LatencyResponse,
  type VendorAnalyticsRow,
  type VolumePoint,
} from "@/lib/analytics-api";

export interface AnalyticsPageClientProps {
  tenantId: string;
  apiBaseUrl: string;
}

interface Loaded {
  summary: AnalyticsSummary;
  volume: VolumePoint[];
  exceptionsByType: ExceptionTypeCount[];
  confidence: ConfidenceBucket[];
  latency: LatencyResponse;
  trend: AutoPostTrendPoint[];
  vendors: VendorAnalyticsRow[];
  deliveryHealth: DeliveryHealth;
}

export function AnalyticsPageClient({ tenantId, apiBaseUrl }: AnalyticsPageClientProps) {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setError(null);
    setData(null);
    Promise.all([
      fetchSummary(apiBaseUrl, tenantId, days),
      fetchVolumeOverTime(apiBaseUrl, tenantId, days),
      fetchExceptionsByType(apiBaseUrl, tenantId, days),
      fetchConfidenceDistribution(apiBaseUrl, tenantId, days),
      fetchLatency(apiBaseUrl, tenantId, days),
      fetchAutoPostTrend(apiBaseUrl, tenantId, days),
      fetchVendors(apiBaseUrl, tenantId),
      fetchDeliveryHealth(apiBaseUrl, tenantId),
    ])
      .then(([summary, volume, exceptionsByType, confidence, latency, trend, vendors, deliveryHealth]) => {
        setData({ summary, volume, exceptionsByType, confidence, latency, trend, vendors, deliveryHealth });
      })
      .catch((err: unknown) => {
        setError(err instanceof AnalyticsApiError ? err.message : "Could not load analytics.");
      });
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load is stable enough for this page's needs
  }, [apiBaseUrl, tenantId, days]);

  if (error) {
    return (
      <div className="p-10">
        <ErrorState description={error} onRetry={load} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-text-primary">Analytics</h1>
        <Select value={String(days)} onValueChange={(v) => v && setDays(Number(v))}>
          <SelectTrigger className="w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="7">Last 7 days</SelectItem>
            <SelectItem value="30">Last 30 days</SelectItem>
            <SelectItem value="90">Last 90 days</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {data === null ? (
        <LoadingState rows={6} />
      ) : (
        <>
          <SummaryCards summary={data.summary} />

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <VolumeChart data={data.volume} />
            <ExceptionsByTypeChart data={data.exceptionsByType} />
            <ConfidenceHistogram data={data.confidence} />
            <LatencyChart data={data.latency} />
            <AutoPostTrendChart data={data.trend} />
          </div>

          <VendorTable vendors={data.vendors} />
          <DeliveryHealthPanel health={data.deliveryHealth} />
        </>
      )}
    </div>
  );
}

import { diag, DiagLogLevel, SpanStatusCode, trace } from "@opentelemetry/api";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { FetchInstrumentation } from "@opentelemetry/instrumentation-fetch";
import { resourceFromAttributes } from "@opentelemetry/resources";
import { BatchSpanProcessor, WebTracerProvider } from "@opentelemetry/sdk-trace-web";

export function allowedStatusURL(url: string, origin: string): boolean {
  const parsed = new URL(url, origin);
  return parsed.origin === origin && parsed.search === "" && parsed.hash === "" &&
    /^\/api\/(healthz|readyz|version)$/.test(parsed.pathname);
}

export async function configureTelemetry(version = "dev"): Promise<void> {
  try {
    const response = await fetch("/telemetry/config", { cache: "no-store", signal: AbortSignal.timeout(2000) });
    if (!response.ok) throw new Error("telemetry configuration unavailable");
    const config: unknown = await response.json();
    if (typeof config !== "object" || config === null ||
      !("enabled" in config) || typeof config.enabled !== "boolean") {
      throw new Error("invalid telemetry configuration");
    }
    if (!config.enabled) return;
    diag.setLogger({
      error: () => console.warn("telemetry_export_failed"),
      warn: () => console.warn("telemetry_warning"),
      info: () => {}, debug: () => {}, verbose: () => {},
    }, DiagLogLevel.WARN);
    const provider = new WebTracerProvider({
      resource: resourceFromAttributes({
        "service.name": "web-portal-browser",
        "service.version": version,
        "deployment.environment.name": "development",
      }),
      spanProcessors: [{
        onStart: () => {},
        onEnd: span => {
          for (const key of Object.keys(span.attributes)) {
            if (!["http.method", "http.status_code", "http.request.method", "http.response.status_code"].includes(key)) {
              delete span.attributes[key];
            }
          }
        },
        forceFlush: async () => {},
        shutdown: async () => {},
      }, new BatchSpanProcessor(new OTLPTraceExporter({
        url: `${location.origin}/telemetry/v1/traces`,
        timeoutMillis: 2000,
      }), { maxQueueSize: 128, maxExportBatchSize: 16, scheduledDelayMillis: 1000 })],
    });
    provider.register();
    const instrumentation = new FetchInstrumentation({
      ignoreUrls: [new RegExp(`^(?!${location.origin.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\/api\\/(?:healthz|readyz|version)$)`)],
      propagateTraceHeaderCorsUrls: [location.origin],
      clearTimingResources: true,
      ignoreNetworkEvents: true,
    });
    instrumentation.setTracerProvider(provider);
    instrumentation.enable();
    window.addEventListener("pagehide", () => {
      void provider.forceFlush().catch(() => console.warn("telemetry_export_failed"));
    });
  } catch {
    console.warn("telemetry_configuration_failed");
  }
}

export function statusInteraction<T>(operation: () => Promise<T>): Promise<T> {
  return trace.getTracer("rpp/portal").startActiveSpan("portal.status.refresh", async span => {
    try {
      return await operation();
    } catch (error) {
      span.setStatus({ code: SpanStatusCode.ERROR, message: "status_failed" });
      throw error;
    } finally {
      span.end();
    }
  });
}

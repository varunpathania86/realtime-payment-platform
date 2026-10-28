export interface PlatformStatus {
  health: "ok";
  ready: "ok";
  version: string;
  commit: string;
}

export type StatusState =
  | { kind: "loading" }
  | { kind: "success"; data: PlatformStatus }
  | { kind: "error"; message: string };

type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

async function request(
  fetcher: Fetcher,
  path: string,
  signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  const response = await fetcher(path, {
    signal,
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`${path} returned HTTP ${response.status}`);
  }
  const body: unknown = await response.json();
  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    throw new Error(`${path} returned an invalid response`);
  }
  return body as Record<string, unknown>;
}

export async function loadStatus(
  fetcher: Fetcher = fetch,
  signal?: AbortSignal,
): Promise<PlatformStatus> {
  const [health, ready, version] = await Promise.all([
    request(fetcher, "/api/healthz", signal),
    request(fetcher, "/api/readyz", signal),
    request(fetcher, "/api/version", signal),
  ]);
  if (health.status !== "ok" || ready.status !== "ok") {
    throw new Error("The API is not healthy and ready");
  }
  if (
    typeof version.version !== "string" || !version.version ||
    typeof version.commit !== "string" || !version.commit
  ) {
    throw new Error("The API returned invalid build metadata");
  }
  return { health: "ok", ready: "ok", version: version.version, commit: version.commit };
}

export interface DashboardAccess {
  dashboardId: string;
  supersetDomain: string;
}

export interface PortalSession {
  username: string;
  permissions: string[];
  bi?: DashboardAccess;
}

type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

// Cookies are HttpOnly and SameSite=Strict; the server also checks Origin on every POST.
const base: RequestInit = { cache: "no-store", credentials: "same-origin" };

function parseSession(body: unknown): PortalSession {
  const value = body as Partial<PortalSession> | null;
  if (
    typeof value !== "object" || value === null || typeof value.username !== "string" ||
    !Array.isArray(value.permissions)
  ) {
    throw new Error("The portal returned an invalid session");
  }
  const bi = value.bi;
  if (bi !== undefined && (typeof bi?.dashboardId !== "string" || typeof bi?.supersetDomain !== "string")) {
    throw new Error("The portal returned invalid dashboard settings");
  }
  return { username: value.username, permissions: value.permissions.map(String), bi };
}

export async function loadSession(fetcher: Fetcher = fetch, signal?: AbortSignal): Promise<PortalSession | null> {
  const response = await fetcher("/auth/session", { ...base, signal, headers: { Accept: "application/json" } });
  if (response.status === 401 || response.status === 404) return null;
  if (!response.ok) throw new Error(`Session check returned HTTP ${response.status}`);
  return parseSession(await response.json());
}

export async function signIn(username: string, password: string, fetcher: Fetcher = fetch): Promise<PortalSession> {
  const response = await fetcher("/auth/login", {
    ...base,
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (response.status === 401) throw new Error("Invalid username or password");
  if (!response.ok) throw new Error(`Sign-in returned HTTP ${response.status}`);
  return parseSession(await response.json());
}

export async function signOut(fetcher: Fetcher = fetch): Promise<void> {
  const response = await fetcher("/auth/logout", { ...base, method: "POST" });
  if (!response.ok) throw new Error(`Sign-out returned HTTP ${response.status}`);
}

export async function fetchGuestToken(fetcher: Fetcher = fetch): Promise<string> {
  const response = await fetcher("/bi/guest-token", { ...base, method: "POST", headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`Dashboard access returned HTTP ${response.status}`);
  const body = (await response.json()) as { token?: unknown };
  if (typeof body?.token !== "string" || !body.token) throw new Error("Dashboard access returned no token");
  return body.token;
}

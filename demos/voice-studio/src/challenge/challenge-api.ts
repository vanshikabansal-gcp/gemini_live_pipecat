/** HTTP calls for the Abhay challenge. Same-origin only: the server sends no
 *  CORS headers in challenge mode. */
import {
  parseServerMessage,
  type ChallengeConfig,
  type ChallengeResultMessage,
  type Leaderboard,
} from "./challenge-logic.ts";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type RequestOptions = {
  method?: "GET" | "POST";
  body?: unknown;
  headers?: Record<string, string>;
  timeoutMs?: number;
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? 15000);
  const headers: Record<string, string> = { ...(options.headers || {}) };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  let response: Response;
  try {
    response = await fetch(path, {
      method: options.method || "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
    });
  } catch (error) {
    const aborted = error instanceof DOMException && error.name === "AbortError";
    throw new ApiError(0, aborted ? "The server took too long to respond. Try again." : "Can't reach the server. Check your connection.");
  } finally {
    clearTimeout(timer);
  }
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload && typeof (payload as { detail?: unknown }).detail === "string"
      ? (payload as { detail: string }).detail
      : `Something went wrong (HTTP ${response.status}).`;
    throw new ApiError(response.status, detail);
  }
  return payload as T;
}

export function getConfig(): Promise<ChallengeConfig> {
  return request<ChallengeConfig>("/api/challenge/config");
}

export function getLeaderboard(limit: number, adminToken?: string | null): Promise<Leaderboard> {
  const headers: Record<string, string> = adminToken ? { "X-Admin-Token": adminToken } : {};
  return request<Leaderboard>(`/api/challenge/leaderboard?limit=${encodeURIComponent(String(limit))}`, { headers });
}

export type RoundTicket = {
  wsUrl: string;
  sessionId: string;
  sessionToken: string;
  durationS: number;
  player: string;
};

/** The socket must stay on this page's host: never follow a URL elsewhere. */
export function validateRoundSocketUrl(value: unknown, page: { protocol: string; host: string }): string {
  if (typeof value !== "string") throw new ApiError(0, "The server sent an invalid connection address.");
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new ApiError(0, "The server sent an invalid connection address.");
  }
  const expected = page.protocol === "https:" ? ["wss:"] : ["ws:", "wss:"];
  if (!expected.includes(url.protocol) || url.host !== page.host) {
    throw new ApiError(0, "The server sent an unexpected connection address.");
  }
  return url.toString();
}

export async function startRound(playerId: string, language: string): Promise<RoundTicket> {
  const body = await request<{
    ws_url?: unknown; session_id?: unknown; session_token?: unknown; duration_s?: unknown; player?: unknown;
  }>("/connect", { method: "POST", body: { player_id: playerId, language }, timeoutMs: 20000 });
  if (typeof body.session_id !== "string" || typeof body.session_token !== "string") {
    throw new ApiError(0, "The server sent an incomplete response. Try again.");
  }
  return {
    wsUrl: validateRoundSocketUrl(body.ws_url, window.location),
    sessionId: body.session_id,
    sessionToken: body.session_token,
    durationS: typeof body.duration_s === "number" ? body.duration_s : 120,
    player: typeof body.player === "string" ? body.player : "",
  };
}

/** End the round now (or fetch the score of one that already ended). */
export async function finishRound(sessionId: string, sessionToken: string): Promise<ChallengeResultMessage> {
  const body = await request<{ result?: Record<string, unknown> }>("/api/challenge/finish", {
    method: "POST",
    body: { session_id: sessionId },
    headers: { "X-Session-Token": sessionToken },
  });
  const parsed = parseServerMessage({ ...(body.result || {}), type: "challenge_result" });
  if (parsed.type !== "challenge_result") throw new ApiError(0, "No result for this round yet.");
  return parsed;
}

export type AdminSession = { token: string; expiresAtMs: number; revealTopN: number };

export async function adminLogin(password: string): Promise<AdminSession> {
  const body = await request<{ token?: unknown; expires_at_ms?: unknown; reveal_top_n?: unknown }>(
    "/api/challenge/admin/login",
    { method: "POST", body: { password } },
  );
  if (typeof body.token !== "string" || typeof body.expires_at_ms !== "number") {
    throw new ApiError(0, "The server sent an incomplete response.");
  }
  return {
    token: body.token,
    expiresAtMs: body.expires_at_ms,
    revealTopN: typeof body.reveal_top_n === "number" ? body.reveal_top_n : 3,
  };
}

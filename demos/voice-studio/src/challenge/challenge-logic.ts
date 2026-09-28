/**
 * Pure helpers for the Abhay negotiation challenge UI.
 *
 * No React, no transport and no persona data: nothing here may ever know
 * Abhay's floor price. Kept dependency-free so it runs under
 * `node --experimental-strip-types --test`.
 */

export const PLAYER_ID_LENGTH = 8;

/** Prices are Indian rupees and Abhay speaks in lakh, so grouping is en-IN
 *  (₹14,50,000) on purpose rather than the browser's default locale. */
const INR = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const COUNT = new Intl.NumberFormat("en-IN");
const PLURALS = new Intl.PluralRules("en-IN");

export type ChallengeLanguage = { code: string; label: string };

export type ChallengeConfig = {
  duration_s: number;
  id_length: number;
  languages: ChallengeLanguage[];
  default_language: string;
  admin_enabled: boolean;
  reveal_top_n: number;
  leaderboard_backend: string;
};

export type LeaderboardEntry = {
  rank: number;
  player: string;
  price_inr: number;
  extras_value_inr: number;
  sold: boolean;
  attempts: number;
  achieved_at_ms: number;
  /** Only present for the top winners, and only with a valid organizer token. */
  player_id?: string;
};

export type Leaderboard = {
  entries: LeaderboardEntry[];
  total_players: number;
  revealed: boolean;
  reveal_top_n: number;
  stale: boolean;
  server_time_ms: number;
};

export type ChallengeStateMessage = {
  type: "challenge_state";
  status: string;
  duration_ms: number;
  remaining_ms: number;
  player: string;
};

export type NotRecordedReason = "no_speech" | "no_price" | "storage_error" | "already_played" | null;
const NOT_RECORDED_REASONS: readonly string[] = ["no_speech", "no_price", "storage_error", "already_played"];

export type ChallengeResultMessage = {
  type: "challenge_result";
  reason: "time_up" | "ended_by_player" | "disconnected" | string;
  player: string;
  price_inr: number;
  extras_value_inr: number;
  sold: boolean;
  recorded: boolean;
  not_recorded_reason: NotRecordedReason;
  /** The round did not count and the ID is free again, so offer "Start again". */
  can_retry: boolean;
  rank: number | null;
  total_players: number | null;
};

/** Why the server refused to start a round for this ID (sent as a fatal error). */
export type RefusalCode = "already_played" | "in_progress" | "unavailable";
const REFUSAL_CODES: readonly string[] = ["already_played", "in_progress", "unavailable"];

export type DealExtra = { key: string; label: string; value_inr: number };

export type DealStateMessage = {
  type: "deal_state";
  cash_price: number;
  extras_value: number;
  extras: DealExtra[];
  sold: boolean;
};

export type ServerEvent =
  | ChallengeStateMessage
  | ChallengeResultMessage
  | DealStateMessage
  | { type: "transcript"; role: "user" | "assistant"; text: string }
  | { type: "partial_user"; text: string }
  | { type: "interruption" }
  | { type: "turn_complete" }
  | { type: "error"; fatal: boolean; code: RefusalCode | null }
  | { type: "ignored" };

/**
 * Keep only ASCII letters and digits, uppercased, at most eight. Pasted
 * "ab12 cd34" becomes "AB12CD34". IDs are case-insensitive (the server
 * uppercases too), so changing case never makes a new ID. Filtering runs
 * before uppercasing so a non-ASCII letter can never turn into an ASCII one.
 */
export function sanitizePlayerIdInput(raw: string): string {
  return (raw || "").replace(/[^A-Za-z0-9]/g, "").toUpperCase().slice(0, PLAYER_ID_LENGTH);
}

export function isValidPlayerId(value: string): boolean {
  return new RegExp(`^[A-Z0-9]{${PLAYER_ID_LENGTH}}$`).test(value);
}

/** `AB12CD34` -> `****CD34`: the only form of an ID the UI shows once a round starts. */
export function maskPlayerId(value: string): string {
  return `****${value.slice(-4)}`;
}

export function formatInr(amount: number): string {
  return INR.format(Math.round(Number.isFinite(amount) ? amount : 0));
}

export function formatCount(value: number): string {
  return COUNT.format(value);
}

export function playersLabel(count: number): string {
  return PLURALS.select(count) === "one" ? `${formatCount(count)} player` : `${formatCount(count)} players`;
}

/** 119_400 ms -> "2:00" (rounds up, so the clock never shows 0:00 early). */
export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

/** Decode one RTVI message from the bot into a typed event. Unknown shapes
 *  are ignored rather than trusted. */
export function parseServerMessage(message: unknown): ServerEvent {
  if (!message || typeof message !== "object") return { type: "ignored" };
  const outer = message as { type?: unknown; data?: unknown };
  const body = (outer.type === "server-message" ? outer.data : message) as Record<string, unknown> | null;
  if (!body || typeof body !== "object") return { type: "ignored" };
  const type = body.type;

  if (type === "challenge_state") {
    return {
      type,
      status: str(body.status, "running"),
      duration_ms: num(body.duration_ms),
      remaining_ms: num(body.remaining_ms),
      player: str(body.player),
    };
  }
  if (type === "challenge_result") {
    const reason = body.not_recorded_reason;
    const recorded = body.recorded === true;
    return {
      type,
      reason: str(body.reason, "time_up"),
      player: str(body.player),
      price_inr: num(body.price_inr),
      extras_value_inr: num(body.extras_value_inr),
      sold: body.sold === true,
      recorded,
      not_recorded_reason:
        typeof reason === "string" && NOT_RECORDED_REASONS.includes(reason) ? (reason as NotRecordedReason) : null,
      // A scored round used up its ID, whatever else the message says.
      can_retry: !recorded && body.can_retry === true,
      rank: typeof body.rank === "number" ? body.rank : null,
      total_players: typeof body.total_players === "number" ? body.total_players : null,
    };
  }
  if (type === "deal_state") {
    const extras = Array.isArray(body.extras) ? body.extras : [];
    return {
      type,
      cash_price: num(body.cash_price),
      extras_value: num(body.extras_value),
      extras: extras
        .filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
        .map(item => ({ key: str(item.key), label: str(item.label), value_inr: num(item.value_inr) })),
      sold: body.sold === true,
    };
  }
  if (type === "transcription") {
    const text = str(body.text);
    if (!text) return { type: "partial_user", text: "" };
    const role = str(body.participant).toLowerCase() === "user" ? "user" : "assistant";
    return { type: "transcript", role, text };
  }
  if (type === "interim_transcription" || type === "interim_input_transcription") {
    return { type: "partial_user", text: str(body.text) };
  }
  if (type === "metrics") {
    const payload = body.payload as { type?: unknown } | undefined;
    if (payload?.type === "interruption") return { type: "interruption" };
    if (payload?.type === "turn_complete") return { type: "turn_complete" };
    return { type: "ignored" };
  }
  if (type === "error" || outer.type === "error") {
    const data = (outer.type === "error" ? outer.data : body) as { fatal?: unknown; code?: unknown } | undefined;
    const code = data?.code;
    return {
      type: "error",
      fatal: data?.fatal === true,
      code: typeof code === "string" && REFUSAL_CODES.includes(code) ? (code as RefusalCode) : null,
    };
  }
  return { type: "ignored" };
}

export function resultHeadline(result: ChallengeResultMessage): string {
  if (result.reason === "time_up") return "Time's up!";
  if (result.reason === "ended_by_player") return "Round over";
  return "Call ended";
}

/** Why a round was not put on the board, in words a player understands. */
export function notRecordedMessage(reason: NotRecordedReason): string | null {
  if (reason === "no_speech") {
    return "We didn't hear you, so this round doesn't count and your ID is still unused. Check your microphone and start again.";
  }
  if (reason === "no_price") return "Abhay never named a price, so this round doesn't count. Your ID is still unused.";
  if (reason === "storage_error") return "We couldn't save this score. Start again, or tell the organizer if it keeps happening.";
  if (reason === "already_played") return "This ID already has a score on the board. Each ID gets one round.";
  return null;
}

/** Why the server would not start a round, mirroring the server's own wording. */
export function refusalMessage(code: RefusalCode): string {
  if (code === "already_played") return "This ID has already played. Each ID gets one round.";
  if (code === "in_progress") return "This ID is already in a round. Try again in a few minutes.";
  return "The challenge is unavailable right now. Please try again in a minute.";
}

export function rankSummary(result: ChallengeResultMessage): string | null {
  if (!result.recorded || result.rank === null) return null;
  const total = result.total_players ?? result.rank;
  return `You're #${formatCount(result.rank)} of ${playersLabel(total)}`;
}

/** Board rows for this player's masked ID. Several players may share the last
 *  four characters, so this only highlights; it never identifies. */
export function isOwnRow(entry: LeaderboardEntry, maskedPlayer: string | null): boolean {
  return !!maskedPlayer && entry.player === maskedPlayer;
}

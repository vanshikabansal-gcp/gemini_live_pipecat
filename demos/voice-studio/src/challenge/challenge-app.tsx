import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  Crown,
  Eye,
  KeyRound,
  LoaderCircle,
  Lock,
  Maximize2,
  Mic,
  MicOff,
  Minimize2,
  Monitor,
  PhoneOff,
  RotateCcw,
  Target,
  Timer,
  Trash2,
  Trophy,
  UserRound,
  X,
} from "lucide-react";
import {
  adminLogin,
  ApiError,
  finishRound,
  getConfig,
  getLeaderboard,
  resetBoard,
  RESET_CONFIRM_WORD,
  startRound,
  type AdminSession,
  type RoundTicket,
} from "./challenge-api.ts";
import {
  formatCountdown,
  formatInr,
  isOwnRow,
  isValidPlayerId,
  maskPlayerId,
  notRecordedMessage,
  PLAYER_ID_LENGTH,
  playersLabel,
  rankSummary,
  refusalMessage,
  resultHeadline,
  sanitizePlayerIdInput,
  type ChallengeConfig,
  type ChallengeResultMessage,
  type DealStateMessage,
  type Leaderboard,
  type LeaderboardEntry,
} from "./challenge-logic.ts";
import { openChallengeSession, type ChallengeSession } from "./challenge-session.ts";

/** The organisers' public dare. Abhay's real limits stay on the server. */
const GOAL_PRICE_LABEL = "₹14.5 lakh";

const DEFAULT_CONFIG: ChallengeConfig = {
  duration_s: 120,
  id_length: PLAYER_ID_LENGTH,
  languages: [{ code: "hi-IN", label: "Hinglish" }, { code: "en-IN", label: "English" }],
  default_language: "hi-IN",
  admin_enabled: false,
  reveal_top_n: 3,
  leaderboard_backend: "firestore",
};

const BOARD_ROWS = 10;
const BOARD_POLL_MS = 5000;
/** How long to wait for the server's pushed result before asking over HTTP. */
const RESULT_WAIT_MS = 5000;
/** The server starts the clock once the call is up; give up if it never does. */
const START_WATCHDOG_MS = 25000;

type Stage = "entry" | "connecting" | "live" | "finishing" | "result";
type Line = { id: number; role: "user" | "assistant"; text: string };

function errorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError || error instanceof Error) return error.message || fallback;
  return fallback;
}

// ---------------------------------------------------------------------------
// Leaderboard data
// ---------------------------------------------------------------------------

function useLeaderboard(adminToken: string | null, onAdminRejected: () => void) {
  const [board, setBoard] = useState<Leaderboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const tokenRef = useRef(adminToken);
  tokenRef.current = adminToken;
  const rejectRef = useRef(onAdminRejected);
  rejectRef.current = onAdminRejected;
  const inFlight = useRef(false);
  const again = useRef(false);
  const run = useRef<() => Promise<void>>(async () => undefined);

  run.current = async () => {
    if (inFlight.current) {
      // e.g. the organizer just signed in mid-poll: fetch again right after.
      again.current = true;
      return;
    }
    inFlight.current = true;
    const token = tokenRef.current;
    try {
      const next = await getLeaderboard(BOARD_ROWS, token);
      // Fetched with a token that has since changed: never show it.
      if (token === tokenRef.current) {
        setBoard(next);
        setError(null);
      } else {
        again.current = true;
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401 && token) {
        rejectRef.current();
      } else {
        setError(errorText(err, "Leaderboard unavailable"));
      }
    } finally {
      inFlight.current = false;
      if (again.current) {
        again.current = false;
        void run.current();
      }
    }
  };

  const refresh = useCallback(() => run.current(), []);

  useEffect(() => {
    if (!adminToken) {
      // "Hide IDs" takes effect at once, not on the next poll.
      setBoard(current => current && {
        ...current,
        revealed: false,
        entries: current.entries.map(({ player_id: _hidden, ...rest }) => rest),
      });
    }
    void refresh();
  }, [adminToken, refresh]);

  useEffect(() => {
    const tick = () => { if (document.visibilityState === "visible") void refresh(); };
    const timer = setInterval(tick, BOARD_POLL_MS);
    document.addEventListener("visibilitychange", tick);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
  }, [refresh]);

  return { board, error, refresh };
}

// ---------------------------------------------------------------------------
// Leaderboard panel (normal, expanded overlay, or real full screen)
// ---------------------------------------------------------------------------

type BoardProps = {
  board: Leaderboard | null;
  error: string | null;
  config: ChallengeConfig;
  maskedPlayer: string | null;
  admin: AdminSession | null;
  onAdmin: (session: AdminSession | null) => void;
  onBoardReset: () => void;
  standalone?: boolean;
};

function LeaderboardPanel({ board, error, config, maskedPlayer, admin, onAdmin, onBoardReset, standalone }: BoardProps) {
  const panelRef = useRef<HTMLElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [organizerOpen, setOrganizerOpen] = useState(false);

  useEffect(() => {
    const onChange = () => { if (!document.fullscreenElement) setExpanded(false); };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !document.fullscreenElement) setExpanded(false);
    };
    document.addEventListener("fullscreenchange", onChange);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("fullscreenchange", onChange);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  const toggleExpanded = () => {
    if (expanded) {
      setExpanded(false);
      if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
      return;
    }
    setExpanded(true);
    // Real full screen where the browser allows it; the CSS overlay covers
    // browsers (such as iPhone Safari) that do not.
    const element = panelRef.current;
    if (element && typeof element.requestFullscreen === "function" && !document.fullscreenElement) {
      void element.requestFullscreen().catch(() => undefined);
    }
  };

  const big = expanded || !!standalone;
  const entries = board?.entries ?? [];
  const revealed = !!board?.revealed;

  return (
    <aside
      ref={panelRef}
      className={`ch-board${expanded ? " is-expanded" : ""}${standalone ? " is-standalone" : ""}`}
      aria-label="Leaderboard"
    >
      <header className="ch-board-head">
        <div className="ch-board-title">
          <Trophy aria-hidden="true" />
          <div>
            <h2>Leaderboard</h2>
            <p>{board ? `${playersLabel(board.total_players)} · goal: below ${GOAL_PRICE_LABEL} · lowest price wins` : `Goal: below ${GOAL_PRICE_LABEL} · lowest price wins`}</p>
          </div>
        </div>
        <div className="ch-board-actions">
          {config.admin_enabled && (
            <button
              type="button"
              className={`ch-icon-btn${admin ? " is-active" : ""}`}
              onClick={() => setOrganizerOpen(true)}
              aria-label={admin ? "Organizer view is on" : "Organizer: reveal winner IDs"}
              title={admin ? "Organizer view is on" : "Organizer: reveal winner IDs"}
            >
              {admin ? <Eye aria-hidden="true" /> : <Lock aria-hidden="true" />}
            </button>
          )}
          <button
            type="button"
            className="ch-icon-btn"
            onClick={toggleExpanded}
            aria-label={expanded ? "Exit full screen" : "Show leaderboard full screen"}
            title={expanded ? "Exit full screen" : "Full screen"}
          >
            {expanded ? <Minimize2 aria-hidden="true" /> : <Maximize2 aria-hidden="true" />}
          </button>
        </div>
      </header>

      {(revealed || admin) && (
        <div className="ch-reveal-banner" role="status">
          <Crown aria-hidden="true" />
          <span>Organizer view: full IDs shown for the top {board?.reveal_top_n ?? config.reveal_top_n}</span>
          <button type="button" onClick={() => setOrganizerOpen(true)}>Reset board</button>
          <button type="button" onClick={() => onAdmin(null)}>Hide IDs</button>
        </div>
      )}

      {error && !board && <p className="ch-board-empty">{error}</p>}
      {!error && !board && <p className="ch-board-empty"><LoaderCircle className="ch-spin" aria-hidden="true" /> Loading…</p>}
      {board && entries.length === 0 && (
        <p className="ch-board-empty">No scores yet. Be the first to beat Abhay!</p>
      )}

      {entries.length > 0 && (
        <ol className="ch-board-list">
          {entries.map(entry => (
            <BoardRow key={`${entry.rank}-${entry.player}`} entry={entry} own={isOwnRow(entry, maskedPlayer)} big={big} />
          ))}
        </ol>
      )}

      <footer className="ch-board-foot">
        <span>IDs show only the last 4 characters.</span>
        {board?.stale && <span className="ch-stale">Reconnecting…</span>}
      </footer>

      {organizerOpen && (
        <OrganizerDialog
          admin={admin}
          revealTopN={config.reveal_top_n}
          onClose={() => setOrganizerOpen(false)}
          onAdmin={session => { onAdmin(session); if (session) setOrganizerOpen(false); }}
          onBoardReset={onBoardReset}
        />
      )}
    </aside>
  );
}

function BoardRow({ entry, own, big }: { entry: LeaderboardEntry; own: boolean; big: boolean }) {
  const medal = entry.rank <= 3 ? ` is-rank-${entry.rank}` : "";
  return (
    <li className={`ch-row${medal}${own ? " is-own" : ""}${big ? " is-big" : ""}`}>
      <span className="ch-rank" aria-label={`Rank ${entry.rank}`}>{entry.rank}</span>
      <span className="ch-player">
        {entry.player_id ? (
          <>
            <span className="ch-player-full">{entry.player_id}</span>
            <span className="ch-winner-tag">Winner</span>
          </>
        ) : (
          <span className="ch-player-masked">{entry.player}</span>
        )}
        {own && <span className="ch-you-tag">You?</span>}
      </span>
      <span className="ch-price">
        {formatInr(entry.price_inr)}
        {entry.extras_value_inr > 0 && <small>+{formatInr(entry.extras_value_inr)} perks</small>}
      </span>
    </li>
  );
}

function OrganizerDialog({
  admin,
  revealTopN,
  onClose,
  onAdmin,
  onBoardReset,
}: {
  admin: AdminSession | null;
  revealTopN: number;
  onClose: () => void;
  onAdmin: (session: AdminSession | null) => void;
  onBoardReset: () => void;
}) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [confirmText, setConfirmText] = useState("");
  const [resetting, setResetting] = useState(false);
  const [resetNote, setResetNote] = useState<string | null>(null);

  useEffect(() => {
    if (!admin) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [admin]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError(null);
    try {
      onAdmin(await adminLogin(password));
      setPassword("");
    } catch (err) {
      setError(errorText(err, "Could not sign in"));
    } finally {
      setBusy(false);
    }
  };

  const reset = async (event: FormEvent) => {
    event.preventDefault();
    if (!admin || resetting || confirmText.trim() !== RESET_CONFIRM_WORD) return;
    setResetting(true);
    setError(null);
    setResetNote(null);
    try {
      const { removed } = await resetBoard(admin.token);
      setConfirmText("");
      setResetNote(`Board reset. ${removed === 1 ? "1 score" : `${removed} scores`} removed; every ID can play again.`);
      onBoardReset();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onAdmin(null);
      setError(errorText(err, "Could not reset the board"));
    } finally {
      setResetting(false);
    }
  };

  return (
    <div className="ch-dialog-backdrop" role="presentation" onClick={onClose}>
      <div
        className="ch-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ch-organizer-title"
        onClick={event => event.stopPropagation()}
      >
        <button type="button" className="ch-dialog-close" onClick={onClose} aria-label="Close">
          <X aria-hidden="true" />
        </button>
        <h3 id="ch-organizer-title"><KeyRound aria-hidden="true" /> Organizer</h3>
        {admin ? (
          <>
            <p>
              Full IDs are shown for the top {admin.revealTopN} on this screen only. This view locks again in{" "}
              {formatCountdown(Math.max(0, admin.expiresAtMs - now))}.
            </p>
            <div className="ch-dialog-actions">
              <button type="button" className="ch-btn ch-btn-secondary" onClick={() => { onAdmin(null); onClose(); }}>
                Hide IDs
              </button>
            </div>
            <form className="ch-danger-zone" onSubmit={reset}>
              <h4><Trash2 aria-hidden="true" /> Reset leaderboard</h4>
              <p>
                Deletes every score on the board and lets every ID play again. This cannot be undone.
                Type <strong>{RESET_CONFIRM_WORD}</strong> to confirm.
              </p>
              <input
                className="ch-input"
                aria-label={`Type ${RESET_CONFIRM_WORD} to confirm`}
                autoComplete="off"
                spellCheck={false}
                value={confirmText}
                maxLength={16}
                placeholder={RESET_CONFIRM_WORD}
                onChange={event => { setConfirmText(event.target.value.toUpperCase()); setResetNote(null); }}
              />
              {resetNote && <p className="ch-form-ok" role="status">{resetNote}</p>}
              {error && <p className="ch-form-error" role="alert">{error}</p>}
              <div className="ch-dialog-actions">
                <button
                  type="submit"
                  className="ch-btn ch-btn-danger"
                  disabled={resetting || confirmText.trim() !== RESET_CONFIRM_WORD}
                >
                  {resetting ? <LoaderCircle className="ch-spin" aria-hidden="true" /> : <Trash2 aria-hidden="true" />}
                  Reset board
                </button>
              </div>
            </form>
          </>
        ) : (
          <form onSubmit={submit}>
            <p>Enter the organizer password to reveal the full IDs of the top {revealTopN} players or reset the board.</p>
            <label className="ch-field-label" htmlFor="ch-organizer-password">Password</label>
            <input
              id="ch-organizer-password"
              className="ch-input"
              type="password"
              autoComplete="off"
              autoFocus
              value={password}
              maxLength={256}
              onChange={event => setPassword(event.target.value)}
            />
            {error && <p className="ch-form-error" role="alert">{error}</p>}
            <div className="ch-dialog-actions">
              <button type="submit" className="ch-btn ch-btn-primary" disabled={!password || busy}>
                {busy ? <LoaderCircle className="ch-spin" aria-hidden="true" /> : <Eye aria-hidden="true" />}
                Reveal winners
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The game
// ---------------------------------------------------------------------------

export default function ChallengeApp() {
  const standalone = useMemo(() => window.location.pathname.replace(/\/+$/, "") === "/board", []);
  const [config, setConfig] = useState<ChallengeConfig>(DEFAULT_CONFIG);
  const [admin, setAdmin] = useState<AdminSession | null>(null);

  useEffect(() => {
    let cancelled = false;
    getConfig()
      .then(value => { if (!cancelled) setConfig({ ...DEFAULT_CONFIG, ...value }); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  // Organizer access lives in memory only and locks itself at expiry.
  useEffect(() => {
    if (!admin) return;
    const timer = setTimeout(() => setAdmin(null), Math.max(0, admin.expiresAtMs - Date.now()));
    return () => clearTimeout(timer);
  }, [admin]);

  const dropAdmin = useCallback(() => setAdmin(null), []);
  const { board, error: boardError, refresh } = useLeaderboard(admin?.token ?? null, dropAdmin);

  if (standalone) {
    return (
      <div className="ch-shell is-board-page">
        <BrandHeader />
        <LeaderboardPanel
          board={board}
          error={boardError}
          config={config}
          maskedPlayer={null}
          admin={admin}
          onAdmin={setAdmin}
          onBoardReset={refresh}
          standalone
        />
      </div>
    );
  }

  return <GameScreen config={config} board={board} boardError={boardError} refreshBoard={refresh} admin={admin} setAdmin={setAdmin} />;
}

function BrandHeader({ children }: { children?: ReactNode }) {
  return (
    <header className="ch-header">
      <div className="ch-brand">
        <span className="ch-brand-mark" aria-hidden="true">₹</span>
        <span>Beat <strong>Abhay</strong></span>
        <span className="ch-tag">Negotiation challenge</span>
      </div>
      {children}
    </header>
  );
}

type GameProps = {
  config: ChallengeConfig;
  board: Leaderboard | null;
  boardError: string | null;
  refreshBoard: () => Promise<void>;
  admin: AdminSession | null;
  setAdmin: (session: AdminSession | null) => void;
};

function GameScreen({ config, board, boardError, refreshBoard, admin, setAdmin }: GameProps) {
  const durationMs = config.duration_s * 1000;
  const [playerId, setPlayerId] = useState("");
  const [language, setLanguage] = useState(config.default_language);
  const [stage, setStage] = useState<Stage>("entry");
  const [maskedPlayer, setMaskedPlayer] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deadline, setDeadline] = useState<number | null>(null);
  const [remainingMs, setRemainingMs] = useState(durationMs);
  const [roundDurationMs, setRoundDurationMs] = useState(durationMs);
  const [deal, setDeal] = useState<DealStateMessage | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [partialUser, setPartialUser] = useState("");
  const [botSpeaking, setBotSpeaking] = useState(false);
  const [micOn, setMicOn] = useState(true);
  const [result, setResult] = useState<ChallengeResultMessage | null>(null);
  const [resultNote, setResultNote] = useState<string | null>(null);

  const sessionRef = useRef<ChallengeSession | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const ticketRef = useRef<RoundTicket | null>(null);
  const resultRef = useRef<ChallengeResultMessage | null>(null);
  const stageRef = useRef<Stage>("entry");
  const fallbackTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const watchdogTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lineId = useRef(0);
  const roundId = useRef(0);
  // The start watchdog reads the latest deadline without re-arming.
  const deadlineRef = useRef<number | null>(null);
  deadlineRef.current = deadline;

  const moveTo = useCallback((next: Stage) => { stageRef.current = next; setStage(next); }, []);

  useEffect(() => {
    if (stageRef.current === "entry") setRemainingMs(durationMs);
  }, [durationMs]);
  useEffect(() => {
    setLanguage(current => (config.languages.some(item => item.code === current) ? current : config.default_language));
  }, [config]);

  const clearTimers = () => {
    if (fallbackTimer.current) clearTimeout(fallbackTimer.current);
    if (watchdogTimer.current) clearTimeout(watchdogTimer.current);
    fallbackTimer.current = null;
    watchdogTimer.current = null;
  };

  const hangUp = useCallback(async () => {
    const session = sessionRef.current;
    const context = contextRef.current;
    sessionRef.current = null;
    contextRef.current = null;
    try { await session?.disconnect(); } catch { /* Already closed. */ }
    try { if (context && context.state !== "closed") await context.close(); } catch { /* Already closed. */ }
    setBotSpeaking(false);
  }, []);

  const showResult = useCallback((value: ChallengeResultMessage | null, note: string | null) => {
    if (stageRef.current === "result") return;
    clearTimers();
    resultRef.current = value;
    setResult(value);
    setResultNote(note);
    setDeadline(null);
    moveTo("result");
    void hangUp();
    void refreshBoard();
  }, [hangUp, moveTo, refreshBoard]);

  /** Abandon the current call and show the form again with an explanation.
   *  Bumping the round makes every late event from that call a no-op. */
  const backToEntry = useCallback((message: string) => {
    roundId.current += 1;
    clearTimers();
    setDeadline(null);
    moveTo("entry");
    setError(message);
    void hangUp();
  }, [hangUp, moveTo]);

  /** Ask over HTTP for this round's score; used when the pushed result is late. */
  const fetchResult = useCallback(async (round: number) => {
    const ticket = ticketRef.current;
    if (!ticket || resultRef.current || round !== roundId.current) return;
    try {
      const value = await finishRound(ticket.sessionId, ticket.sessionToken);
      if (round === roundId.current) showResult(value, null);
    } catch {
      if (round !== roundId.current) return;
      if (stageRef.current === "live" && deadlineRef.current === null) {
        // The call ended before the clock started, so nothing was scored
        // and the server has already freed the ID.
        backToEntry("The call ended before your round started. Please try again.");
        return;
      }
      showResult(null, "The round has ended. If Abhay heard you, your score will appear on the leaderboard shortly.");
    }
  }, [backToEntry, showResult]);

  const awaitResult = useCallback((round: number) => {
    if (resultRef.current || stageRef.current === "result") return;
    moveTo("finishing");
    if (fallbackTimer.current) clearTimeout(fallbackTimer.current);
    fallbackTimer.current = setTimeout(() => { void fetchResult(round); }, RESULT_WAIT_MS);
  }, [fetchResult, moveTo]);

  // Local countdown, re-anchored by every challenge_state from the server.
  useEffect(() => {
    if (deadline === null) return;
    const round = roundId.current;
    const tick = () => {
      const left = Math.max(0, deadline - performance.now());
      setRemainingMs(left);
      if (left <= 0 && stageRef.current === "live") awaitResult(round);
    };
    tick();
    const timer = setInterval(tick, 250);
    return () => clearInterval(timer);
  }, [deadline, awaitResult]);

  // Leaving the page ends the call; the server scores it as "disconnected".
  useEffect(() => {
    const onLeave = () => { void sessionRef.current?.disconnect(); };
    window.addEventListener("pagehide", onLeave);
    return () => { window.removeEventListener("pagehide", onLeave); void hangUp(); };
  }, [hangUp]);

  const start = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!isValidPlayerId(playerId) || stageRef.current === "connecting") return;
    const round = ++roundId.current;
    clearTimers();
    setError(null);
    setResult(null);
    setResultNote(null);
    resultRef.current = null;
    setDeal(null);
    setLines([]);
    setPartialUser("");
    setMicOn(true);
    setDeadline(null);
    setRemainingMs(durationMs);
    setRoundDurationMs(durationMs);
    setMaskedPlayer(maskPlayerId(playerId));
    // Created inside the click so the browser lets it play Abhay's voice.
    const context = new AudioContext({ sampleRate: 24000 });
    void context.resume().catch(() => undefined);
    contextRef.current = context;
    moveTo("connecting");

    try {
      const ticket = await startRound(playerId, language);
      if (round !== roundId.current) return;
      ticketRef.current = ticket;
      if (ticket.player) setMaskedPlayer(ticket.player);
      setRoundDurationMs(ticket.durationS * 1000);
      setRemainingMs(ticket.durationS * 1000);

      const session = await openChallengeSession(ticket.wsUrl, context, {
        onState: state => {
          if (round !== roundId.current) return;
          if (watchdogTimer.current) { clearTimeout(watchdogTimer.current); watchdogTimer.current = null; }
          if (state.duration_ms > 0) setRoundDurationMs(state.duration_ms);
          setDeadline(performance.now() + state.remaining_ms);
        },
        onResult: value => { if (round === roundId.current) showResult(value, null); },
        onDeal: value => { if (round === roundId.current) setDeal(value); },
        onTranscript: (role, text, append) => {
          if (round !== roundId.current) return;
          setLines(current => {
            if (append && current.length && current[current.length - 1].role === role) {
              const last = current[current.length - 1];
              return [...current.slice(0, -1), { ...last, text: `${last.text} ${text}`.replace(/\s+/g, " ").trim() }];
            }
            return [...current, { id: ++lineId.current, role, text }].slice(-24);
          });
        },
        onPartialUser: text => { if (round === roundId.current) setPartialUser(text); },
        onBotSpeaking: speaking => { if (round === roundId.current) setBotSpeaking(speaking); },
        onLevel: () => undefined,
        onError: message => {
          // Once the form is showing again, its own message is the useful one.
          if (round === roundId.current && stageRef.current !== "entry") setError(message);
        },
        // The ID has already played, or another round holds it right now.
        onRefused: code => { if (round === roundId.current) backToEntry(refusalMessage(code)); },
        onDisconnected: () => {
          if (round !== roundId.current || resultRef.current) return;
          if (stageRef.current === "live" || stageRef.current === "finishing") void fetchResult(round);
        },
      });
      if (round !== roundId.current) { await session.disconnect(); return; }
      sessionRef.current = session;
      moveTo("live");
      watchdogTimer.current = setTimeout(() => {
        if (round !== roundId.current || stageRef.current !== "live" || deadlineRef.current !== null) return;
        backToEntry("Abhay didn't pick up. Please try again.");
      }, START_WATCHDOG_MS);
    } catch (err) {
      if (round !== roundId.current) return;
      await hangUp();
      moveTo("entry");
      setError(errorText(err, "Couldn't start the round. Please try again."));
    }
  };

  const endRound = () => {
    const round = roundId.current;
    sessionRef.current?.requestFinish();
    awaitResult(round);
  };

  const toggleMic = () => {
    const next = !micOn;
    setMicOn(next);
    sessionRef.current?.setMic(next);
  };

  /** Only offered when the round did not count, so the ID is still free. */
  const startAgain = () => { moveTo("entry"); setError(null); };
  const nextPlayer = () => { setPlayerId(""); setMaskedPlayer(null); moveTo("entry"); setError(null); };

  const idValid = isValidPlayerId(playerId);
  const low = remainingMs <= 20000;
  const critical = remainingMs <= 10000;
  const progress = roundDurationMs > 0 ? Math.max(0, Math.min(1, remainingMs / roundDurationMs)) : 0;

  return (
    <div className="ch-shell">
      <BrandHeader>
        <a className="ch-header-link" href="/board" target="_blank" rel="noopener">
          <Monitor aria-hidden="true" /> Board view
        </a>
      </BrandHeader>

      <main className="ch-main">
        <section className="ch-stage" aria-live="polite">
          {stage === "entry" && (
            <form className="ch-card ch-entry" onSubmit={start}>
              <div className="ch-entry-hero">
                <img src="/personas/abhay.webp" alt="" className="ch-avatar-lg" />
                <div>
                  <p className="ch-kicker">Can you out-haggle Delhi's toughest car dealer?</p>
                  <h1>Get Abhay below {GOAL_PRICE_LABEL} on the AeroNxt EV</h1>
                </div>
              </div>
              <ul className="ch-rules">
                <li><Target aria-hidden="true" /> <strong>Your goal: get the price below {GOAL_PRICE_LABEL}.</strong> Abhay opens at ₹20 lakh and never drops on the first ask. Keep pushing, and get creative.</li>
                <li><Timer aria-hidden="true" /> You get <strong>{formatCountdown(durationMs)}</strong> to negotiate by voice, for one car, in rupees.</li>
                <li><Trophy aria-hidden="true" /> When time's up, Abhay's price at that moment goes on the board. Lowest price wins; more perks breaks a tie.</li>
                <li><UserRound aria-hidden="true" /> Your ID shows as <strong>****</strong> plus its last 4 characters. Each ID gets one round, and its score is final.</li>
              </ul>

              <label className="ch-field-label" htmlFor="ch-player-id">Your {PLAYER_ID_LENGTH}-character ID</label>
              <input
                id="ch-player-id"
                className="ch-input ch-input-id"
                autoComplete="off"
                autoCapitalize="characters"
                autoCorrect="off"
                spellCheck={false}
                maxLength={PLAYER_ID_LENGTH}
                placeholder="AB12CD34"
                value={playerId}
                aria-describedby="ch-player-id-help"
                aria-invalid={playerId.length > 0 && !idValid}
                onChange={event => setPlayerId(sanitizePlayerIdInput(event.target.value))}
              />
              <p id="ch-player-id-help" className="ch-help">
                {idValid
                  ? <>You'll appear on the board as <strong>{maskPlayerId(playerId)}</strong>.</>
                  : `Letters and numbers only, exactly ${PLAYER_ID_LENGTH} characters (${playerId.length}/${PLAYER_ID_LENGTH}).`}
              </p>

              <fieldset className="ch-languages">
                <legend className="ch-field-label">Abhay speaks</legend>
                {config.languages.map(item => (
                  <label key={item.code} className={`ch-chip${language === item.code ? " is-selected" : ""}`}>
                    <input
                      type="radio"
                      name="ch-language"
                      value={item.code}
                      checked={language === item.code}
                      onChange={() => setLanguage(item.code)}
                    />
                    {item.label}
                  </label>
                ))}
              </fieldset>

              {error && <p className="ch-form-error" role="alert">{error}</p>}

              <button type="submit" className="ch-btn ch-btn-primary ch-btn-lg" disabled={!idValid}>
                <Mic aria-hidden="true" /> Start my {formatCountdown(durationMs)} round
              </button>
              <p className="ch-help">Your browser will ask to use the microphone. You get one round per ID, so make it count.</p>
            </form>
          )}

          {stage === "connecting" && (
            <div className="ch-card ch-center">
              <LoaderCircle className="ch-spin ch-spin-lg" aria-hidden="true" />
              <h2>Connecting you to the showroom…</h2>
              <p className="ch-help">Playing as {maskedPlayer}</p>
            </div>
          )}

          {(stage === "live" || stage === "finishing") && (
            <div className="ch-card ch-live">
              <div className="ch-live-top">
                <div
                  className={`ch-timer${low ? " is-low" : ""}${critical ? " is-critical" : ""}`}
                  role="timer"
                  aria-label={`Time left ${formatCountdown(remainingMs)}`}
                >
                  <Timer aria-hidden="true" />
                  <span>{deadline === null && stage === "live" ? formatCountdown(roundDurationMs) : formatCountdown(remainingMs)}</span>
                </div>
                <div className="ch-player-pill">Playing as <strong>{maskedPlayer}</strong></div>
              </div>
              <div className={`ch-progress${low ? " is-low" : ""}${critical ? " is-critical" : ""}`} aria-hidden="true">
                <span style={{ transform: `scaleX(${deadline === null ? 1 : progress})` }} />
              </div>

              <div className="ch-live-body">
                <div className={`ch-abhay${botSpeaking ? " is-speaking" : ""}`}>
                  <img src="/personas/abhay.webp" alt="Abhay, the car dealer" />
                  <p>
                    {stage === "finishing"
                      ? "Tallying the final price…"
                      : deadline === null
                        ? "Abhay is picking up…"
                        : botSpeaking ? "Abhay is talking…" : "Your turn: make your offer"}
                  </p>
                </div>

                <div className="ch-deal">
                  <p className="ch-deal-label">{deal?.sold ? "Deal closed at" : "Abhay's price"}</p>
                  <p className="ch-deal-price">{deal ? formatInr(deal.cash_price) : "—"}</p>
                  {deal?.sold && <p className="ch-sold">Sold! Keep talking or end the round.</p>}
                  {deal && deal.extras.length > 0 && (
                    <ul className="ch-perks" aria-label="Perks won">
                      {deal.extras.map(item => (
                        <li key={item.key}>{item.label} <span>+{formatInr(item.value_inr)}</span></li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>

              <div className="ch-transcript" aria-label="Conversation">
                {lines.length === 0 && !partialUser && <p className="ch-help">The conversation appears here.</p>}
                {lines.slice(-6).map(line => (
                  <p key={line.id} className={`ch-line is-${line.role}`}>
                    <span>{line.role === "user" ? "You" : "Abhay"}</span>{line.text}
                  </p>
                ))}
                {partialUser && <p className="ch-line is-user is-partial"><span>You</span>{partialUser}</p>}
              </div>

              {error && <p className="ch-form-error" role="alert">{error}</p>}

              <div className="ch-controls">
                <button
                  type="button"
                  className={`ch-btn ch-btn-secondary${micOn ? "" : " is-muted"}`}
                  onClick={toggleMic}
                  disabled={stage !== "live"}
                  aria-pressed={!micOn}
                >
                  {micOn ? <Mic aria-hidden="true" /> : <MicOff aria-hidden="true" />}
                  {micOn ? "Mute" : "Unmute"}
                </button>
                <button type="button" className="ch-btn ch-btn-danger" onClick={endRound} disabled={stage !== "live" || deadline === null}>
                  {stage === "finishing" ? <LoaderCircle className="ch-spin" aria-hidden="true" /> : <PhoneOff aria-hidden="true" />}
                  End round now
                </button>
              </div>
            </div>
          )}

          {stage === "result" && (
            <div className="ch-card ch-result">
              <p className="ch-kicker">{result ? resultHeadline(result) : "Round over"}</p>
              {result ? (
                <>
                  <p className="ch-result-label">{result.sold ? "You bought it for" : "Abhay's final price"}</p>
                  <p className="ch-result-price">{formatInr(result.price_inr)}</p>
                  {result.extras_value_inr > 0 && (
                    <p className="ch-result-perks">plus {formatInr(result.extras_value_inr)} in perks</p>
                  )}
                  {result.recorded ? (
                    <div className="ch-result-rank">
                      <Trophy aria-hidden="true" />
                      <div>
                        <p>{rankSummary(result) ?? "Your score is on the board."}</p>
                        <p className="ch-help">This is the final score for {result.player}. Each ID gets one round.</p>
                      </div>
                    </div>
                  ) : (
                    <p className="ch-form-error">{notRecordedMessage(result.not_recorded_reason)}</p>
                  )}
                </>
              ) : (
                <p className="ch-help">{resultNote}</p>
              )}
              <div className="ch-controls">
                {result?.can_retry && (
                  <button type="button" className="ch-btn ch-btn-primary" onClick={startAgain}>
                    <RotateCcw aria-hidden="true" /> Start again
                  </button>
                )}
                <button
                  type="button"
                  className={`ch-btn ${result?.can_retry ? "ch-btn-secondary" : "ch-btn-primary"}`}
                  onClick={nextPlayer}
                >
                  <UserRound aria-hidden="true" /> Next player
                </button>
              </div>
            </div>
          )}
        </section>

        <LeaderboardPanel
          board={board}
          error={boardError}
          config={config}
          maskedPlayer={maskedPlayer}
          admin={admin}
          onAdmin={setAdmin}
          onBoardReset={refreshBoard}
        />
      </main>
    </div>
  );
}

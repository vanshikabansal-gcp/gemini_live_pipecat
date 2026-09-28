/**
 * The voice call for one challenge round.
 *
 * Deliberately separate from Voice Studio's pipecat-session.ts: that module
 * imports the persona catalogue, whose prompts include Abhay's floor price,
 * and none of that may end up in the challenge bundle.
 */
import {
  parseServerMessage,
  type ChallengeResultMessage,
  type ChallengeStateMessage,
  type DealStateMessage,
  type RefusalCode,
} from "./challenge-logic.ts";

export type ChallengeSessionEvents = {
  onState: (state: ChallengeStateMessage) => void;
  onResult: (result: ChallengeResultMessage) => void;
  onDeal: (deal: DealStateMessage) => void;
  /** `append` continues Abhay's current reply rather than starting a new line. */
  onTranscript: (role: "user" | "assistant", text: string, append: boolean) => void;
  onPartialUser: (text: string) => void;
  onBotSpeaking: (speaking: boolean) => void;
  onLevel: (level: number) => void;
  onError: (message: string) => void;
  /** The server would not start a round for this ID. The call then closes,
   *  and onDisconnected is not sent. */
  onRefused: (code: RefusalCode) => void;
  onDisconnected: () => void;
};

export type ChallengeSession = {
  /** Ask the server to score the round now. The result arrives as onResult. */
  requestFinish: () => void;
  setMic: (enabled: boolean) => void;
  disconnect: () => Promise<void>;
};

const PLAYBACK_RATE = 24000;
/** The transport decodes each message asynchronously, so the server's close
 *  can overtake the message it sent just before closing (such as the reason a
 *  round was refused). Messages are still handled for this long after a close. */
export const CLOSE_GRACE_MS = 500;

export type TransportModules = {
  WebSocketTransport: typeof import("@pipecat-ai/websocket-transport").WebSocketTransport;
  DailyMediaManager: typeof import("@pipecat-ai/websocket-transport").DailyMediaManager;
  RTVIMessage: typeof import("@pipecat-ai/client-js").RTVIMessage;
};

/** Loaded on demand, so the entry page does not wait for the media stack. */
async function loadTransport(): Promise<TransportModules> {
  const [{ WebSocketTransport, DailyMediaManager }, { RTVIMessage }] = await Promise.all([
    import("@pipecat-ai/websocket-transport"),
    import("@pipecat-ai/client-js"),
  ]);
  return { WebSocketTransport, DailyMediaManager, RTVIMessage };
}

/**
 * Opens the call. `context` must be created (and resumed) inside the click
 * handler that started the round, so browsers allow it to play audio.
 * `load` exists for tests; the page always uses the real transport.
 */
export async function openChallengeSession(
  wsUrl: string,
  context: AudioContext,
  events: ChallengeSessionEvents,
  load: () => Promise<TransportModules> = loadTransport,
): Promise<ChallengeSession> {
  const { WebSocketTransport, DailyMediaManager, RTVIMessage } = await load();

  let stopped = false;
  let initialized = false;
  let assistantTurnOpen = false;
  let nextAudioAt = 0;
  let speakingTimer: ReturnType<typeof setTimeout> | null = null;
  let levelTimer: ReturnType<typeof setInterval> | null = null;
  const sources = new Set<AudioBufferSourceNode>();
  const analyser = context.createAnalyser();
  analyser.fftSize = 256;
  analyser.connect(context.destination);

  const stopPlayback = () => {
    for (const source of sources) {
      try { source.stop(); } catch { /* Already ended. */ }
      source.disconnect();
    }
    sources.clear();
    nextAudioAt = 0;
    if (speakingTimer) clearTimeout(speakingTimer);
    events.onBotSpeaking(false);
    events.onLevel(0);
  };

  // The stock WebSocket player gives no level signal; play the bot's PCM
  // through our own graph so the page can show when Abhay is talking.
  class ChallengeMediaManager extends DailyMediaManager {
    constructor() { super(false, true, undefined, undefined, 512, 16000, PLAYBACK_RATE); }

    bufferBotAudio(data: ArrayBuffer | Int16Array): Int16Array | undefined {
      if (stopped) return;
      const pcm = data instanceof Int16Array ? data : new Int16Array(data);
      if (!pcm.length) return pcm;
      const buffer = context.createBuffer(1, pcm.length, PLAYBACK_RATE);
      const channel = buffer.getChannelData(0);
      for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(analyser);
      sources.add(source);
      source.onended = () => { sources.delete(source); source.disconnect(); };
      nextAudioAt = Math.max(context.currentTime, nextAudioAt);
      source.start(nextAudioAt);
      nextAudioAt += buffer.duration;
      events.onBotSpeaking(true);
      if (speakingTimer) clearTimeout(speakingTimer);
      speakingTimer = setTimeout(() => {
        if (!stopped) { events.onBotSpeaking(false); events.onLevel(0); }
      }, Math.max(150, (nextAudioAt - context.currentTime) * 1000 + 100));
      return pcm;
    }

    async userStartedSpeaking() {
      stopPlayback();
    }
  }

  const media = new ChallengeMediaManager();
  const transport = new WebSocketTransport({ mediaManager: media, recorderSampleRate: 16000, playerSampleRate: PLAYBACK_RATE });

  let draining = false;
  let refused = false;
  let drainTimer: ReturnType<typeof setTimeout> | null = null;

  const teardown = async () => {
    if (stopped) return;
    stopped = true;
    if (levelTimer) clearInterval(levelTimer);
    stopPlayback();
    if (initialized) {
      try { transport.tracks().local?.audio?.stop(); } catch { /* Initialization may be incomplete. */ }
      try { await transport.disconnect(); } catch { /* Local media is released above. */ }
    }
    try { analyser.disconnect(); } catch { /* Already disconnected. */ }
  };

  /** The page is done with this call: no more events after this. */
  const disconnect = async () => {
    draining = false;
    if (drainTimer) { clearTimeout(drainTimer); drainTimer = null; }
    await teardown();
  };

  /** The server (or the network) ended the call. Stop the microphone and
   *  audio now, but report it only after messages already on the way in. */
  const closedRemotely = (dropped: boolean) => {
    if (stopped) return;
    draining = true;
    void teardown();
    drainTimer = setTimeout(() => {
      drainTimer = null;
      draining = false;
      if (refused) return;
      if (dropped) events.onError("The voice connection dropped.");
      events.onDisconnected();
    }, CLOSE_GRACE_MS);
  };

  transport.initialize({
    transport, enableMic: true, enableCam: false,
    callbacks: {
      onDisconnected: () => closedRemotely(false),
      onError: () => closedRemotely(true),
    },
  }, message => {
    if (stopped && !draining) return;
    const event = parseServerMessage(message);
    switch (event.type) {
      case "challenge_state":
        events.onState(event);
        break;
      case "challenge_result":
        events.onResult(event);
        break;
      case "deal_state":
        events.onDeal(event);
        break;
      case "transcript":
        if (event.role === "user") {
          assistantTurnOpen = false;
          events.onPartialUser("");
          events.onTranscript("user", event.text, false);
        } else {
          events.onTranscript("assistant", event.text, assistantTurnOpen);
          assistantTurnOpen = true;
        }
        break;
      case "partial_user":
        events.onPartialUser(event.text);
        break;
      case "interruption":
        assistantTurnOpen = false;
        stopPlayback();
        break;
      case "turn_complete":
        assistantTurnOpen = false;
        break;
      case "error":
        // Only the server's fatal errors end the call; RTVI also sends
        // harmless notices (for example, about our own control messages).
        if (!event.fatal) break;
        if (event.code) {
          refused = true;
          events.onRefused(event.code);
        } else {
          events.onError("The round could not continue. Please try again.");
        }
        break;
      default:
        break;
    }
  });

  initialized = true;
  try {
    await transport.initDevices();
  } catch {
    await disconnect();
    throw new Error("Microphone access failed. Allow microphone access in your browser, then try again.");
  }
  if (stopped) throw new Error("The round was cancelled.");

  const values = new Float32Array(analyser.fftSize);
  levelTimer = setInterval(() => {
    if (stopped) return;
    analyser.getFloatTimeDomainData(values);
    let sum = 0;
    for (const value of values) sum += value * value;
    events.onLevel(Math.min(1, Math.sqrt(sum / values.length) * 6));
  }, 100);

  try {
    await transport.connect({ wsUrl });
  } catch {
    // teardown, not disconnect: if the server closed on us, a refusal
    // reason may still be on its way in (see closedRemotely).
    await teardown();
    throw new Error("Couldn't reach Abhay's showroom. Check your connection and try again.");
  }
  if (stopped) {
    await transport.disconnect();
    throw new Error("The round was cancelled.");
  }
  transport.sendReadyMessage();
  transport.sendMessage(new RTVIMessage("start_trigger", {}));

  return {
    requestFinish() {
      if (!stopped) transport.sendMessage(new RTVIMessage("challenge_finish", {}));
    },
    setMic(enabled) {
      if (!stopped) transport.enableMic(enabled);
    },
    disconnect,
  };
}

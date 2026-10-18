/**
 * Realistic, continuous background ambience generator for natural voice conversations.
 *
 * Supports two persona-specific acoustic profiles:
 * - "call-center" (exclusively for Riya from Asian Paints BHPS):
 *   Streams `/ambience/call-center.mp3` — a real 20.0s seamless multi-speaker Indian
 *   Customer Call Centre loop synthesized from 6 Google Cloud `en-IN-Chirp3-HD` and
 *   `hi-IN-Chirp3-HD` voices with cubicle bandpass and room acoustics, cached across
 *   sessions and shaped through a warm 220–2200 Hz background filter.
 * - "vedic" (exclusively for Astrotalk Pandit Raghavendra Ji):
 *   Sacred Vedic consultation chamber featuring warm room tone, soothing 136.1 Hz
 *   (C# Om) Sa-Pa-Sa Tanpura string resonance, and soft distant temple bell (ghanti) chimes.
 */

export type AmbienceProfile = "call-center" | "vedic";

export type AmbientNoiseController = {
  setEnabled: (enabled: boolean) => void;
  setLevel: (level: number) => void;
  setSpeaking: (speaking: boolean) => void;
  stop: () => void;
};

const CALL_CENTER_MP3_URL = "/ambience/call-center.mp3";
let cachedCallCenterMp3Bytes: ArrayBuffer | null = null;
let inflightCallCenterFetch: Promise<ArrayBuffer | null> | null = null;
let cachedCallCenterDecoded: { sampleRate: number; buffer: AudioBuffer } | null = null;

function fetchCallCenterMp3Bytes(): Promise<ArrayBuffer | null> {
  if (cachedCallCenterMp3Bytes) {
    return Promise.resolve(cachedCallCenterMp3Bytes);
  }
  if (inflightCallCenterFetch) {
    return inflightCallCenterFetch;
  }
  if (typeof window === "undefined" || typeof fetch === "undefined") {
    return Promise.resolve(null);
  }
  inflightCallCenterFetch = fetch(CALL_CENTER_MP3_URL)
    .then((res) => (res.ok ? res.arrayBuffer() : null))
    .then((buf) => {
      if (buf && buf.byteLength > 0) {
        cachedCallCenterMp3Bytes = buf;
        return buf;
      }
      return null;
    })
    .catch(() => null)
    .finally(() => {
      inflightCallCenterFetch = null;
    });
  return inflightCallCenterFetch;
}

export function resolveAmbienceProfile(personaId?: string): AmbienceProfile | null {
  const id = (personaId || "").trim().toLowerCase();
  if (
    id === "astrotalk-panditji" ||
    id === "panditji" ||
    id === "astrotalk" ||
    id === "acharya" ||
    id === "jyotish"
  ) {
    return "vedic";
  }
  if (
    id === "asian-paints" ||
    id === "asian_paints" ||
    id === "bhps" ||
    id === "riya" ||
    id === "bajaj-bhps"
  ) {
    // Pre-warm the MP3 cache as soon as Riya's persona is resolved in the UI.
    void fetchCallCenterMp3Bytes();
    return "call-center";
  }
  return null;
}

type VirtualAgentSpec = {
  f0: number;          // Base fundamental pitch in Hz (male ~125-155 Hz, female ~185-235 Hz)
  f1Center: number;    // First vowel formant center (Hz)
  f2Center: number;    // Second vowel formant center (Hz)
  syllableRate: number;// Syllable modulation rate (Hz, ~4.0 - 5.6 Hz)
  phraseRate: number;  // Phrase on/off cycle rate (Hz)
  phaseOffset: number; // Phase offset (radians)
  dutyThreshold: number;// Controls how often this distant agent is speaking
  gain: number;        // Relative distance weight
};

const CALL_CENTER_AGENTS: VirtualAgentSpec[] = [
  // Distant Female Agent 1 (neighbouring bay)
  { f0: 205, f1Center: 580, f2Center: 1620, syllableRate: 4.8, phraseRate: 0.25, phaseOffset: 0.3, dutyThreshold: -0.15, gain: 1.0 },
  // Distant Male Agent 2 (across aisle)
  { f0: 132, f1Center: 490, f2Center: 1280, syllableRate: 4.3, phraseRate: 0.1667, phaseOffset: 1.9, dutyThreshold: -0.10, gain: 0.92 },
  // Distant Female Agent 3 (further down the floor)
  { f0: 224, f1Center: 620, f2Center: 1740, syllableRate: 5.2, phraseRate: 0.3333, phaseOffset: 3.6, dutyThreshold: 0.0, gain: 0.78 },
  // Distant Male Agent 4 (neighbouring row)
  { f0: 148, f1Center: 520, f2Center: 1380, syllableRate: 4.6, phraseRate: 0.25, phaseOffset: 4.8, dutyThreshold: -0.05, gain: 0.85 },
  // Distant Female Agent 5 (background floor)
  { f0: 192, f1Center: 550, f2Center: 1520, syllableRate: 4.1, phraseRate: 0.1667, phaseOffset: 2.7, dutyThreshold: 0.10, gain: 0.70 },
];

/**
 * Build a normalized 12-second seamless loop buffer containing either:
 * - Authentic Customer Call Centre floor noise ("call-center"), or
 * - Sacred Vedic Jyotish chamber ambience ("vedic").
 */
function buildAmbienceBuffer(ctx: AudioContext, profile: AmbienceProfile): AudioBuffer {
  const sr = ctx.sampleRate;
  const durationSec = 12;
  const length = sr * durationSec;
  const buffer = ctx.createBuffer(1, length, sr);
  const out = buffer.getChannelData(0);

  // Pink/Brown noise filter state
  let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
  let brown = 0;
  let lpRoom = 0;

  let seed = profile === "vedic" ? 108108 : 550983;
  const nextRand = () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return (seed / 0xffffffff) * 2 - 1;
  };

  // Precompute typing keystroke timestamps for Call Centre CRM activity (in seconds)
  const keystrokeTimes: number[] = [];
  if (profile === "call-center") {
    const typingWindows = [
      [0.8, 2.6],
      [4.2, 6.4],
      [8.1, 10.5],
    ];
    for (const [start, end] of typingWindows) {
      let cursor = start;
      while (cursor < end) {
        keystrokeTimes.push(cursor);
        cursor += 0.09 + ((nextRand() + 1) * 0.5) * 0.11; // ~6-10 keys/sec
      }
    }
  }

  // Per-agent instantaneous phase accumulators for natural pitch glides
  const agentPhase = new Float64Array(CALL_CENTER_AGENTS.length);
  let keyIdx = 0;

  for (let i = 0; i < length; i++) {
    const t = i / sr;
    const white = nextRand();

    // Paul Kellet pink noise
    b0 = 0.99886 * b0 + white * 0.0555179;
    b1 = 0.99332 * b1 + white * 0.0750759;
    b2 = 0.96900 * b2 + white * 0.1538520;
    b3 = 0.86650 * b3 + white * 0.3104856;
    b4 = 0.55000 * b4 + white * 0.5329522;
    b5 = -0.7616 * b5 - white * 0.0168980;
    const pink = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362) * 0.04;
    b6 = white * 0.115926;

    // Warm brown noise
    brown = (brown + 0.025 * white) / 1.025;
    lpRoom = lpRoom + 0.18 * (pink * 0.7 + brown * 0.12 - lpRoom);

    let sample = lpRoom * 0.45;

    if (profile === "call-center") {
      // 1. Multi-Agent Distant Call Centre Floor Babble (Formant-shaped human speech murmur)
      let babble = 0;
      for (let a = 0; a < CALL_CENTER_AGENTS.length; a++) {
        const spec = CALL_CENTER_AGENTS[a];
        // Smooth phrase on/off envelope (loops cleanly over 12s because phraseRate is multiple of 1/12)
        const phraseWave =
          0.6 * Math.sin(2 * Math.PI * spec.phraseRate * t + spec.phaseOffset) +
          0.4 * Math.cos(2 * Math.PI * (spec.phraseRate * 2) * t - spec.phaseOffset * 0.7);
        const phraseGate = Math.max(0, Math.min(1, (phraseWave - spec.dutyThreshold) * 2.8));

        if (phraseGate > 0.01) {
          // Syllabic envelope (4-5.2 Hz human syllable cadence)
          const sylWave =
            0.55 +
            0.45 *
              Math.sin(2 * Math.PI * spec.syllableRate * t + spec.phaseOffset * 1.3) *
              Math.cos(2 * Math.PI * 0.75 * t + a);
          const env = phraseGate * Math.max(0, sylWave);

          // Natural prosodic pitch inflection around f0
          const fInst =
            spec.f0 *
            (1 +
              0.11 * Math.sin(2 * Math.PI * 0.5 * t + spec.phaseOffset) +
              0.05 * Math.cos(2 * Math.PI * 1.25 * t + a));
          agentPhase[a] += (2 * Math.PI * fInst) / sr;
          if (agentPhase[a] > 2 * Math.PI) agentPhase[a] -= 2 * Math.PI;

          // Moving vowel formants F1 and F2 across syllables
          const f1 = spec.f1Center + 130 * Math.sin(2 * Math.PI * (spec.syllableRate * 0.5) * t + a);
          const f2 = spec.f2Center + 260 * Math.cos(2 * Math.PI * (spec.syllableRate * 0.35) * t + spec.phaseOffset);

          // Synthesize voiced harmonics weighted by F1 and F2 formant bell curves (muffled distance roll-off)
          let voice = 0;
          const p = agentPhase[a];
          for (let h = 1; h <= 7; h++) {
            const fh = fInst * h;
            const d1 = (fh - f1) / 160;
            const d2 = (fh - f2) / 280;
            const formantWeight = Math.exp(-0.5 * d1 * d1) + 0.45 * Math.exp(-0.5 * d2 * d2) + 0.12 / h;
            voice += formantWeight * Math.sin(h * p);
          }

          babble += voice * env * spec.gain * 0.08;
        }
      }

      // 2. Soft Mechanical Keyboard Typing Clicks (CRM data entry)
      while (keyIdx < keystrokeTimes.length && t >= keystrokeTimes[keyIdx] + 0.018) {
        keyIdx++;
      }
      let keyClick = 0;
      if (keyIdx < keystrokeTimes.length) {
        const dt = t - keystrokeTimes[keyIdx];
        if (dt >= 0 && dt < 0.018) {
          const clickEnv = Math.exp(-dt * 260);
          keyClick =
            clickEnv *
            (0.6 * Math.sin(2 * Math.PI * 1450 * dt) +
              0.4 * Math.sin(2 * Math.PI * 2350 * dt) +
              0.5 * white) *
            0.055;
        }
      }

      // 3. Faint Distant Call-Centre ACD Queue / Headset Soft-Tone (once at t=3.4s and t=9.6s)
      let queueTone = 0;
      for (const beepStart of [3.4, 9.6]) {
        const dt = t - beepStart;
        if (dt >= 0 && dt < 0.22) {
          const beepEnv = Math.sin((Math.PI * dt) / 0.22) * Math.exp(-dt * 6);
          queueTone +=
            beepEnv *
            (0.6 * Math.sin(2 * Math.PI * 660 * dt) + 0.4 * Math.sin(2 * Math.PI * 880 * dt)) *
            0.018;
        }
      }

      sample += babble + keyClick + queueTone;
    } else {
      // Sacred Vedic Jyotish Chamber Ambience ("vedic")
      const pluck1 = 0.55 + 0.45 * Math.sin(2 * Math.PI * (2 / 12) * t);
      const pluck2 = 0.55 + 0.45 * Math.sin(2 * Math.PI * (2 / 12) * t - 2.1);
      const pluck3 = 0.55 + 0.45 * Math.sin(2 * Math.PI * (1 / 12) * t - 4.2);

      const fSa = 136.0;
      const fPa = 204.0;
      const fSaHigh = 272.0;

      const drone =
        pluck1 * (0.50 * Math.sin(2 * Math.PI * fSa * t) + 0.20 * Math.sin(2 * Math.PI * fSa * 2 * t)) +
        pluck2 * (0.36 * Math.sin(2 * Math.PI * fPa * t) + 0.14 * Math.sin(2 * Math.PI * fPa * 2 * t)) +
        pluck3 * (0.24 * Math.sin(2 * Math.PI * fSaHigh * t));

      let bell = 0;
      for (const bellStart of [1.4, 7.4]) {
        const dt = t - bellStart;
        if (dt >= 0 && dt < 3.5) {
          const env = Math.exp(-dt * 2.2) * (1 - Math.exp(-dt * 45));
          bell +=
            env *
            (0.55 * Math.sin(2 * Math.PI * 1088 * dt) +
              0.28 * Math.sin(2 * Math.PI * 1632 * dt) +
              0.14 * Math.sin(2 * Math.PI * 2720 * dt)) *
            0.06;
        }
      }

      sample += drone * 0.055 + bell;
    }

    out[i] = sample;
  }

  // Seamless 120ms cross-fade at loop boundary
  const fadeSamples = Math.min(Math.floor(sr * 0.12), Math.floor(length / 4));
  for (let i = 0; i < fadeSamples; i++) {
    const alpha = i / fadeSamples;
    const tailIdx = length - fadeSamples + i;
    const blended = out[i] * alpha + out[tailIdx] * (1 - alpha);
    out[i] = blended;
    out[tailIdx] = blended;
  }

  // Peak-normalize buffer to 0.85 so `gainNode.gain` directly sets the exact output amplitude!
  let maxAbs = 0;
  for (let i = 0; i < length; i++) {
    const a = Math.abs(out[i]);
    if (a > maxAbs) maxAbs = a;
  }
  if (maxAbs > 1e-6) {
    const normScale = 0.85 / maxAbs;
    for (let i = 0; i < length; i++) {
      out[i] *= normScale;
    }
  }

  return buffer;
}

async function loadCallCenterAudioBuffer(ctx: AudioContext): Promise<AudioBuffer> {
  if (cachedCallCenterDecoded && cachedCallCenterDecoded.sampleRate === ctx.sampleRate) {
    return cachedCallCenterDecoded.buffer;
  }
  const rawBytes = await fetchCallCenterMp3Bytes();
  if (rawBytes && ctx.state !== "closed") {
    try {
      // Slice a fresh copy because decodeAudioData detaches the input ArrayBuffer.
      const decoded = await ctx.decodeAudioData(rawBytes.slice(0));
      cachedCallCenterDecoded = { sampleRate: ctx.sampleRate, buffer: decoded };
      return decoded;
    } catch {
      // Fall back to synthesized buffer if decoding fails in an offline/test environment.
    }
  }
  return buildAmbienceBuffer(ctx, "call-center");
}

/**
 * Start continuous natural background ambience connected to the call's output destination.
 */
export function startAmbientNoise(
  ctx: AudioContext,
  destination: AudioNode,
  options: {
    personaId?: string;
    enabled?: boolean;
    level?: number;
  } = {},
): AmbientNoiseController {
  const profile = resolveAmbienceProfile(options.personaId);
  if (!profile) {
    return {
      setEnabled() {},
      setLevel() {},
      setSpeaking() {},
      stop() {},
    };
  }
  let isEnabled = options.enabled ?? true;
  // Default level 0.22 -> subtle, natural distant call-centre voices in neighbouring cubicles
  // (~0.031 listening gain, ~0.017 when Riya is speaking) without overpowering the bot.
  let userLevel = Math.max(0, Math.min(1, options.level ?? 0.22));
  let isSpeaking = false;
  let stopped = false;
  let source: AudioBufferSourceNode | null = null;

  // Warm background bandpass shaping (220 Hz - 2200 Hz for call-center; 95 Hz - 2100 Hz for vedic)
  const highpass = ctx.createBiquadFilter();
  highpass.type = "highpass";
  highpass.frequency.value = profile === "vedic" ? 95 : 220;
  highpass.Q.value = 0.6;

  const lowpass = ctx.createBiquadFilter();
  lowpass.type = "lowpass";
  lowpass.frequency.value = profile === "vedic" ? 2100 : 2200;
  lowpass.Q.value = 0.7;

  const gainNode = ctx.createGain();

  const computeTargetGain = () => {
    if (!isEnabled) return 0;
    const baseGain = userLevel * (profile === "call-center" ? 0.14 : 0.10);
    return isSpeaking ? baseGain * 0.55 : baseGain;
  };

  gainNode.gain.setValueAtTime(computeTargetGain(), ctx.currentTime);

  highpass.connect(lowpass);
  lowpass.connect(gainNode);
  gainNode.connect(destination);

  const attachAndStartBuffer = (buffer: AudioBuffer) => {
    if (stopped || ctx.state === "closed") return;
    const node = ctx.createBufferSource();
    node.buffer = buffer;
    node.loop = true;
    node.connect(highpass);
    try {
      node.start(0);
      source = node;
    } catch {
      try {
        node.disconnect();
      } catch {
        // Ignore
      }
    }
  };

  if (profile === "vedic") {
    attachAndStartBuffer(buildAmbienceBuffer(ctx, "vedic"));
  } else if (cachedCallCenterDecoded && cachedCallCenterDecoded.sampleRate === ctx.sampleRate) {
    attachAndStartBuffer(cachedCallCenterDecoded.buffer);
  } else {
    void loadCallCenterAudioBuffer(ctx).then((buffer) => {
      attachAndStartBuffer(buffer);
    });
  }

  const updateGainSmooth = (timeConstant = 0.12) => {
    if (stopped || ctx.state === "closed") return;
    try {
      gainNode.gain.setTargetAtTime(computeTargetGain(), ctx.currentTime, timeConstant);
    } catch {
      gainNode.gain.value = computeTargetGain();
    }
  };

  return {
    setEnabled(enabled: boolean) {
      isEnabled = enabled;
      updateGainSmooth(0.06);
    },
    setLevel(level: number) {
      userLevel = Math.max(0, Math.min(1, level));
      updateGainSmooth(0.06);
    },
    setSpeaking(speaking: boolean) {
      if (isSpeaking === speaking) return;
      isSpeaking = speaking;
      updateGainSmooth(speaking ? 0.08 : 0.20);
    },
    stop() {
      stopped = true;
      if (source) {
        try {
          source.stop();
        } catch {
          // Already stopped
        }
        try {
          source.disconnect();
        } catch {
          // Ignore
        }
        source = null;
      }
      try {
        highpass.disconnect();
        lowpass.disconnect();
        gainNode.disconnect();
      } catch {
        // Ignore disconnect errors
      }
    },
  };
}

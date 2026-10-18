/**
 * Realistic, continuous background ambience generator for natural voice conversations.
 *
 * Eliminates unnatural digital "dead air" between turns by synthesizing a warm,
 * organic acoustic floor directly on the session's WebAudio graph:
 * - "vedic" (for Astrotalk Pandit Raghavendra Ji): Warm consultation room tone,
 *   subtle 136.1 Hz (C# Om) Sa-Pa Tanpura harmonic resonance, and a soft, distant
 *   temple bell (ghanti) shimmer.
 * - "office" (for Riya, Meera, Ananya, Pragya, Abhay, etc.): Warm telephony/office
 *   room comfort noise (pink/brown 1/f spectrum, bandpass 200-2400 Hz) with subtle
 *   distant contact-center room murmur and HVAC presence.
 *
 * Automatically ducks slightly while the bot speaks so voice clarity remains pristine,
 * and fills pauses/listening windows so the line always feels live and human.
 */

export type AmbienceProfile = "vedic" | "office";

export type AmbientNoiseController = {
  setEnabled: (enabled: boolean) => void;
  setLevel: (level: number) => void;
  setSpeaking: (speaking: boolean) => void;
  stop: () => void;
};

export function resolveAmbienceProfile(personaId?: string): AmbienceProfile {
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
  return "office";
}

/**
 * Build a seamless 10-second loop buffer containing warm room comfort noise plus
 * persona-matched acoustic texture (either Vedic Tanpura/ashram or Office/Call room).
 */
function buildAmbienceBuffer(ctx: AudioContext, profile: AmbienceProfile): AudioBuffer {
  const sr = ctx.sampleRate;
  const durationSec = 10;
  const length = sr * durationSec;
  const buffer = ctx.createBuffer(1, length, sr);
  const out = buffer.getChannelData(0);

  // Paul Kellet pink-noise filter state + leaky brown-noise integrator
  let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
  let brown = 0;
  // Simple 1-pole lowpass state for distant murmur / room warmth
  let lpMurmur = 0;
  let lpRoom = 0;

  // Deterministic pseudo-random generator for reproducible smooth loop
  let seed = profile === "vedic" ? 108108 : 424242;
  const nextRand = () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return (seed / 0xffffffff) * 2 - 1;
  };

  for (let i = 0; i < length; i++) {
    const t = i / sr;
    const white = nextRand();

    // Pink noise (1/f)
    b0 = 0.99886 * b0 + white * 0.0555179;
    b1 = 0.99332 * b1 + white * 0.0750759;
    b2 = 0.96900 * b2 + white * 0.1538520;
    b3 = 0.86650 * b3 + white * 0.3104856;
    b4 = 0.55000 * b4 + white * 0.5329522;
    b5 = -0.7616 * b5 - white * 0.0168980;
    const pink = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362) * 0.04;
    b6 = white * 0.115926;

    // Brown noise (1/f^2) for warm low-mid room air
    brown = (brown + 0.02 * white) / 1.02;
    const warmBrown = brown * 0.18;

    // Smooth room air (low-pass around ~1.4 kHz to avoid any harsh high hiss)
    lpRoom = lpRoom + 0.22 * (pink * 0.65 + warmBrown * 0.35 - lpRoom);

    let sample = lpRoom * 0.32;

    if (profile === "vedic") {
      // Sacred Vedic study ambiance:
      // Soft Tanpura drone on C# (136.1 Hz 'Om' Shadja) + G# (204.15 Hz Pancham) + C# octave (272.2 Hz)
      // Each string has a slow, organic 5-second plucking swell that loops seamlessly over 10s
      const pluck1 = 0.55 + 0.45 * Math.sin(2 * Math.PI * 0.2 * t);
      const pluck2 = 0.55 + 0.45 * Math.sin(2 * Math.PI * 0.2 * t - 2.1);
      const pluck3 = 0.55 + 0.45 * Math.sin(2 * Math.PI * 0.1 * t - 4.2);

      const fSa = 136.0; // Exact integer multiple of 0.1 Hz for zero-click 10s loop
      const fPa = 204.0;
      const fSaHigh = 272.0;

      const drone =
        pluck1 * (0.50 * Math.sin(2 * Math.PI * fSa * t) + 0.18 * Math.sin(2 * Math.PI * fSa * 2 * t)) +
        pluck2 * (0.35 * Math.sin(2 * Math.PI * fPa * t) + 0.12 * Math.sin(2 * Math.PI * fPa * 2 * t)) +
        pluck3 * (0.22 * Math.sin(2 * Math.PI * fSaHigh * t));

      // Gentle distant temple bell (ghanti) resonance once per 10s loop (at t = 1.2s)
      let bell = 0;
      const bellOffset = t - 1.2;
      if (bellOffset >= 0 && bellOffset < 3.5) {
        const env = Math.exp(-bellOffset * 2.4) * (1 - Math.exp(-bellOffset * 45));
        bell =
          env *
          (0.55 * Math.sin(2 * Math.PI * 1088 * bellOffset) +
            0.28 * Math.sin(2 * Math.PI * 1632 * bellOffset) +
            0.14 * Math.sin(2 * Math.PI * 2720 * bellOffset)) *
          0.028;
      }

      sample += drone * 0.022 + bell;
    } else {
      // Natural Contact-Center / Office Call Ambience:
      // 1. Subtle room HVAC / air movement (110 Hz + 220 Hz)
      const hvac = 0.004 * Math.sin(2 * Math.PI * 110 * t) + 0.0025 * Math.sin(2 * Math.PI * 220 * t);

      // 2. Distant, unintelligible office room murmur (heavily low-pass filtered ~450 Hz
      //    with syllabic 3.2 Hz & 4.7 Hz envelope modulations)
      lpMurmur = lpMurmur + 0.085 * (pink - lpMurmur);
      const syllabicEnv =
        Math.max(0, Math.sin(2 * Math.PI * 0.3 * t) * 0.6 + 0.4) *
        (0.5 + 0.5 * Math.sin(2 * Math.PI * 3.1 * t) * Math.cos(2 * Math.PI * 1.7 * t));

      sample += hvac + lpMurmur * syllabicEnv * 0.45;
    }

    // Cross-fade first and last 2400 samples (100ms) for a 100% click-free loop boundary
    out[i] = sample;
  }

  const fadeSamples = Math.min(Math.floor(sr * 0.1), Math.floor(length / 4));
  for (let i = 0; i < fadeSamples; i++) {
    const alpha = i / fadeSamples;
    const tailIdx = length - fadeSamples + i;
    const blended = out[i] * alpha + out[tailIdx] * (1 - alpha);
    out[i] = blended;
    out[tailIdx] = blended;
  }

  return buffer;
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
  let isEnabled = options.enabled ?? true;
  // Base gain calibrated so RMS is ~0.018-0.024 (-33 dBFS): clearly audible as a natural
  // live phone/room background without ever competing with the advisor's speech.
  let userLevel = Math.max(0, Math.min(1, options.level ?? 0.42));
  let isSpeaking = false;

  const buffer = buildAmbienceBuffer(ctx, profile);
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.loop = true;

  // Warm telephony/room bandpass shaping (highpass 120 Hz, lowpass 2600 Hz)
  const highpass = ctx.createBiquadFilter();
  highpass.type = "highpass";
  highpass.frequency.value = profile === "vedic" ? 95 : 130;
  highpass.Q.value = 0.5;

  const lowpass = ctx.createBiquadFilter();
  lowpass.type = "lowpass";
  lowpass.frequency.value = profile === "vedic" ? 2200 : 2500;
  lowpass.Q.value = 0.6;

  const gainNode = ctx.createGain();

  const computeTargetGain = () => {
    if (!isEnabled) return 0;
    // Scale 0..1 user level to a natural gain multiplier (0.14 at level=0.42 -> ~0.06 gain)
    const baseGain = userLevel * 0.15;
    // Duck slightly while the bot speaks so voice clarity is 100% crisp,
    // and restore during listening/thinking pauses so dead air disappears.
    return isSpeaking ? baseGain * 0.62 : baseGain;
  };

  gainNode.gain.setValueAtTime(computeTargetGain(), ctx.currentTime);

  source.connect(highpass);
  highpass.connect(lowpass);
  lowpass.connect(gainNode);
  gainNode.connect(destination);

  try {
    source.start(0);
  } catch {
    // Ignore if context is already closed
  }

  const updateGainSmooth = (timeConstant = 0.15) => {
    if (ctx.state === "closed") return;
    try {
      gainNode.gain.setTargetAtTime(computeTargetGain(), ctx.currentTime, timeConstant);
    } catch {
      gainNode.gain.value = computeTargetGain();
    }
  };

  return {
    setEnabled(enabled: boolean) {
      isEnabled = enabled;
      updateGainSmooth(0.08);
    },
    setLevel(level: number) {
      userLevel = Math.max(0, Math.min(1, level));
      updateGainSmooth(0.08);
    },
    setSpeaking(speaking: boolean) {
      if (isSpeaking === speaking) return;
      isSpeaking = speaking;
      updateGainSmooth(speaking ? 0.10 : 0.25);
    },
    stop() {
      try {
        source.stop();
      } catch {
        // Already stopped
      }
      try {
        source.disconnect();
        highpass.disconnect();
        lowpass.disconnect();
        gainNode.disconnect();
      } catch {
        // Ignore disconnect errors
      }
    },
  };
}

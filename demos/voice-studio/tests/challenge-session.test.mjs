import test from 'node:test';
import assert from 'node:assert/strict';
import { CLOSE_GRACE_MS, openChallengeSession } from '../src/challenge/challenge-session.ts';

// Stand-ins for the pipecat transport and the Web Audio graph, so the call's
// close handling can be driven step by step.
function fakeModules({ failConnect = false } = {}) {
  const state = { transport: null };
  class DailyMediaManager {}
  class RTVIMessage {
    constructor(type, data) { this.type = type; this.data = data; }
  }
  class WebSocketTransport {
    constructor() { this.sent = []; this.disconnects = 0; state.transport = this; }
    initialize(options, onMessage) { this.callbacks = options.callbacks; this.onMessage = onMessage; }
    async initDevices() {}
    async connect() {
      if (failConnect) {
        this.callbacks.onError({}); // The server hung up during the handshake.
        throw new Error('closed');
      }
    }
    async disconnect() { this.disconnects += 1; }
    sendReadyMessage() {}
    sendMessage(message) { this.sent.push(message); }
    tracks() { return { local: { audio: { stop() {} } } }; }
    enableMic() {}
  }
  return { state, load: async () => ({ WebSocketTransport, DailyMediaManager, RTVIMessage }) };
}

function fakeAudioContext() {
  const node = () => ({ connect() {}, disconnect() {}, getFloatTimeDomainData() {}, fftSize: 256 });
  return { currentTime: 0, destination: {}, createAnalyser: node };
}

function recorder() {
  const log = [];
  const events = {};
  for (const name of ['onState', 'onResult', 'onDeal', 'onTranscript', 'onPartialUser', 'onError', 'onRefused', 'onDisconnected']) {
    events[name] = (...args) => log.push([name, ...args]);
  }
  events.onBotSpeaking = () => {};
  events.onLevel = () => {};
  return { log, events };
}

const refusal = code => ({ label: 'rtvi-ai', type: 'error', data: { error: 'x', fatal: true, code } });
const result = { label: 'rtvi-ai', type: 'server-message', data: { type: 'challenge_result', recorded: true, rank: 1 } };

async function openCall(t, options) {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] });
  const { state, load } = fakeModules(options);
  const { log, events } = recorder();
  const session = await openChallengeSession('ws://host/ws', fakeAudioContext(), events, load);
  return { session, transport: state.transport, log };
}

test('a refusal decoded just after the server closes still reaches the page', async t => {
  const { transport, log } = await openCall(t);
  transport.callbacks.onError({}); // The close event wins the race...
  assert.equal(transport.disconnects, 1); // ...and the microphone is released at once.
  transport.onMessage(refusal('already_played')); // ...then the message it overtook.
  t.mock.timers.tick(CLOSE_GRACE_MS);
  assert.deepEqual(log, [['onRefused', 'already_played']]);
});

test('a refusal before the close is reported once, with no "dropped" error', async t => {
  const { transport, log } = await openCall(t);
  transport.onMessage(refusal('in_progress'));
  transport.callbacks.onError({});
  t.mock.timers.tick(CLOSE_GRACE_MS * 4);
  assert.deepEqual(log, [['onRefused', 'in_progress']]);
});

test('an unexplained close is reported as dropped after the grace period', async t => {
  const { transport, log } = await openCall(t);
  transport.callbacks.onError({});
  t.mock.timers.tick(CLOSE_GRACE_MS - 1);
  assert.deepEqual(log, []);
  t.mock.timers.tick(1);
  assert.deepEqual(log, [['onError', 'The voice connection dropped.'], ['onDisconnected']]);
});

test('a result overtaken by the close is still delivered', async t => {
  const { transport, log } = await openCall(t);
  transport.callbacks.onDisconnected();
  transport.onMessage(result);
  t.mock.timers.tick(CLOSE_GRACE_MS);
  assert.deepEqual(log.map(entry => entry[0]), ['onResult', 'onDisconnected']);
  // Nothing is handled once the grace period is over.
  transport.onMessage(refusal('already_played'));
  assert.equal(log.length, 2);
});

test('hanging up from the page silences the call, even mid-grace', async t => {
  const { session, transport, log } = await openCall(t);
  transport.callbacks.onError({});
  await session.disconnect();
  transport.onMessage(result);
  transport.onMessage(refusal('already_played'));
  t.mock.timers.tick(CLOSE_GRACE_MS * 4);
  assert.deepEqual(log, []);
});

test('only fatal errors end the round, and only known codes are refusals', async t => {
  const { transport, log } = await openCall(t);
  transport.onMessage({ label: 'rtvi-ai', type: 'error', data: { error: 'notice', fatal: false } });
  transport.onMessage(refusal('something-else'));
  assert.deepEqual(log, [['onError', 'The round could not continue. Please try again.']]);
});

test('a refusal during the handshake survives the failed connect', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] });
  const { state, load } = fakeModules({ failConnect: true });
  const { log, events } = recorder();
  await assert.rejects(openChallengeSession('ws://host/ws', fakeAudioContext(), events, load), /Couldn't reach/);
  state.transport.onMessage(refusal('already_played'));
  t.mock.timers.tick(CLOSE_GRACE_MS);
  assert.deepEqual(log, [['onRefused', 'already_played']]);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { adminLogin, ApiError, finishRound, getLeaderboard, resetBoard, RESET_CONFIRM_WORD, startRound, validateRoundSocketUrl } from '../src/challenge/challenge-api.ts';

function mockFetch(handler) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, init });
    const { status = 200, body = {} } = await handler(url, init);
    return { ok: status >= 200 && status < 300, status, json: async () => body };
  };
  return calls;
}

test('the round socket must stay on the page host', () => {
  const page = { protocol: 'https:', host: 'abhay.example.run.app' };
  assert.equal(validateRoundSocketUrl('wss://abhay.example.run.app/ws?session_id=a', page), 'wss://abhay.example.run.app/ws?session_id=a');
  assert.throws(() => validateRoundSocketUrl('ws://abhay.example.run.app/ws', page), ApiError);
  assert.throws(() => validateRoundSocketUrl('wss://evil.example/ws', page), ApiError);
  assert.throws(() => validateRoundSocketUrl('javascript:alert(1)', page), ApiError);
  assert.throws(() => validateRoundSocketUrl(42, page), ApiError);
  assert.equal(validateRoundSocketUrl('ws://localhost:7860/ws', { protocol: 'http:', host: 'localhost:7860' }), 'ws://localhost:7860/ws');
});

test('startRound sends only the ID and language as JSON, and surfaces server errors', async () => {
  globalThis.window = { location: { protocol: 'https:', host: 'abhay.example.run.app' } };
  const calls = mockFetch(async () => ({ body: {
    ws_url: 'wss://abhay.example.run.app/ws?bot_type=gemini-live&session_id=s&connection_id=c',
    session_id: 's', session_token: 't', duration_s: 120, player: '********',
  } }));
  const ticket = await startRound('AB12CD34', 'hi-IN');
  assert.equal(calls[0].url, '/connect');
  assert.equal(calls[0].init.method, 'POST');
  assert.equal(calls[0].init.headers['Content-Type'], 'application/json');
  assert.equal(calls[0].init.credentials, 'same-origin');
  assert.deepEqual(JSON.parse(calls[0].init.body), { player_id: 'AB12CD34', language: 'hi-IN' });
  assert.deepEqual(ticket, {
    wsUrl: 'wss://abhay.example.run.app/ws?bot_type=gemini-live&session_id=s&connection_id=c',
    sessionId: 's', sessionToken: 't', durationS: 120, player: '********',
  });

  mockFetch(async () => ({ status: 400, body: { detail: 'Enter an 8-character ID (letters and numbers only).' } }));
  await assert.rejects(startRound('1', 'hi-IN'), err => err instanceof ApiError && err.status === 400 && /8-character/.test(err.message));
  // One round per ID: the server's reason reaches the player word for word.
  mockFetch(async () => ({ status: 409, body: { detail: 'This ID has already played. Each ID gets one round.' } }));
  await assert.rejects(startRound('AB12CD34', 'hi-IN'),
    err => err instanceof ApiError && err.status === 409 && err.message === 'This ID has already played. Each ID gets one round.');
  mockFetch(async () => { throw new TypeError('offline'); });
  await assert.rejects(startRound('AB12CD34', 'hi-IN'), err => err instanceof ApiError && err.status === 0);
});

test('organizer token travels in a header, never the URL', async () => {
  const calls = mockFetch(async url => (url.startsWith('/api/challenge/admin/login')
    ? { body: { token: 'tok.en.sig', expires_at_ms: 99, reveal_top_n: 3 } }
    : { body: { entries: [], total_players: 0, revealed: true, reveal_top_n: 3, stale: false, server_time_ms: 1 } }));
  const session = await adminLogin('correct horse battery staple');
  assert.deepEqual(session, { token: 'tok.en.sig', expiresAtMs: 99, revealTopN: 3 });
  assert.deepEqual(JSON.parse(calls[0].init.body), { password: 'correct horse battery staple' });
  await getLeaderboard(10, session.token);
  assert.equal(calls[1].url, '/api/challenge/leaderboard?limit=10');
  assert.equal(calls[1].init.headers['X-Admin-Token'], 'tok.en.sig');
  await getLeaderboard(10, null);
  assert.equal(calls[2].init.headers['X-Admin-Token'], undefined);
});

test('finishRound authenticates with the session token and decodes the result', async () => {
  const calls = mockFetch(async () => ({ body: { result: {
    reason: 'ended_by_player', player: '********', price_inr: 1610000, extras_value_inr: 0, sold: false,
    recorded: true, not_recorded_reason: null, can_retry: false, rank: 4, total_players: 12,
  } } }));
  const result = await finishRound('s', 't');
  assert.equal(calls[0].init.headers['X-Session-Token'], 't');
  assert.deepEqual(JSON.parse(calls[0].init.body), { session_id: 's' });
  assert.equal(result.type, 'challenge_result');
  assert.equal(result.rank, 4);
  assert.equal(result.can_retry, false);

  mockFetch(async () => ({ body: { result: {
    reason: 'ended_by_player', player: '********', price_inr: 2000000, extras_value_inr: 0, sold: false,
    recorded: false, not_recorded_reason: 'no_speech', can_retry: true, rank: null, total_players: null,
  } } }));
  const unscored = await finishRound('s', 't');
  assert.equal(unscored.not_recorded_reason, 'no_speech');
  assert.equal(unscored.can_retry, true);
});

test('resetBoard sends the organizer token in a header with the explicit confirmation', async () => {
  const calls = mockFetch(async () => ({ body: { removed: 7 } }));
  assert.equal(RESET_CONFIRM_WORD, 'RESET');
  assert.deepEqual(await resetBoard('tok.en.sig'), { removed: 7 });
  assert.equal(calls[0].url, '/api/challenge/admin/reset');
  assert.equal(calls[0].init.method, 'POST');
  assert.equal(calls[0].init.headers['X-Admin-Token'], 'tok.en.sig');
  assert.deepEqual(JSON.parse(calls[0].init.body), { confirm: 'RESET' });
  assert.ok(!calls[0].url.includes('tok.en.sig'));
  // An expired organizer session surfaces as a 401 the UI can act on.
  mockFetch(async () => ({ status: 401, body: { detail: 'Organizer session expired' } }));
  await assert.rejects(resetBoard('old'), err => err instanceof ApiError && err.status === 401);
});

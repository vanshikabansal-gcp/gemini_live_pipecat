import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  formatCountdown,
  formatInr,
  isOwnRow,
  isValidPlayerId,
  maskPlayerId,
  notRecordedMessage,
  parseServerMessage,
  playersLabel,
  rankSummary,
  refusalMessage,
  sanitizePlayerIdInput,
} from '../src/challenge/challenge-logic.ts';

test('player IDs: eight ASCII letters or digits, uppercased, masked across all eight characters', () => {
  assert.equal(sanitizePlayerIdInput('1234 5678'), '12345678');
  assert.equal(sanitizePlayerIdInput('ab12 cd34'), 'AB12CD34');
  assert.equal(sanitizePlayerIdInput('Ab-12_cD.34xyz'), 'AB12CD34');
  assert.equal(sanitizePlayerIdInput('١٢٣٤٥٦٧٨'), ''); // Non-ASCII digits never count.
  // Removed before uppercasing: 'ſ' uppercases to 'S', 'ß' to 'SS' and 'ı' to 'I';
  // the Kelvin sign (U+212A) and 'Å' look like ASCII letters but are not.
  assert.equal(sanitizePlayerIdInput('\u017f\u00df\u0131\u212a\u00c512345678'), '12345678');
  assert.equal(sanitizePlayerIdInput(''), '');
  assert.equal(isValidPlayerId('12345678'), true);
  assert.equal(isValidPlayerId('AB12CD34'), true);
  assert.equal(isValidPlayerId('ABCDEFGH'), true);
  assert.equal(isValidPlayerId('ab12cd34'), false); // The input is uppercased before this check.
  assert.equal(isValidPlayerId('AB12CD3'), false);
  assert.equal(isValidPlayerId('AB12CD345'), false);
  assert.equal(isValidPlayerId('AB12CD3-'), false);
  assert.equal(isValidPlayerId('AB12CD3Å'), false);
  assert.equal(maskPlayerId('AB12CD34'), '********');
  assert.equal(maskPlayerId('12345678'), '********');
});

test('prices use Indian grouping and the countdown never shows 0:00 early', () => {
  assert.equal(formatInr(1450000), '₹14,50,000');
  assert.equal(formatInr(2000000), '₹20,00,000');
  assert.equal(formatInr(Number.NaN), '₹0');
  assert.equal(formatCountdown(120000), '2:00');
  assert.equal(formatCountdown(119400), '2:00');
  assert.equal(formatCountdown(59001), '1:00');
  assert.equal(formatCountdown(9000), '0:09');
  assert.equal(formatCountdown(1), '0:01');
  assert.equal(formatCountdown(0), '0:00');
  assert.equal(formatCountdown(-50), '0:00');
  assert.equal(playersLabel(1), '1 player');
  assert.equal(playersLabel(1234), '1,234 players');
});

test('server messages decode from RTVI server-message envelopes', () => {
  const wrap = data => ({ label: 'rtvi-ai', type: 'server-message', data });
  assert.deepEqual(
    parseServerMessage(wrap({ type: 'challenge_state', status: 'running', duration_ms: 120000, remaining_ms: 119000, player: '********' })),
    { type: 'challenge_state', status: 'running', duration_ms: 120000, remaining_ms: 119000, player: '********' },
  );
  const result = parseServerMessage(wrap({
    type: 'challenge_result', reason: 'time_up', player: '********', price_inr: 1480000, extras_value_inr: 55000,
    sold: false, recorded: true, not_recorded_reason: null, can_retry: false, rank: 2, total_players: 9,
  }));
  assert.deepEqual(result, {
    type: 'challenge_result', reason: 'time_up', player: '********', price_inr: 1480000, extras_value_inr: 55000,
    sold: false, recorded: true, not_recorded_reason: null, can_retry: false, rank: 2, total_players: 9,
  });
  assert.equal(rankSummary(result), "You're #2 of 9 players");

  const deal = parseServerMessage(wrap({
    type: 'deal_state', currency: 'INR', cash_price: 1875000, extras_value: 55000,
    extras: [{ key: 'wallbox_charger', label: 'Wall-box charger', value_inr: 55000 }, 'junk'], sold: false,
  }));
  assert.deepEqual(deal, {
    type: 'deal_state', cash_price: 1875000, extras_value: 55000,
    extras: [{ key: 'wallbox_charger', label: 'Wall-box charger', value_inr: 55000 }], sold: false,
  });

  assert.deepEqual(parseServerMessage(wrap({ type: 'transcription', participant: 'User', text: 'Bhai 15 lakh' })),
    { type: 'transcript', role: 'user', text: 'Bhai 15 lakh' });
  assert.deepEqual(parseServerMessage(wrap({ type: 'transcription', participant: 'Assistant', text: 'Nahi boss' })),
    { type: 'transcript', role: 'assistant', text: 'Nahi boss' });
  assert.deepEqual(parseServerMessage(wrap({ type: 'metrics', payload: { type: 'interruption' } })), { type: 'interruption' });
  assert.deepEqual(parseServerMessage(wrap({ type: 'metrics', payload: { type: 'usage' } })), { type: 'ignored' });
});

test('a round that did not count can be retried; a scored round never can', () => {
  const wrap = data => ({ label: 'rtvi-ai', type: 'server-message', data: { type: 'challenge_result', ...data } });
  for (const reason of ['no_speech', 'no_price', 'storage_error']) {
    const unscored = parseServerMessage(wrap({ recorded: false, not_recorded_reason: reason, can_retry: true }));
    assert.equal(unscored.not_recorded_reason, reason);
    assert.equal(unscored.can_retry, true);
  }
  const taken = parseServerMessage(wrap({ recorded: false, not_recorded_reason: 'already_played', can_retry: false }));
  assert.equal(taken.not_recorded_reason, 'already_played');
  assert.equal(taken.can_retry, false);
  // Whatever else it says, a recorded round used up its ID.
  assert.equal(parseServerMessage(wrap({ recorded: true, can_retry: true, rank: 1 })).can_retry, false);
  assert.equal(parseServerMessage(wrap({ recorded: false, can_retry: 'yes' })).can_retry, false);
  assert.equal(parseServerMessage(wrap({ recorded: false })).can_retry, false);
});

test('unknown or hostile shapes are ignored, and only fatal errors are errors', () => {
  assert.deepEqual(parseServerMessage(null), { type: 'ignored' });
  assert.deepEqual(parseServerMessage('challenge_result'), { type: 'ignored' });
  assert.deepEqual(parseServerMessage({ label: 'rtvi-ai', type: 'bot-ready', data: {} }), { type: 'ignored' });
  assert.deepEqual(parseServerMessage({ label: 'rtvi-ai', type: 'error-response', data: { error: 'Unsupported type' } }), { type: 'ignored' });
  assert.deepEqual(parseServerMessage({ label: 'rtvi-ai', type: 'error', data: { error: 'x', fatal: true } }), { type: 'error', fatal: true, code: null });
  assert.deepEqual(parseServerMessage({ label: 'rtvi-ai', type: 'error', data: { error: 'x', fatal: false } }), { type: 'error', fatal: false, code: null });
  const odd = parseServerMessage({ type: 'server-message', data: { type: 'challenge_result', price_inr: '1', rank: '1', not_recorded_reason: 'hax' } });
  assert.equal(odd.price_inr, 0);
  assert.equal(odd.rank, null);
  assert.equal(odd.not_recorded_reason, null);
  assert.equal(odd.recorded, false);
  assert.equal(odd.can_retry, false);
});

test('a refused round carries its reason code, and only known codes count', () => {
  const error = data => parseServerMessage({ label: 'rtvi-ai', type: 'error', data: { error: 'x', fatal: true, ...data } });
  assert.deepEqual(error({ code: 'already_played' }), { type: 'error', fatal: true, code: 'already_played' });
  assert.equal(error({ code: 'in_progress' }).code, 'in_progress');
  assert.equal(error({ code: 'unavailable' }).code, 'unavailable');
  assert.equal(error({ code: 'toString' }).code, null);
  assert.equal(error({ code: 7 }).code, null);
  assert.equal(refusalMessage('already_played'), 'This ID has already played. Each ID gets one round.');
  assert.match(refusalMessage('in_progress'), /already in a round/);
  assert.match(refusalMessage('unavailable'), /unavailable right now/);
});

test('result copy explains why a round is not on the board', () => {
  assert.match(notRecordedMessage('no_speech'), /didn't hear you.*still unused/);
  assert.match(notRecordedMessage('no_price'), /never named a price.*still unused/);
  assert.match(notRecordedMessage('storage_error'), /organizer/);
  assert.match(notRecordedMessage('already_played'), /one round/);
  assert.equal(notRecordedMessage(null), null);
  assert.equal(rankSummary({ recorded: false, rank: null }), null);
  assert.equal(rankSummary({ recorded: true, rank: null, total_players: null }), null);
  assert.equal(rankSummary({ recorded: true, rank: 1, total_players: null }), "You're #1 of 1 player");
  assert.equal(isOwnRow({ player: '********' }, '********'), false);
  assert.equal(isOwnRow({ player: '****CD34' }, '****CD34'), true);
  assert.equal(isOwnRow({ player: '********' }, null), false);
});

test('the challenge sources never import the persona catalogue or studio session', () => {
  const dir = fileURLToPath(new URL('../src/challenge/', import.meta.url));
  for (const name of fs.readdirSync(dir)) {
    const source = fs.readFileSync(path.join(dir, name), 'utf8');
    for (const forbidden of ['personas', 'voice-session', 'pipecat-session', 'use-voice-session', '@/lib', '../lib/']) {
      const imports = [...source.matchAll(/(?:import|from)\s*\(?\s*["']([^"']+)["']/g)].map(match => match[1]);
      assert.ok(!imports.some(item => item.includes(forbidden)), `${name} imports ${forbidden}`);
    }
  }
});

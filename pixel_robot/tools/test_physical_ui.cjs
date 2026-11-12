#!/usr/bin/env node
'use strict';

// Offline contract tests for the *actual* PHYSICAL panel. No browser, provider,
// Android service, full live-control script, network access, or hardware is used.
// Narrow manual HTTP helper/pagehide extraction tests cover its operator-token boundary.
// Run from any directory: node --test pixel_robot/tools/test_physical_ui.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createHash } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const cases = [];
const test = (name, run) => cases.push({ name, run });
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');

const htmlPath = path.join(__dirname, '../app/src/main/assets/robot_control.html');
const htmlBytes = fs.readFileSync(htmlPath);
const html = htmlBytes.toString('utf8');
const testBytes = fs.readFileSync(__filename);
for (const [name, actual] of [
  ['PIXEL_PHYSICAL_UI_EXPECTED_HTML_SHA256', sha256(htmlBytes)],
  ['PIXEL_PHYSICAL_UI_EXPECTED_TEST_SHA256', sha256(testBytes)],
]) {
  if (process.env[name]) assert.equal(actual, process.env[name], 'test inputs changed while starting report run');
}
const marker = '// Physical experiment panel: explicit one-shot operator authority.';
const start = html.indexOf(marker);
assert.notEqual(start, -1, 'actual physical IIFE marker must exist');
const end = html.indexOf('</script>', start);
assert.notEqual(end, -1, 'physical script closing tag must exist');
const source = html.slice(start, end);
assert.match(source, /\(\(\) => \{/);
assert.match(source.trim(), /\}\)\(\);$/);

class EventTargetFake {
  constructor() { this.listeners = new Map(); }
  addEventListener(name, listener) {
    const listeners = this.listeners.get(name) || [];
    listeners.push(listener);
    this.listeners.set(name, listeners);
  }
  dispatch(name, properties = {}) {
    const event = { type: name, defaultPrevented: false, ...properties,
      preventDefault() { this.defaultPrevented = true; } };
    for (const listener of this.listeners.get(name) || []) listener(event);
    return event;
  }
}

class ElementFake extends EventTargetFake {
  constructor(tag, attributes, content) {
    super();
    this.id = attributes.match(/\bid="([^"]+)"/)[1];
    this.value = (attributes.match(/\bvalue="([^"]*)"/) || [])[1] || '';
    this.checked = /\bchecked(?:\s|=|$)/.test(attributes);
    this.disabled = /\bdisabled(?:\s|=|$)/.test(attributes);
    this.textContent = tag === 'input' ? '' : (content || '').trim();
    this.htmlWrites = 0;
  }
  set innerHTML(_) { this.htmlWrites++; throw new Error('PHYSICAL output must use textContent, never innerHTML'); }
  get innerHTML() { throw new Error('PHYSICAL must not read or interpret HTML output'); }
  insertAdjacentHTML() { this.htmlWrites++; throw new Error('PHYSICAL must not insert HTML output'); }
}

const status = (state = 'disabled', service = {}, provider = 'idle') => ({
  ok: true, mode: 'PHYSICAL_EXPERIMENT', physical_stop_confirmed: false, provider_state: provider,
  service: { state, reason: `fixture_${state}`, eligible: true, blockers: [], generation: 1,
    request_id: null, command: null, expires_at_ms: null, ...service },
});
const enabled = (generation = 2) => status('enabled', { generation });
const pending = (generation = 2, requestId = 'fixture-session:r1') => status('pending', {
  generation, request_id: requestId, expires_at_ms: 10000,
}, 'requesting');
const leased = (label = 'CURRENT_COMMAND', requestId = 'fixture-session:r1', generation = 2) => status('leased', {
  generation, request_id: requestId, expires_at_ms: 1000,
  command: { label, forward_m_s: 0.1, lateral_m_s: 0, yaw_rate_rad_s: 0, duration_ms: 500 },
}, 'completed');
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

function harness(t, { injectedToken = true } = {}) {
  const elements = new Map();
  const tags = /<(input|textarea|button|form|p|pre)\b([^>]*\bid="[^"]+"[^>]*)>([^<]*)/g;
  for (const match of html.matchAll(tags)) {
    const element = new ElementFake(match[1], match[2], match[3]);
    elements.set(element.id, element);
  }
  const document = new EventTargetFake();
  document.hidden = false;
  document.getElementById = id => {
    assert.ok(elements.has(id), `panel only addresses actual page elements: ${id}`);
    return elements.get(id);
  };
  const window = new EventTargetFake();
  const calls = [], violations = [], timers = new Map();
  let timerId = 0;
  const fetchFake = (url, options = {}) => {
    // Record before rejecting so catches cannot hide unintended routes.
    if (!/^\/api\/physical\/(status|enable|request|cancel)$/.test(url)) {
      violations.push(url); return Promise.reject(new Error(`Forbidden route: ${url}`));
    }
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    const call = { url, options, settled: false,
      respond(data, response = {}) { this.settled = true; resolve({ ok: true, status: 200, json: async () => data, ...response }); },
      reject(error = new Error('offline simulated failure')) { this.settled = true; reject(error); },
    };
    options.signal?.addEventListener('abort', () => call.reject(new Error('simulated AbortError')));
    calls.push(call); return promise;
  };
  const storage = new Proxy({}, { get() { throw new Error('Physical AI must not persist configuration, credentials or consent'); } });
  const context = vm.createContext({ document, window, URL, AbortController,
    fetch: fetchFake,
    setTimeout(callback, ms) { timers.set(++timerId, { callback, ms }); return timerId; },
    clearTimeout: id => timers.delete(id), localStorage: storage, sessionStorage: storage,
    navigator: { sendBeacon(url) { violations.push(`beacon:${url}`); throw new Error('Unexpected beacon'); } },
  });
  vm.runInContext(injectedToken ? source.replaceAll('__PIXEL_SHADOW_TOKEN__', 'f'.repeat(64)) : source, context, { filename: htmlPath, timeout: 1000 });
  const h = {
    elements, document, window, calls, timers,
    field: name => document.getElementById(`physical-${name}`),
    matching: route => calls.filter(call => call.url === `/api/physical/${route}`),
    latest(route) { return this.matching(route).at(-1); },
    input(name, value) { const field = this.field(name); if (typeof value === 'boolean') field.checked = value; else field.value = value; field.dispatch('input'); },
    configure() { this.input('endpoint', 'https://provider.invalid/v1'); this.input('model', 'exact-physical-model'); this.input('text', 'Move forward briefly'); this.input('network', true); },
    submit() { return this.field('form').dispatch('submit'); },
    click(name) { this.field(name).dispatch('click'); },
    manual(id) { document.getElementById(id).dispatch(['forward', 'lateral', 'yaw', 'height', 'roll', 'pitch'].includes(id) ? 'input' : 'click'); },
    async respond(route, data, response) { const call = this.matching(route).find(item => !item.settled); assert.ok(call, `expected pending ${route}`); call.respond(data, response); await flush(); return call; },
    async tick(ms) { for (const [id, timer] of [...timers]) { if (timer.ms === ms) { timers.delete(id); timer.callback(); } } await flush(); },
    async idle(data = status()) { await this.respond('status', data); },
    async enable(generation = 2) { this.input('acknowledge', true); this.click('enable'); await this.respond('enable', enabled(generation)); },
    async prepared() { await this.idle(); this.configure(); await this.enable(); },
    async complete(data = leased()) { this.submit(); await this.respond('request', data); },
  };
  t.after(() => {
    assert.deepEqual(violations, [], 'no shadow, manual start/stand/command/stop, external fetch, credential or beacon routes');
    for (const element of elements.values()) assert.equal(element.htmlWrites, 0, `${element.id} renders text only`);
    for (const call of calls) {
      assert.equal(call.options.method, call.url.endsWith('/status') ? 'GET' : 'POST');
      assert.equal(call.options.cache, 'no-store'); assert.equal(call.options.credentials, 'omit'); assert.equal(call.options.redirect, 'error');
      assert.equal(call.options.headers['X-Pixel-Shadow-Token'], 'f'.repeat(64));
      assert.equal(call.options.headers['Content-Type'], 'application/json');
    }
  });
  return h;
}

test('page clearly separates physical risk, independent Start/Stop, shipped blocker, data and no preview-grant reuse', () => {
  const panel = html.slice(html.indexOf('<section class="panel physical-panel"'), html.indexOf('  </main>'));
  for (const text of ['Can command robot motion', 'already running', 'at most 1 second', 'at most 10 seconds', 'Start the controller manually',
    'never starts or arms', 'shipped bundle is unqualified', 'transport-verified', 'cannot unlock', 'Do not change metadata',
    'Stop + torque off', 'provider may retain', 'Credentials are unsupported', 'not saved', 'delivery is not guaranteed']) assert.ok(panel.includes(text), text);
  assert.doesNotMatch(panel, /type="password"|allow_unqualified_metadata/);
});

test('actual authored panel starts disabled, blank and without endpoint discovery or provider request', async t => {
  const h = harness(t);
  for (const id of ['endpoint', 'model', 'text']) assert.equal(h.field(id).value, '');
  for (const id of ['network', 'acknowledge']) assert.equal(h.field(id).checked, false);
  assert.equal(h.field('enable').disabled, true); assert.equal(h.field('submit').disabled, true);
  h.click('enable'); h.submit();
  assert.deepEqual(h.calls.map(call => call.url), ['/api/physical/status']);
  await h.idle(leased('ANOTHER_TAB_COMMAND'));
  assert.equal(h.field('result').textContent, ''); assert.match(h.field('status').textContent, /disabled in this page/);
});

test('unreplaced page token disables all physical routes', async t => {
  const h = harness(t, { injectedToken: false }); h.configure(); h.input('acknowledge', true); h.click('enable'); h.submit(); h.click('cancel');
  assert.equal(h.calls.length, 0); assert.equal(h.field('enable').disabled, true); assert.equal(h.field('cancel').disabled, true);
});

test('shipped unqualified bundle is visibly blocked even with every checkbox checked', async t => {
  const h = harness(t); await h.idle(status('blocked', { eligible: false, blockers: ['bundle_not_promoted', 'transport_unverified'], reason: 'deployment evidence required' }));
  h.configure(); h.input('acknowledge', true); h.click('enable'); h.submit();
  assert.equal(h.field('enable').disabled, true); assert.equal(h.field('submit').disabled, true);
  assert.match(h.field('blockers').textContent, /bundle_not_promoted.*transport_unverified/);
  assert.match(h.field('status').textContent, /blocked.*deployment evidence required/);
  assert.equal(h.matching('enable').length, 0); assert.equal(h.matching('request').length, 0);
});

test('eligible status alone cannot enable, and explicit enable never submits a model request', async t => {
  const h = harness(t); await h.idle(); h.configure(); h.click('enable'); h.submit();
  assert.equal(h.matching('enable').length, 0);
  h.input('acknowledge', true); h.click('enable'); h.click('enable');
  assert.equal(h.matching('enable').length, 1); assert.equal(h.matching('request').length, 0);
  await h.respond('enable', enabled());
  assert.equal(h.field('submit').disabled, false); assert.equal(h.field('enable').disabled, true);
});

test('enable refusal clears acknowledgement and consent, with actionable host blockers', async t => {
  const h = harness(t); await h.idle(); h.configure(); h.input('acknowledge', true); h.click('enable');
  await h.respond('enable', status('blocked', { eligible: false, reason: 'controller_not_running', blockers: ['manually start qualified controller'] }));
  assert.equal(h.field('acknowledge').checked, false); assert.equal(h.field('network').checked, false);
  assert.equal(h.field('submit').disabled, true); assert.match(h.field('blockers').textContent, /manually start/);
});

for (const [index, [name, value]] of [['endpoint', ''], ['model', ''], ['text', ''], ['model', 'm'.repeat(257)], ['model', 'model\ntoken'],
  ['text', '🐕'.repeat(2001)], ['network', false]].entries()) {
  test(`invalid ${name} case ${index + 1} cannot submit`, async t => {
    const h = harness(t); await h.idle(); h.configure(); h.input(name, value); if (name !== 'network') h.input('network', true); await h.enable(); h.submit();
    assert.equal(h.matching('request').length, 0); assert.equal(h.field('submit').disabled, true);
  });
}
for (const endpoint of ['http://provider.invalid/v1', 'https://user:secret@provider.invalid/v1', 'https://provider.invalid/v1?secret=value',
  'https://provider.invalid/v1#secret', 'https://provider.invalid', '/v1', 'not a URL', `https://provider.invalid/${'x'.repeat(1000)}/v1`]) {
  test(`unsafe endpoint ${endpoint.slice(0, 70)} cannot submit`, async t => {
    const h = harness(t); await h.idle(); h.configure(); h.input('endpoint', endpoint); h.input('network', true); await h.enable(); h.submit();
    assert.equal(h.matching('request').length, 0); assert.equal(h.field('submit').disabled, true);
  });
}

test('explicit payload preserves chosen endpoint/model and Unicode text, with no preview grant', async t => {
  const h = harness(t); await h.idle(); h.configure(); h.input('text', '🐕'.repeat(2000)); h.input('network', true); await h.enable();
  assert.equal(h.submit().defaultPrevented, true); h.submit();
  assert.equal(h.matching('request').length, 1);
  const body = JSON.parse(h.latest('request').options.body);
  assert.equal(body.endpoint, 'https://provider.invalid/v1'); assert.equal(body.model, 'exact-physical-model');
  assert.equal([...body.text].length, 2000); assert.equal(body.allow_network, true);
  assert.equal('allow_unqualified_metadata' in body, false); assert.equal('grant' in body, false);
  assert.equal(h.field('network').checked, false); assert.equal(h.field('acknowledge').checked, false);
});

test('one-shot request stays consumed through pending and leased states', async t => {
  const h = harness(t); await h.prepared(); await h.complete(pending());
  h.submit(); assert.equal(h.matching('request').length, 1);
  await h.tick(500); await h.respond('status', leased()); h.submit();
  assert.equal(h.matching('request').length, 1); assert.equal(h.field('submit').disabled, true);
  const shown = JSON.parse(h.field('result').textContent);
  assert.equal(shown.admitted_command.label, 'CURRENT_COMMAND'); assert.equal(shown.physical_stop_confirmed, false);
  assert.match(shown.note, /not measured movement/);
});

for (const state of ['expired', 'cancelled', 'blocked', 'closed']) {
  test(`${state} ends one-shot authority and hides previously admitted command`, async t => {
    const h = harness(t); await h.prepared(); await h.complete();
    await h.tick(500); await h.respond('status', status(state, { request_id: 'fixture-session:r1', generation: 3 }));
    assert.doesNotMatch(h.field('result').textContent, /CURRENT_COMMAND/); assert.equal(h.field('submit').disabled, true);
    assert.equal(h.field('enable').disabled, true); assert.equal(h.field('acknowledge').checked, false);
    h.input('acknowledge', true); await h.enable(4);
    assert.equal(h.field('submit').disabled, true, 'fresh data consent is required too');
  });
}

test('host strings remain inert text and never become code or route authority', async t => {
  const h = harness(t); await h.prepared();
  const hostile = '<img src=x onerror="fetch(\'/api/start\')"><script>start()</script>';
  const result = leased(); result.service.reason = hostile; result.service.blockers = [hostile];
  await h.complete(result);
  assert.ok(h.field('status').textContent.includes(hostile)); assert.ok(h.field('blockers').textContent.includes(hostile));
  assert.equal(JSON.parse(h.field('result').textContent).reason, hostile);
});

for (const route of ['enable', 'request', 'cancel', 'status']) {
  for (const failure of ['fetch', 'JSON', 'null', 'HTTP', 'not-ok', 'wrong-mode', 'bad-schema']) {
    test(`${route} ${failure} error closes authority without implying torque-off`, async t => {
      const h = harness(t);
      if (route === 'status') { /* initial GET */ }
      else if (route === 'enable') { await h.idle(); h.configure(); h.input('acknowledge', true); h.click('enable'); }
      else { await h.prepared(); if (route === 'request') h.submit(); else h.click('cancel'); }
      const call = h.latest(route);
      if (failure === 'fetch') call.reject();
      else if (failure === 'JSON') call.respond({}, { json: async () => { throw new SyntaxError('bad'); } });
      else if (failure === 'null') call.respond(null);
      else if (failure === 'HTTP') call.respond(status(), { ok: false });
      else if (failure === 'not-ok') call.respond({ ok: false });
      else if (failure === 'wrong-mode') call.respond({ ...status(), mode: 'SHADOW' });
      else call.respond({ ...status(), service: { ...status().service, eligible: 'true' } });
      await flush(); assert.equal(h.field('submit').disabled, true); assert.equal(h.field('enable').disabled, true);
      assert.equal(h.field('cancel').disabled, false); assert.equal(h.field('result').textContent, '');
      assert.match(h.field('status').textContent, /unavailable|unverified|not be verified/i);
    });
  }
}

test('local timeout aborts request but cannot assert that the host did not move', async t => {
  const h = harness(t); await h.prepared(); h.submit(); const call = h.latest('request');
  await h.tick(3000); assert.equal(call.options.signal.aborted, true); assert.match(h.field('status').textContent, /movement cannot be ruled out/);
  assert.equal(h.field('enable').disabled, true); assert.equal(h.field('cancel').disabled, false);
});

for (const phase of ['enable', 'pending', 'leased']) {
  for (const action of ['cancel', 'stop', 'start', 'stand', 'reconnect', 'forward', 'lateral', 'yaw', 'height', 'roll', 'pitch', 'endpoint', 'model', 'text', 'network', 'background', 'pagehide']) {
    test(`${action} fences ${phase} and discards late success`, async t => {
      const h = harness(t); await h.idle(); h.configure(); h.input('acknowledge', true); h.click('enable');
      const oldEnable = h.latest('enable'); let oldRequest;
      if (phase !== 'enable') { await h.respond('enable', enabled()); h.submit(); oldRequest = h.latest('request'); }
      if (phase === 'leased') await h.respond('request', leased('REVOKED_COMMAND'));
      if (action === 'cancel') h.click('cancel');
      else if (action === 'background') { h.document.hidden = true; h.document.dispatch('visibilitychange'); }
      else if (action === 'pagehide') h.window.dispatch('pagehide', { persisted: true });
      else if (['endpoint', 'model', 'text', 'network'].includes(action)) h.input(action, action === 'network' ? false : `${h.field(action).value}-edited`);
      else h.manual(action);
      assert.equal(h.matching('cancel').length, 1); assert.equal(h.field('submit').disabled, true);
      assert.equal(h.field('result').textContent, ''); assert.equal(h.field('network').checked, false);
      if (['background', 'pagehide'].includes(action)) assert.equal(h.latest('cancel').options.keepalive, true);
      if (phase === 'enable') oldEnable.respond(enabled());
      else if (phase === 'pending') oldRequest.respond(leased('LATE_COMMAND'));
      await flush(); assert.equal(h.field('result').textContent, ''); assert.equal(h.field('submit').disabled, true);
      await h.respond('cancel', status('cancelled', { generation: 3 }));
      assert.doesNotMatch(h.field('result').textContent, /COMMAND/);
    });
  }
}

test('acknowledgement withdrawal cancels the unconsumed enable', async t => {
  const h = harness(t); await h.prepared(); h.input('acknowledge', false);
  assert.equal(h.matching('cancel').length, 1); assert.equal(h.field('submit').disabled, true);
});

test('acknowledgement withdrawal fences a pending enable and its late success', async t => {
  const h = harness(t); await h.idle(); h.configure(); h.input('acknowledge', true); h.click('enable');
  const oldEnable = h.latest('enable');
  h.input('acknowledge', false);
  assert.equal(h.matching('cancel').length, 1);
  assert.equal(JSON.parse(h.latest('cancel').options.body).expected_generation, 2);
  assert.equal(h.field('submit').disabled, true);
  assert.equal(h.field('network').checked, false);
  oldEnable.respond(enabled()); await flush(); h.submit();
  assert.equal(h.field('submit').disabled, true);
  assert.equal(h.matching('request').length, 0);
  assert.equal(h.field('acknowledge').checked, false);
  await h.respond('cancel', status('cancelled', { generation: 3 }));
  assert.equal(h.field('enable').disabled, true);
  assert.equal(h.field('submit').disabled, true);
  assert.match(h.field('status').textContent, /authority revoked by the host/);
});

test('polls launched before a new enable cannot overwrite its state', async t => {
  const h = harness(t); await h.idle(); h.configure(); await h.tick(500); const old = h.latest('status');
  await h.enable(); const current = h.field('status').textContent; old.respond(status('blocked', { eligible: false })); await flush();
  assert.equal(h.field('status').textContent, current); assert.equal(h.field('submit').disabled, false);
});

test('polling never races a pending mutation or reopens a consumed submit', async t => {
  const h = harness(t); await h.prepared(); h.submit(); await h.tick(500);
  assert.equal(h.matching('status').length, 1); assert.equal(h.field('submit').disabled, true);
  await h.respond('request', pending()); await h.tick(500); await h.respond('status', pending());
  assert.equal(h.field('submit').disabled, true); h.submit(); assert.equal(h.matching('request').length, 1);
});

test('repeated Cancel completion cannot restore a newer explicit enable or request', async t => {
  const h = harness(t); await h.prepared(); h.submit(); const oldRequest = h.latest('request');
  h.click('cancel'); const oldCancel = h.latest('cancel'); h.click('cancel');
  h.latest('cancel').respond(status('cancelled', { generation: 4 })); await flush();
  h.configure(); await h.enable(5); h.submit(); h.latest('request').respond(leased('NEW_COMMAND', 'fixture-session:r2', 5)); await flush();
  const current = h.field('result').textContent;
  oldRequest.respond(leased('OLD_COMMAND')); oldCancel.respond(enabled()); await flush();
  assert.equal(h.field('result').textContent, current); assert.match(current, /NEW_COMMAND/);
});

test('failed cancellation cannot be undone by fresh enabled or leased host snapshots', async t => {
  const h = harness(t); await h.prepared(); await h.complete(); h.click('cancel'); h.latest('cancel').reject(); await flush();
  const warning = h.field('status').textContent;
  await h.tick(500); await h.respond('status', leased('AFTER_FAILED_CANCEL'));
  assert.equal(h.field('status').textContent, warning); assert.equal(h.field('result').textContent, '');
  h.input('acknowledge', true); h.click('enable'); assert.equal(h.matching('enable').length, 1);
  h.click('cancel'); await h.respond('cancel', status('cancelled', { generation: 3 }));
  h.configure(); await h.enable(4); assert.equal(h.field('submit').disabled, false);
});

test('a cancellation response with active host state never confirms cancellation', async t => {
  const h = harness(t); await h.prepared(); h.click('cancel'); await h.respond('cancel', enabled());
  assert.match(h.field('status').textContent, /unverified/); assert.equal(h.field('enable').disabled, true);
});

for (const change of ['different-id', 'missing-id', 'generation-rollback', 'generation-advance']) {
  test(`foreign host ${change} cannot repaint or authorize the panel`, async t => {
    const h = harness(t); await h.prepared(); await h.complete();
    const changed = leased('FOREIGN_COMMAND', change === 'missing-id' ? null : change === 'different-id' ? 'foreign:r1' : 'fixture-session:r1', change === 'generation-rollback' ? 1 : change === 'generation-advance' ? 3 : 2);
    await h.tick(500); await h.respond('status', changed);
    assert.equal(h.field('result').textContent, ''); assert.equal(h.field('submit').disabled, true);
    assert.match(h.field('status').textContent, /authority changed/);
    await h.tick(500); await h.respond('status', leased('REAPPEARING_COMMAND'));
    assert.equal(h.field('result').textContent, '');
  });
}

test('request response missing its host request ID stays unbound even on later status', async t => {
  const h = harness(t); await h.prepared(); await h.complete(leased('UNBOUND_COMMAND', null));
  assert.equal(h.field('result').textContent, ''); assert.match(h.field('status').textContent, /Unbound/);
  await h.tick(500); await h.respond('status', leased('UNBOUND_COMMAND'));
  assert.equal(h.field('result').textContent, ''); assert.equal(h.field('enable').disabled, true);
});

test('back/forward restoration cannot restore consent, authority, request or old rendering', async t => {
  const h = harness(t); await h.prepared(); await h.complete();
  h.window.dispatch('pagehide', { persisted: true }); const oldCancel = h.latest('cancel');
  h.window.dispatch('pageshow', { persisted: true });
  assert.equal(h.field('acknowledge').checked, false); assert.equal(h.field('network').checked, false);
  assert.equal(h.field('enable').disabled, true); assert.equal(h.field('submit').disabled, true);
  oldCancel.respond(status('cancelled', { generation: 3 })); await flush();
  await h.respond('status', leased('RESTORED_OLD_COMMAND'));
  assert.equal(h.field('result').textContent, ''); assert.equal(h.field('enable').disabled, true);
  h.click('cancel'); await h.respond('cancel', status('cancelled', { generation: 4 }));
  h.configure(); await h.enable(5); await h.complete(leased('FRESH_REQUEST', 'fixture-session:r2', 5));
  assert.match(h.field('result').textContent, /FRESH_REQUEST/);
});

test('hidden page cannot enable or submit, including programmatic events', async t => {
  const h = harness(t); await h.prepared(); h.document.hidden = true; h.document.dispatch('visibilitychange');
  h.input('network', true); h.input('acknowledge', true); h.click('enable'); h.submit();
  assert.equal(h.matching('request').length, 0); assert.equal(h.matching('enable').length, 1);
});

test('enable and request bodies bind to exact host generation, cancel fences anticipated enable', async t => {
  const h = harness(t); await h.idle(); h.configure(); h.input('acknowledge', true); h.click('enable');
  assert.deepEqual(JSON.parse(h.latest('enable').options.body), { allow_physical: true, expected_generation: 1 });
  h.click('cancel');
  assert.deepEqual(JSON.parse(h.latest('cancel').options.body), { expected_generation: 2 });
  h.latest('enable').respond(enabled()); await flush();
  await h.respond('cancel', status('cancelled', { generation: 3 }));
  h.configure(); await h.enable(4); h.submit();
  assert.deepEqual(Object.keys(JSON.parse(h.latest('request').options.body)).sort(), ['allow_network', 'endpoint', 'expected_generation', 'model', 'text']);
  assert.equal(JSON.parse(h.latest('request').options.body).expected_generation, 4);
  h.click('cancel'); assert.equal(JSON.parse(h.latest('cancel').options.body).expected_generation, 4);
});

test('foreign enabled generation cannot be used as this page operator permission', async t => {
  const h = harness(t); await h.prepared(); await h.tick(500); await h.respond('status', enabled(3));
  assert.match(h.field('status').textContent, /authority changed/); h.submit();
  assert.equal(h.matching('request').length, 0); assert.equal(h.field('enable').disabled, true);
});

test('request response with new active generation cannot borrow another enable', async t => {
  const h = harness(t); await h.prepared(); await h.complete(leased('FOREIGN_GENERATION', 'fixture-session:r1', 3));
  assert.equal(h.field('result').textContent, ''); assert.equal(h.field('enable').disabled, true);
  assert.match(h.field('status').textContent, /Unbound/);
});

test('enable response with unexpected generation cannot establish authority', async t => {
  const h = harness(t); await h.idle(); h.configure(); h.input('acknowledge', true); h.click('enable');
  await h.respond('enable', enabled(8)); h.submit();
  assert.equal(h.matching('request').length, 0); assert.equal(h.field('enable').disabled, true);
});

test('an older terminal cancellation snapshot cannot confirm revocation', async t => {
  const h = harness(t); await h.prepared(); h.click('cancel');
  await h.respond('cancel', status('cancelled', { generation: 2 }));
  assert.match(h.field('status').textContent, /unverified/); assert.equal(h.field('enable').disabled, true);
});

test('reported blockers override a contradictory eligible flag', async t => {
  const h = harness(t); await h.idle(status('blocked', { eligible: true, blockers: ['transport_unverified'] }));
  h.input('acknowledge', true); h.click('enable');
  assert.equal(h.matching('enable').length, 0); assert.equal(h.field('enable').disabled, true);
});

test('withdrawn acknowledgement also cancels a consumed pending request', async t => {
  const h = harness(t); await h.prepared(); await h.complete(pending());
  h.input('acknowledge', true); h.input('acknowledge', false);
  assert.equal(h.matching('cancel').length, 1); assert.equal(h.field('submit').disabled, true);
});

test('the complete page has unique IDs and every authored script parses', () => {
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(ids).size, ids.length);
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
  assert.equal(scripts.length, 2, 'physical panel remains separate from existing shadow/live script');
  for (const [index, script] of scripts.entries()) new vm.Script(script[1], { filename: `${htmlPath}:script-${index}` });
});

function manualBoundary({ injectedToken = true, powered = false } = {}) {
  const constants = html.slice(html.indexOf('    const operatorToken ='), html.indexOf('    function values()'));
  const helpers = html.slice(html.indexOf('    async function request('), html.indexOf('    function renderStatus('));
  const renderer = html.slice(html.indexOf('    function renderStatus('), html.indexOf('    async function poll('));
  const leave = html.match(/    window\.addEventListener\("pagehide", \(\) => \{[\s\S]*?\n    \}\);/)[0];
  const restore = html.match(/    window\.addEventListener\("pageshow", event => \{[\s\S]*?\n    \}\);/)[0];
  const calls = [], cleared = [], window = new EventTargetFake();
  const ui = new Proxy({}, { get: (target, name) => target[name] ??= { textContent: '', value: '0', disabled: false } });
  const context = vm.createContext({ ui, window,
    clearTimeout: timer => cleared.push(timer),
    fetch: async (url, options) => {
      assert.match(url, /^\/api\/(status|stand|start|stop|command|reconnect|record\/start|record\/stop|servo-telemetry)$/);
      calls.push({ url, options });
      return { ok: true, json: async () => ({ ok: true, manual_generation: 8, link_state: 'ready',
        clocked_policy_ready: true, policy_active: false, policy_commanding: false, holding_pose: false,
        policy_detail: 'offline fixture' }) };
    },
  });
  const code = `${constants}\n${helpers}\n${renderer}\n${leave}\n${restore}\npowered = ${powered}; live = true; commandTimer = 23; manualGeneration = 7;`;
  vm.runInContext(injectedToken ? code.replaceAll('__PIXEL_SHADOW_TOKEN__', 'e'.repeat(64)) : code, context);
  return { calls, cleared, window, ui, run: code => vm.runInContext(code, context) };
}

for (const route of ['stand', 'start', 'stop', 'command', 'reconnect', 'record/start', 'record/stop', 'servo-telemetry']) {
  test(`manual ${route} mutation carries the local token and cannot follow redirects`, async () => {
    const h = manualBoundary(); await h.run(`post('/api/${route}', {forward: 0})`);
    assert.equal(h.calls.length, 1);
    const { url, options } = h.calls[0];
    assert.equal(url, `/api/${route}`); assert.equal(options.method, 'POST');
    assert.equal(options.headers['X-Pixel-Shadow-Token'], 'e'.repeat(64));
    assert.equal(options.headers['Content-Type'], 'application/json');
    assert.equal(options.credentials, 'omit'); assert.equal(options.redirect, 'error');
    assert.equal(options.cache, 'no-store'); assert.equal(options.keepalive, false);
    const body = JSON.parse(options.body);
    if (route === 'start' || route === 'stand') assert.equal(body.expected_manual_generation, 7);
    else assert.equal('expected_manual_generation' in body, false);
  });
}

test('manual mutations with no injected operator token fail locally while read-only status remains available', async () => {
  const h = manualBoundary({ injectedToken: false });
  await assert.rejects(h.run("post('/api/start')"), /Local operator token unavailable/);
  assert.equal(h.calls.length, 0); assert.match(h.ui.message.textContent, /Reload/);
  await h.run("request('/api/status')");
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].url, '/api/status');
});

test('manual pagehide clears pending command timers and sends token-authenticated keepalive Stop', async () => {
  const h = manualBoundary({ powered: true }); h.window.dispatch('pagehide'); await flush();
  assert.deepEqual(h.cleared, [23]); assert.equal(h.run('live'), false);
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].url, '/api/stop');
  assert.equal(h.calls[0].options.headers['X-Pixel-Shadow-Token'], 'e'.repeat(64));
  assert.equal(h.calls[0].options.keepalive, true); assert.equal(h.calls[0].options.credentials, 'omit');
  const idle = manualBoundary(); idle.window.dispatch('pagehide'); await flush(); assert.equal(idle.calls.length, 0);
});

for (const route of ['start', 'stand']) {
  test(`manual pagehide fences pending ${route} before powered status has arrived`, async () => {
    const h = manualBoundary();
    const pending = h.run(`post('/api/${route}')`);
    assert.equal(h.run('pendingManualStarts'), 1); assert.equal(h.run('powered'), false);
    h.window.dispatch('pagehide'); await pending; await flush();
    assert.deepEqual(h.calls.map(call => call.url), [`/api/${route}`, '/api/stop']);
    assert.equal(JSON.parse(h.calls[0].options.body).expected_manual_generation, 7);
    assert.equal(h.calls[1].options.keepalive, true);
    assert.equal(h.calls[1].options.headers['X-Pixel-Shadow-Token'], 'e'.repeat(64));
    assert.equal(h.run('manualPageActive'), false); assert.equal(h.run('pendingManualStarts'), 0);
    assert.equal(h.ui.start.disabled, true); assert.equal(h.ui.stand.disabled, true);
    await assert.rejects(h.run(`post('/api/${route}')`), /Fresh controller status/);
    assert.equal(h.calls.length, 2, 'a late successful response cannot authorize a hidden-page restart');
  });
}

test('manual Start and Stand require fresh generation and reject duplicate in-flight admission', async () => {
  const h = manualBoundary(); h.run('manualGeneration = null');
  await assert.rejects(h.run("post('/api/start')"), /Fresh controller status/);
  await assert.rejects(h.run("post('/api/stand')"), /Fresh controller status/);
  assert.equal(h.calls.length, 0);
  h.run('manualGeneration = 7'); const pending = h.run("post('/api/start')");
  await assert.rejects(h.run("post('/api/stand')"), /Fresh controller status/);
  await pending; assert.equal(h.calls.length, 1); assert.equal(h.run('pendingManualStarts'), 0);
});

test('manual restored pages refresh generation without restarting, and stale status cannot roll it back', async () => {
  const h = manualBoundary(); h.window.dispatch('pagehide'); await flush();
  h.window.dispatch('pageshow', { persisted: true });
  assert.equal(h.run('manualGeneration'), null); assert.equal(h.ui.start.disabled, true);
  await flush(); assert.equal(h.run('manualGeneration'), 8);
  assert.deepEqual(h.calls.map(call => call.url), ['/api/status']);
  h.run('renderStatus({manual_generation: 3})'); assert.equal(h.run('manualGeneration'), 8);
  assert.deepEqual(h.calls.map(call => call.url), ['/api/status'], 'restoration never starts an operation');
});

// JSON mode runs this same source through Node's TAP reporter, and records
// actual top-level outcomes (including after-hook assertions), not hand-counted
// successes. It never invokes npm, a browser, the network, or a live service.
// Usage: node pixel_robot/tools/test_physical_ui.cjs --output /tmp/physical-ui.json
//        node pixel_robot/tools/test_physical_ui.cjs --json
const args = process.argv.slice(2);
if (args.length) {
  const outputMode = args.length === 2 && args[0] === '--output';
  const jsonMode = args.length === 1 && args[0] === '--json';
  if (!outputMode && !jsonMode) throw new Error('Usage: test_physical_ui.cjs [--json | --output PATH]');
  const child = spawnSync(process.execPath, ['--test', '--test-reporter=tap', __filename], {
    encoding: 'utf8', timeout: 30000, maxBuffer: 4 * 1024 * 1024,
    env: { ...process.env, PIXEL_PHYSICAL_UI_EXPECTED_HTML_SHA256: sha256(htmlBytes),
      PIXEL_PHYSICAL_UI_EXPECTED_TEST_SHA256: sha256(testBytes) },
  });
  const tap = child.stdout || '';
  const outcomes = [...tap.matchAll(/^(not ok|ok) \d+ - (.+)$/gm)];
  const unescapeTap = name => name.replace(/\\#/g, '#');
  const testIds = outcomes.map(match => unescapeTap(match[2]));
  const passedIds = outcomes.filter(match => match[1] === 'ok').map(match => unescapeTap(match[2]));
  const failures = outcomes.filter(match => match[1] === 'not ok').map(match => unescapeTap(match[2]));
  const summary = {};
  for (const counter of ['tests', 'pass', 'fail', 'cancelled', 'skipped', 'todo']) {
    const matches = [...tap.matchAll(new RegExp(`^# ${counter} (\\d+)$`, 'gm'))];
    if (matches.length !== 1) failures.push(`missing or duplicate TAP summary: ${counter}`);
    summary[counter] = matches.length === 1 ? Number(matches[0][1]) : null;
  }
  const { skipped, cancelled, todo } = summary;
  if (child.error) failures.push(`runner error: ${child.error.message}`);
  if (testIds.length !== cases.length) failures.push(`expected ${cases.length} tests, observed ${testIds.length}`);
  if (new Set(testIds).size !== testIds.length) failures.push('duplicate test IDs');
  if (testIds.some((id, index) => id !== cases[index]?.name)) failures.push('reported tests differ from registered tests');
  if (summary.tests !== testIds.length) failures.push('TAP summary test count differs from observed outcomes');
  if (summary.pass !== passedIds.length) failures.push('TAP summary pass count differs from observed successes');
  if (summary.fail !== outcomes.filter(match => match[1] === 'not ok').length) failures.push('TAP summary failure count differs from observed failures');
  if (!new RegExp(`^1\\.\\.${cases.length}$`, 'm').test(tap)) failures.push('missing complete TAP test plan');
  if (skipped !== 0) failures.push(`${skipped} tests were skipped`);
  if (cancelled !== 0) failures.push(`${cancelled} tests were cancelled`);
  if (todo !== 0) failures.push(`${todo} tests were marked todo`);
  if (child.status !== 0 && !failures.length) failures.push(`test runner exited ${child.status}: ${(child.stderr || '').trim()}`);
  const report = {
    schema_version: 1,
    scope: 'pixel-production-physical-panel-dom-stub-tests',
    status: failures.length === 0 && child.status === 0 ? 'passed' : 'failed',
    test_count: testIds.length, test_ids: testIds, passed_test_ids: passedIds,
    skipped, cancelled, todo, failures,
    production_html_path: 'pixel_robot/app/src/main/assets/robot_control.html',
    html_sha256: sha256(htmlBytes),
    script_sha256: sha256(Buffer.from(source, 'utf8')),
    production_html_sha256: sha256(htmlBytes),
    production_script_sha256: sha256(Buffer.from(source, 'utf8')),
    test_source_sha256: sha256(testBytes),
    node_version: process.version,
    network_access: false, dom_environment: 'stub', android_webview_verified: false, hardware_access: false,
    live_control_script_executed: false, physical_execution: false,
    manual_request_boundary_executed: true,
  };
  const json = `${JSON.stringify(report, null, 2)}\n`;
  if (outputMode) {
    fs.mkdirSync(path.dirname(path.resolve(args[1])), { recursive: true });
    fs.writeFileSync(args[1], json);
  }
  process.stdout.write(json);
  if (report.status !== 'passed') process.stderr.write(tap + (child.stderr || ''));
  process.exitCode = report.status === 'passed' ? 0 : 1;
} else {
  const { test: runTest } = require('node:test');
  for (const { name, run } of cases) runTest(name, run);
}

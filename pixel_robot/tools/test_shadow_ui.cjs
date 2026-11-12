#!/usr/bin/env node
'use strict';

// Offline contract tests for the *actual* SHADOW panel. No browser, provider,
// Android service, live-control script, network access, or hardware is used.
// Run from any directory: node --test pixel_robot/tools/test_shadow_ui.cjs
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
  ['PIXEL_SHADOW_UI_EXPECTED_HTML_SHA256', sha256(htmlBytes)],
  ['PIXEL_SHADOW_UI_EXPECTED_TEST_SHA256', sha256(testBytes)],
]) {
  if (process.env[name]) assert.equal(actual, process.env[name], 'test inputs changed while starting report run');
}
const marker = '// This panel never calls';
const start = html.indexOf(marker);
assert.notEqual(start, -1, 'actual shadow IIFE marker must exist');
const end = html.indexOf('</script>', start);
assert.notEqual(end, -1, 'shadow script closing tag must exist');
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
  set innerHTML(_) { this.htmlWrites++; throw new Error('SHADOW output must use textContent, never innerHTML'); }
  get innerHTML() { throw new Error('SHADOW must not read or interpret HTML output'); }
  insertAdjacentHTML() { this.htmlWrites++; throw new Error('SHADOW must not insert HTML output'); }
}

const status = (state = 'idle', service = {}, provider = 'idle') => ({
  ok: true, provider_state: provider,
  service: { state, request_id: state === 'idle' ? null : 'fixture-session:r1',
    physical_execution: false, physical_stop_confirmed: false, ...service },
});
const accepted = (label = 'NEW_PREVIEW', requestId = 'fixture-session:r1') => status('shadow_accepted', {
  request_id: requestId,
  preview: { label, forward_mps: 0.1, lateral_mps: 0, yaw_rps: 0, duration_ms: 500 },
}, 'completed');
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function harness(t) {
  const elements = new Map();
  const tags = /<(input|textarea|button|form|p|pre)\b([^>]*\bid="shadow-[^"]+"[^>]*)>([^<]*)/g;
  for (const match of html.matchAll(tags)) {
    const element = new ElementFake(match[1], match[2], match[3]);
    elements.set(element.id, element);
  }
  const document = new EventTargetFake();
  document.hidden = false;
  document.getElementById = id => {
    assert.ok(elements.has(id), `SHADOW only addresses actual panel elements: ${id}`);
    return elements.get(id);
  };
  const window = new EventTargetFake();
  const calls = [];
  const violations = [];
  const timers = new Map();
  let timerId = 0;
  const setTimeoutFake = (callback, ms) => { timers.set(++timerId, { callback, ms }); return timerId; };
  const fetchFake = (url, options = {}) => {
    // Record before rejecting so a swallowed error cannot conceal live-route use.
    if (!/^\/api\/shadow\/(status|request|cancel|stop)$/.test(url)) {
      violations.push(url);
      return Promise.reject(new Error(`Forbidden network route: ${url}`));
    }
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    const call = { url, options, settled: false,
      respond(data, response = {}) {
        this.settled = true;
        resolve({ ok: true, status: 200, json: async () => data, ...response });
      },
      reject(error = new Error('offline simulated failure')) { this.settled = true; reject(error); },
    };
    if (options.signal) options.signal.addEventListener('abort', () => call.reject(new Error('simulated AbortError')));
    calls.push(call);
    return promise;
  };
  const storage = new Proxy({}, { get() { throw new Error('SHADOW must not persist endpoint, text or consent'); } });
  const context = vm.createContext({ document, window, URL, AbortController,
    fetch: fetchFake, setTimeout: setTimeoutFake, clearTimeout: id => timers.delete(id),
    localStorage: storage, sessionStorage: storage,
    navigator: { sendBeacon(url) { violations.push(`beacon:${url}`); throw new Error('Unexpected beacon'); } },
  });
  vm.runInContext(source, context, { filename: htmlPath, timeout: 1000 });
  const h = {
    document, window, calls, timers,
    field: name => document.getElementById(`shadow-${name}`),
    matching: route => calls.filter(call => call.url === `/api/shadow/${route}`),
    latest(route) { return this.matching(route).at(-1); },
    input(name, value) {
      const field = this.field(name);
      if (typeof value === 'boolean') field.checked = value;
      else field.value = value;
      field.dispatch('input');
    },
    configure() {
      this.input('endpoint', 'https://provider.invalid/v1');
      this.input('model', 'exact-shadow-model');
      this.input('text', 'Preview one small step');
      this.input('network', true);
    },
    submit() { return this.field('form').dispatch('submit'); },
    click(name) { this.field(name).dispatch('click'); },
    async respond(route, data, response) {
      const call = this.matching(route).find(item => !item.settled);
      assert.ok(call, `expected pending ${route} request`);
      call.respond(data, response);
      await flush();
      return call;
    },
    async tick(ms) {
      for (const [id, timer] of [...timers]) {
        if (timer.ms === ms) { timers.delete(id); timer.callback(); }
      }
      await flush();
    },
    async idle() { await this.respond('status', status()); },
    async complete(data = accepted()) { this.submit(); await this.respond('request', data); },
  };
  t.after(() => {
    assert.deepEqual(violations, [], 'no live /api/start, command, stand, reconnect, external fetch or beacons');
    for (const element of elements.values()) assert.equal(element.htmlWrites, 0, `${element.id} must render text only`);
    for (const call of calls) {
      assert.equal(call.options.method, call.url.endsWith('/status') ? 'GET' : 'POST');
      assert.equal(call.options.cache, 'no-store');
      assert.ok(call.options.headers['X-Pixel-Shadow-Token'], 'all shadow routes must carry page token');
    }
  });
  return h;
}

test('actual panel starts blank, with no endpoint/model/consent and no provider submission', async t => {
  const h = harness(t);
  for (const name of ['endpoint', 'model', 'text']) assert.equal(h.field(name).value, '');
  assert.equal(h.field('network').checked, false);
  assert.equal(h.field('unqualified').checked, false);
  assert.equal(h.field('submit').disabled, true);
  assert.equal(h.field('cancel').disabled, true);
  h.submit();
  assert.equal(h.matching('request').length, 0);
  assert.deepEqual(h.calls.map(call => call.url), ['/api/shadow/status']);
  await h.respond('status', accepted('PREVIOUS_TAB_PREVIEW'));
  assert.doesNotMatch(h.field('result').textContent, /PREVIOUS_TAB_PREVIEW/);
  assert.equal(h.field('submit').disabled, true);
});

test('each required field and explicit current consent gates submission', async t => {
  const h = harness(t); await h.idle();
  h.configure();
  assert.equal(h.field('submit').disabled, false);
  for (const name of ['endpoint', 'model', 'text']) {
    const value = h.field(name).value;
    h.input(name, '');
    h.input('network', true);
    assert.equal(h.field('submit').disabled, true, `${name} is required`);
    h.submit();
    h.input(name, value);
    assert.equal(h.field('network').checked, false, `${name} edit revokes consent`);
    assert.equal(h.field('submit').disabled, true);
    h.input('network', true);
  }
  h.input('network', false); h.submit();
  assert.equal(h.matching('request').length, 0);
});

for (const endpoint of ['http://provider.invalid/v1', 'http://localhost:18081/v1', 'http://192.168.1.2:18081/v1', 'https://user:secret@provider.invalid/v1',
  'https://provider.invalid/v1?token=secret', 'https://provider.invalid/v1#secret',
  'https://provider.invalid', '/v1', 'not a URL']) {
  test(`endpoint validation rejects ${endpoint}`, async t => {
    const h = harness(t); await h.idle(); h.configure();
    h.input('endpoint', endpoint); h.input('network', true); h.submit();
    assert.equal(h.field('submit').disabled, true);
    assert.equal(h.matching('request').length, 0);
  });
}

for (const endpoint of ['http://127.0.0.1:18081/v1', 'http://[::1]:18081/v1']) {
  test(`literal phone-local endpoint requires explicit consent: ${endpoint}`, async t => {
    const h = harness(t); await h.idle(); h.configure();
    h.input('endpoint', endpoint); h.submit();
    assert.equal(h.matching('request').length, 0);
    h.input('network', true); h.submit();
    assert.equal(h.matching('request').length, 1);
    assert.equal(JSON.parse(h.latest('request').options.body).endpoint, endpoint);
  });
}

test('text limit counts Unicode code points, and preview requires no unqualified opt-in', async t => {
  const h = harness(t); await h.idle(); h.configure();
  h.input('text', '🐕'.repeat(2001)); h.input('network', true); h.submit();
  assert.equal(h.matching('request').length, 0);
  h.input('text', '🐕'.repeat(2000)); h.input('network', true); h.submit();
  const body = JSON.parse(h.latest('request').options.body);
  assert.equal([...body.text].length, 2000);
  assert.equal(body.allow_unqualified_metadata, false);
  assert.deepEqual(Object.keys(body).sort(), ['allow_network', 'allow_unqualified_metadata', 'endpoint', 'model', 'text']);
});

test('double submit produces one request and preserves explicit endpoint/model/text', async t => {
  const h = harness(t); await h.idle(); h.configure(); h.input('unqualified', true);
  assert.equal(h.submit().defaultPrevented, true); h.submit();
  assert.equal(h.matching('request').length, 1);
  assert.equal(h.field('submit').disabled, true);
  assert.equal(h.field('cancel').disabled, false);
  assert.deepEqual(JSON.parse(h.latest('request').options.body), {
    endpoint: 'https://provider.invalid/v1', model: 'exact-shadow-model', text: 'Preview one small step',
    allow_network: true, allow_unqualified_metadata: true,
  });
});

for (const state of ['clarify', 'unsupported']) {
  test(`${state} and HTML-looking provider strings are inert text`, async t => {
    const h = harness(t); await h.idle(); h.configure();
    const hostile = '<img src=x onerror="fetch(\'/api/start\')"><script>start()</script>';
    const result = status(state, { question: hostile, unsupported_reason: hostile, reason: hostile }, 'completed');
    result.provider_error = hostile;
    await h.complete(result);
    assert.ok(h.field('status').textContent.includes(hostile));
    const rendered = JSON.parse(h.field('result').textContent);
    assert.equal(rendered.state, state);
    assert.equal(rendered.question, hostile);
    assert.equal(rendered.unsupported_reason, hostile);
    assert.equal(rendered.reason, hostile);
    assert.equal(rendered.physical_execution, false);
    assert.equal(rendered.physical_stop_confirmed, false);
    assert.equal(h.field('cancel').disabled, true);
    assert.equal(h.field('submit').disabled, false);
  });
}

for (const failure of ['fetch rejection', 'malformed JSON', 'null JSON', 'HTTP error', 'rejected result']) {
  test(`request ${failure} fails closed and leaves cancellation possible`, async t => {
    const h = harness(t); await h.idle(); h.configure(); h.submit();
    const call = h.latest('request');
    if (failure === 'fetch rejection') call.reject();
    else if (failure === 'malformed JSON') call.respond(undefined, { json: async () => { throw new SyntaxError('bad JSON'); } });
    else if (failure === 'null JSON') call.respond(null);
    else if (failure === 'HTTP error') call.respond(status(), { ok: false, status: 500 });
    else call.respond({ ok: false });
    await flush();
    assert.match(h.field('status').textContent, /failed|unavailable/i);
    assert.equal(h.field('result').textContent, '');
    assert.equal(h.field('cancel').disabled, false);
    h.click('cancel');
    assert.equal(h.matching('cancel').length, 1);
  });
}

test('request deadline aborts the local fetch without disabling cancellation', async t => {
  const h = harness(t); await h.idle(); h.configure(); h.submit();
  const call = h.latest('request');
  await h.tick(3000);
  assert.equal(call.options.signal.aborted, true);
  assert.match(h.field('status').textContent, /failed|unavailable/i);
  assert.equal(h.field('cancel').disabled, false);
});

test('completed provider with an accepted host lease still permits Cancel', async t => {
  const h = harness(t); await h.idle(); h.configure(); await h.complete();
  assert.match(h.field('result').textContent, /NEW_PREVIEW/);
  assert.equal(h.field('cancel').disabled, false);
  h.click('cancel');
  assert.equal(h.field('result').textContent, '');
  await h.respond('cancel', status('cancelled'));
  assert.equal(h.field('cancel').disabled, true);
  assert.doesNotMatch(h.field('result').textContent, /NEW_PREVIEW/);
});

for (const phase of ['pending', 'accepted']) {
  for (const action of ['endpoint', 'model', 'text', 'network', 'unqualified', 'background', 'pagehide']) {
    test(`${action} revokes ${phase} host work and suppresses late output`, async t => {
      const h = harness(t); await h.idle(); h.configure(); h.submit();
      const oldRequest = h.latest('request');
      if (phase === 'accepted') await h.respond('request', accepted('REVOKED_PREVIEW'));
      if (action === 'background') { h.document.hidden = true; h.document.dispatch('visibilitychange'); }
      else if (action === 'pagehide') h.window.dispatch('pagehide', { persisted: true });
      else h.input(action, ['network', 'unqualified'].includes(action) ? !h.field(action).checked : `${h.field(action).value}-edited`);
      assert.equal(h.matching('cancel').length, 1);
      if (['endpoint', 'model', 'text'].includes(action)) assert.equal(h.field('network').checked, false);
      if (['background', 'pagehide'].includes(action)) assert.equal(h.latest('cancel').options.keepalive, true);
      assert.equal(h.field('result').textContent, '');
      if (phase === 'pending') { oldRequest.respond(accepted('LATE_REVOKED_PREVIEW')); await flush(); }
      assert.doesNotMatch(h.field('result').textContent, /REVOKED_PREVIEW/);
      await h.respond('cancel', status('cancelled'));
      assert.doesNotMatch(h.field('result').textContent, /REVOKED_PREVIEW/);
    });
  }
}

test('old status started before submit cannot overwrite the newer request result', async t => {
  const h = harness(t);
  const oldStatus = h.latest('status'); h.configure(); await h.complete(accepted('CURRENT_REQUEST'));
  const current = h.field('result').textContent;
  oldStatus.respond(accepted('STALE_STATUS')); await flush();
  assert.equal(h.field('result').textContent, current);
  assert.doesNotMatch(h.field('result').textContent, /STALE_STATUS/);
});

test('status polling cannot reopen submission while the request POST is in flight', async t => {
  const h = harness(t); await h.idle(); h.configure(); h.submit();
  await h.tick(500);
  const poll = h.matching('status').find(call => !call.settled);
  // A status GET may reach the host before its concurrent request POST.
  if (poll) { poll.respond(status()); await flush(); }
  assert.equal(h.field('submit').disabled, true);
  h.submit();
  assert.equal(h.matching('request').length, 1);
});

test('same-generation older status cannot replace a completed request result', async t => {
  const h = harness(t); await h.idle(); h.configure(); h.submit();
  await h.tick(500);
  const olderPoll = h.matching('status').find(call => !call.settled);
  await h.respond('request', accepted('LATEST_COMPLETED_RESULT'));
  const current = h.field('result').textContent;
  if (olderPoll) { olderPoll.respond(status('pending', {}, 'requesting')); await flush(); }
  assert.equal(h.field('result').textContent, current);
  assert.equal(h.field('submit').disabled, false);
});

test('old request and cancel completions cannot overwrite a subsequent explicit submit', async t => {
  const h = harness(t); await h.idle(); h.configure(); h.submit();
  const oldRequest = h.latest('request'); h.click('cancel');
  const oldCancel = h.latest('cancel'); h.submit();
  assert.equal(h.matching('request').length, 1, 'cancellation must settle before resubmission');
  // A newer Stop supersedes the delayed Cancel. Its completion permits a fresh
  // submission while the old request/cancel responses may still arrive late.
  h.click('stop'); await h.respond('stop', status('stop_requested')); h.submit();
  assert.equal(h.matching('request').length, 2);
  h.latest('request').respond(accepted('LATEST_REQUEST', 'fixture-session:r2')); await flush();
  const current = h.field('result').textContent;
  oldRequest.respond(accepted('OLD_REQUEST')); oldCancel.respond(status('cancelled')); await flush();
  assert.equal(h.field('result').textContent, current);
  assert.match(current, /LATEST_REQUEST/);
});

test('failed cancellation and fresh status cannot restore an old preview before explicit submit', async t => {
  const h = harness(t); await h.idle(); h.configure(); await h.complete(accepted('OLD_PREVIEW'));
  h.click('cancel'); h.latest('cancel').reject(); await flush();
  assert.match(h.field('status').textContent, /Could not verify|unavailable/i);
  await h.tick(500); await h.respond('status', accepted('OLD_PREVIEW'));
  assert.doesNotMatch(h.field('result').textContent, /OLD_PREVIEW/);
  assert.equal(h.field('cancel').disabled, false, 'unverified lease may still need cancellation');
  await h.tick(500); await h.respond('status', accepted('OLD_PREVIEW'));
  assert.doesNotMatch(h.field('result').textContent, /OLD_PREVIEW/);
  await h.complete(accepted('EXPLICIT_NEW_PREVIEW', 'fixture-session:r2'));
  assert.match(h.field('result').textContent, /EXPLICIT_NEW_PREVIEW/);
});

test('a different host request ID suppresses stale preview until the next explicit submit', async t => {
  const h = harness(t); await h.idle(); h.configure();
  await h.complete(accepted('CURRENT_PREVIEW', 'fixture-session:r2'));
  await h.tick(500); await h.respond('status', accepted('OTHER_REQUEST_PREVIEW', 'fixture-session:r1'));
  assert.doesNotMatch(h.field('result').textContent, /CURRENT_PREVIEW|OTHER_REQUEST_PREVIEW/);
  await h.tick(500); await h.respond('status', accepted('CURRENT_PREVIEW', 'fixture-session:r2'));
  assert.doesNotMatch(h.field('result').textContent, /CURRENT_PREVIEW/);
  await h.complete(accepted('EXPLICIT_NEXT_PREVIEW', 'fixture-session:r3'));
  assert.match(h.field('result').textContent, /EXPLICIT_NEXT_PREVIEW/);
});

test('a request response without a host-issued request ID cannot authorize preview rendering', async t => {
  const h = harness(t); await h.idle(); h.configure();
  const missingId = accepted('UNBOUND_PREVIEW'); delete missingId.service.request_id;
  await h.complete(missingId);
  assert.doesNotMatch(h.field('result').textContent, /UNBOUND_PREVIEW/);
  assert.equal(h.field('cancel').disabled, false);
  await h.tick(500); await h.respond('status', accepted('LATER_UNBOUND_PREVIEW'));
  assert.doesNotMatch(h.field('result').textContent, /UNBOUND_PREVIEW/);
});

test('Stop is a separate shadow-only request and blocks older completion', async t => {
  const h = harness(t); await h.idle(); h.configure(); h.submit();
  const oldRequest = h.latest('request'); h.click('stop');
  assert.equal(h.matching('stop').length, 1);
  await h.respond('stop', status('stop_requested'));
  oldRequest.respond(accepted('AFTER_STOP')); await flush();
  assert.doesNotMatch(h.field('result').textContent, /AFTER_STOP/);
});

test('failed status polling keeps pending Cancel and Stop available', async t => {
  const h = harness(t); await h.idle(); h.configure(); h.submit();
  await h.respond('request', status('pending', {}, 'requesting'));
  await h.tick(500); h.latest('status').reject(); await flush();
  assert.equal(h.field('cancel').disabled, false);
  assert.equal(h.field('stop').disabled, false);
  assert.match(h.field('status').textContent, /unavailable/i);
});

test('back/forward restore clears consent and preview, with no automatic resubmission', async t => {
  const h = harness(t); await h.idle(); h.configure(); await h.complete(accepted('BEFORE_NAVIGATION'));
  h.window.dispatch('pagehide', { persisted: true });
  const cancel = h.latest('cancel');
  h.window.dispatch('pageshow', { persisted: true });
  assert.equal(h.field('network').checked, false);
  assert.equal(h.field('submit').disabled, true);
  assert.equal(h.matching('request').length, 1);
  cancel.respond(accepted('OLD_CANCEL_COMPLETION')); await flush();
  await h.respond('status', accepted('RESTORED_SERVER_PREVIEW'));
  assert.doesNotMatch(h.field('result').textContent, /BEFORE_NAVIGATION|OLD_CANCEL_COMPLETION|RESTORED_SERVER_PREVIEW/);
  h.submit();
  assert.equal(h.matching('request').length, 1);
  h.input('network', true);
  await h.respond('cancel', status('cancelled'));
  await h.complete(accepted('AFTER_EXPLICIT_RESUBMIT', 'fixture-session:r2'));
  assert.match(h.field('result').textContent, /AFTER_EXPLICIT_RESUBMIT/);
});

// JSON mode runs this same source through Node's TAP reporter, and records
// actual top-level outcomes (including after-hook assertions), not hand-counted
// successes. It never invokes npm, a browser, the network, or a live service.
// Usage: node pixel_robot/tools/test_shadow_ui.cjs --output /tmp/shadow-ui.json
//        node pixel_robot/tools/test_shadow_ui.cjs --json
const args = process.argv.slice(2);
if (args.length) {
  const outputMode = args.length === 2 && args[0] === '--output';
  const jsonMode = args.length === 1 && args[0] === '--json';
  if (!outputMode && !jsonMode) throw new Error('Usage: test_shadow_ui.cjs [--json | --output PATH]');
  const child = spawnSync(process.execPath, ['--test', '--test-reporter=tap', __filename], {
    encoding: 'utf8', timeout: 30000, maxBuffer: 4 * 1024 * 1024,
    env: { ...process.env, PIXEL_SHADOW_UI_EXPECTED_HTML_SHA256: sha256(htmlBytes),
      PIXEL_SHADOW_UI_EXPECTED_TEST_SHA256: sha256(testBytes) },
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
    scope: 'pixel-production-shadow-panel-dom-stub-tests',
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
    live_control_script_executed: false,
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

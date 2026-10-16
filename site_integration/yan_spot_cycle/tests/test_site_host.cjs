'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto, createHash} = require('node:crypto');
const {execFileSync} = require('node:child_process');
const os = require('node:os');
const root = path.resolve(__dirname, '../../..');
const here = path.resolve(__dirname, '..');

test('the authored public text projects into the existing browser contract with unchanged prompts', async () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'yan-content-'));
  try {
    const target = path.join(temporary, 'site');
    execFileSync(process.env.BRIDGE_TEST_PYTHON || 'python', ['-m',
      'site_integration.yan_spot_cycle.build_candidate', '--output', target], {cwd: root});
    const learner = require(path.join(target, 'lesson-assets/spot-learner.js'));
    const bytes = fs.readFileSync(path.join(target, 'lesson-assets/learner-presentation.json'));
    const artifact = await learner.loadPresentation(async () => ({ok: true,
      arrayBuffer: async () => Uint8Array.from(bytes).buffer}), webcrypto);
    const source = JSON.parse(fs.readFileSync(path.join(here, 'content/lesson.ui.json')));
    for (const [index, card] of artifact.cards.entries()) {
      assert.ok(card.presentation.body.includes(source.exercises[index].lesson_content.question));
      for (const fact of source.shared_student_facts) assert.ok(card.presentation.body.includes(fact));
      assert.equal(createHash('sha256').update(card.presentation.body).digest('hex'), card.source_sha256);
      assert.deepEqual(card.presentation.inputs.count.options, [0, 1]);
      assert.deepEqual(Object.keys(card).sort(), ['exercise_id', 'presentation', 'source_sha256']);
    }
    for (const name of ['reviewer-only', 'lesson.teacher.json', 'LESSON_RU.md', 'cms']) {
      assert.equal(fs.existsSync(path.join(target, name)), false);
    }
  } finally {
    assert.equal(path.dirname(path.resolve(temporary)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(temporary).startsWith('yan-content-'));
    fs.rmSync(temporary, {recursive: true, force: true});
  }
});

async function boot(invalid = false) {
  const prefix = '/index.php?route=practice%2Fspot-cycle%2F';
  const config = {transport: prefix + 'transport', savedAnswer: prefix + 'saved-answer',
    presentation: prefix + 'assets%2Flearner-presentation.json'};
  if (invalid) config.transport = 'https://other.invalid/index.php?route=practice/spot-cycle/transport';
  const calls = [], message = {}, submit = {};
  let mounted;
  const window = {location: new URL('https://site.test/ru/practice/spot-cycle/'),
    BridgeSpotLearner: {loadPresentation: async fetcher => {await fetcher(); return {cards: []};}},
    BridgeLesson: {mountSessionStore: (store, options) => {
      mounted = {store, options}; return {ready: Promise.resolve()};}}};
  const document = {getElementById: id => id === 'bridge-lesson-host'
      ? {textContent: JSON.stringify(config)} : id === 'connection-status' ? message : submit,
    querySelector: () => ({content: 'synthetic-csrf'})};
  vm.runInNewContext(fs.readFileSync(path.join(here, 'site/js/spot-cycle-site-host.js'), 'utf8'),
    {window, document, URL, fetch: async (url, options) => {
      calls.push({url, options}); return {ok: true, status: 200, json: async () => ({ok: true})};}});
  await new Promise(resolve => setImmediate(resolve));
  return {calls, mounted, message, submit};
}

test('the PHP query routes carry the existing transport and CSRF contract', async () => {
  const {calls, mounted} = await boot();
  assert.ok(mounted);
  await mounted.store.resume(); await mounted.store.execute({command: {kind: 'RESPONSE'}});
  await mounted.store.acknowledge({delivery_id: 'd', result_sha256: 'hash'});
  await mounted.options.loadSavedAnswer();
  for (const call of calls) {
    assert.equal(new URL(call.url).origin, 'https://site.test');
    assert.equal(call.options.credentials, 'same-origin');
    assert.equal(call.options.cache, 'no-store');
  }
  assert.deepEqual(calls.filter(c => c.options.method === 'POST').map(c =>
    JSON.parse(c.options.body).operation), ['resume', 'execute', 'acknowledge']);
  assert.equal(calls[1].options.headers['X-CSRF-Token'], 'synthetic-csrf');
  assert.equal(new URL(calls.at(-1).url).searchParams.get('route'), 'practice/spot-cycle/saved-answer');
});

test('an endpoint outside the site cannot receive a lesson request', async () => {
  const {calls, mounted, submit, message} = await boot(true);
  assert.equal(calls.length, 0); assert.equal(mounted, undefined);
  assert.equal(submit.disabled, true);
  assert.match(message.textContent, /временно недоступно/);
});

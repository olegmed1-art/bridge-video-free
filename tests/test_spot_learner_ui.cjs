'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {webcrypto} = require('node:crypto');
const learner = require('../bridge_school_api/static/spot_cycle_session/spot-learner.js');
const {createClient} = require('../bridge_school_api/static/spot_cycle_session/teacher-client.js');

const bytes = readFileSync(join(__dirname,
  '../bridge_school_api/static/spot_cycle_session/learner-presentation.json'));
const fetchBytes = source => async () => ({ok: true, arrayBuffer: async () =>
  Uint8Array.from(source).buffer});

test('loads the exact authored learner text and only the two public cards', async () => {
  const artifact = await learner.loadPresentation(fetchBytes(bytes), webcrypto);
  assert.deepEqual(artifact.cards.map(card => card.exercise_id), ['spot.a7c2', 'spot.f3b8']);
  const serialized = JSON.stringify(artifact);
  for (const key of ['teacher_snapshot', 'expected_solution', 'defenders_remaining_cards',
    'hidden_hands', 'answer_key']) assert.equal(serialized.includes('"' + key + '"'), false);
  for (const [id, hand, expected] of [
    ['spot.a7c2', {D: ['A'], H: ['2']}, ['DA', 'H2']],
    ['spot.f3b8', {C: ['A'], H: ['A']}, ['CA', 'HA']],
  ]) {
    const card = learner.cardFor(artifact, {candidate_preview: true,
      prompt: {exercise_id: id, presentation: {hand}}});
    assert.deepEqual(card.options.map(option => option.value), expected);
    assert.ok(card.body.length > 300);
    assert.deepEqual(learner.responseFrom(card, expected[0], 1, 'Моё объяснение'),
      {first_play: expected[0], count: 1, explanation: 'Моё объяснение'});
    assert.throws(() => learner.responseFrom(card, 'S3', 1, 'reason'));
    assert.throws(() => learner.responseFrom(card, expected[0], 2, 'reason'));
    assert.throws(() => learner.responseFrom(card, expected[0], 1, ' '));
  }
});

test('rejects changed presentation bytes and a choice absent from the visible hand', async () => {
  const changed = Buffer.from(bytes);
  changed[changed.length - 2] = changed[changed.length - 2] === 32 ? 33 : 32;
  await assert.rejects(learner.loadPresentation(fetchBytes(changed), webcrypto),
    /INVALID_LEARNER_PRESENTATION/);
  const artifact = await learner.loadPresentation(fetchBytes(bytes), webcrypto);
  assert.throws(() => learner.cardFor(artifact, {candidate_preview: true,
    prompt: {exercise_id: 'spot.a7c2', presentation: {hand: {D: ['A']}}}}),
  /INVALID_LEARNER_PRESENTATION/);
});

test('teacher transport receives only the bound three-field spot answer', async () => {
  const boundary = {actual_mastery: null, criterion_met: null, learner_error: null,
    persisted: false, student_profile_written: false, curriculum_activated: false,
    mastery_event_created: false};
  const assignment = {...boundary, status: 'ASSIGNED', session_id: 'local-synthetic',
    issuance_id: 'issuance-1', method_version: 'local-teacher-routing-v1',
    session_mode: 'SPOT_CANDIDATE_PREVIEW_V1', automatic_assessment_enabled: false,
    approved_catalog_member: false, context_exposure: 'PUBLIC', support_history: 'NONE',
    prompt: {exercise_id: 'spot.a7c2', prompt_sha256: 'a'.repeat(64),
      response_schema: 'low-card-structured-response-v1', method_version: 'spot-routing-handoff-v2',
      presentation: {title: 'Synthetic spot', target_suit: 'D',
        hand: {S: ['3'], H: ['2'], D: ['A', 'K'], C: ['A']},
        dummy: {H: ['A'], D: ['7', '6', '5'], C: ['2']},
        questions: [{id: 'guaranteed_spot_tricks_from_position', type: 'integer',
          prompt: 'How many?', minimum: 0, maximum: 1}]}}};
  let recorded;
  const transport = {current: async () => assignment,
    record_response: async args => {recorded = args; return {...boundary,
      status: 'RECORDED_PENDING_FEEDBACK', issuance_id: 'issuance-1',
      response_no: 1, response_sha256: 'b'.repeat(64)};},
    request_hint: async () => {throw Error('unexpected hint');},
    advance: async () => {throw Error('unexpected advance');}};
  const client = createClient(transport, {makeCommandId: () => 'command-1'});
  await client.connect();
  assert.throws(() => client.dispatch({type: 'first_answer', response: {
    first_play: 'S3', count: 1, explanation: 'wrong choice'}}),
  /Use the three authored response fields/);
  await client.dispatch({type: 'first_answer', response: {
    first_play: 'DA', count: 1, explanation: 'My own reason'}});
  assert.deepEqual(recorded.response, {schema: 'spot-first-play-response-v2',
    exercise_id: 'spot.a7c2', prompt_sha256: 'a'.repeat(64),
    first_play: 'DA', guaranteed_spot_tricks_from_position: 1,
    explanation: 'My own reason'});
  assert.equal(client.getState().receipt.status, 'RECORDED_PENDING_FEEDBACK');
});

'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {positions, initialState, nextState} = require('../bridge_school_api/static/spot_cycle/two-spot.js');

const source = JSON.parse(fs.readFileSync(path.join(__dirname,
  '../data/research/spot_card_candidate_v2/routing-handoff/student-prompts.json'), 'utf8'));

test('both visible positions match the pinned public prompts', () => {
  assert.deepEqual(positions.map(p => p.id), ['spot.a7c2', 'spot.f3b8']);
  for (const position of positions) {
    const prompt = source.prompts.find(p => p.exercise_id === position.id).presentation;
    assert.deepEqual(position.hand, prompt.hand);
    assert.deepEqual(position.dummy, prompt.dummy);
    assert.deepEqual(position.played.split(', ').map(card => {
      const suits = {'♠': 'S', '♥': 'H', '♦': 'D', '♣': 'C'};
      return suits[card[0]] + card.slice(1);
    }), prompt.given_information.played_in_target_suit);
    assert.equal(Object.values(position.hand).flat().length, 5);
    assert.equal(Object.values(position.dummy).flat().length, 5);
  }
});

test('choice and explanation precede feedback, then practice changes position', () => {
  let state = initialState();
  assert.equal(state.index, 0);
  assert.equal(nextState(state, {type: 'NEXT'}), state);
  const first = {exercise_id: positions[0].id, first_play: 'DA', guaranteed: 1,
    explanation: 'After the high diamonds, the heart entry reaches dummy.'};
  assert.equal(nextState(state, {type: 'ANSWER', answer: first}), state);
  state = nextState(state, {type: 'ANSWER', answer: first,
    feedback: {exercise_id: first.exercise_id, choice_and_count_match: true}});
  assert.equal(state.phase, 'feedback');
  state = nextState(state, {type: 'NEXT'});
  assert.equal(state.phase, 'question');
  assert.equal(state.index, 1);
  assert.equal(state.answer, null);
  const second = {exercise_id: positions[1].id, first_play: 'CA', guaranteed: 0,
    explanation: 'No entry to dummy remains.'};
  state = nextState(state, {type: 'ANSWER', answer: second,
    feedback: {exercise_id: second.exercise_id, choice_and_count_match: true}});
  assert.equal(state.phase, 'feedback');
  assert.equal(nextState(state, {type: 'NEXT'}).index, 0);
});

'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const {normalizeReview, mountTeacherReview} = require('../bridge_school_api/static/spot_cycle_session/teacher-review.js');

class FakeNode {
  constructor(tag, document) { this.tag = tag; this.ownerDocument = document; this.children = []; this._text = ''; this.hidden = false; this.handlers = {}; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._text = ''; this.children = [...nodes]; }
  setAttribute() {}
  addEventListener(type, handler) { this.handlers[type] = handler; }
  click() { this.handlers.click?.(); }
}
class FakeDocument { createElement(tag) { return new FakeNode(tag, this); } }
const container = () => new FakeNode('div', new FakeDocument());
const review = (explanation = 'После старших бубен ♥2 ведёт к ♥A стола.') => ({
  schema: 'bridge-await-teacher-review-v1', status: 'AWAIT_TEACHER_REVIEW',
  exercise_id: 'spot.a7c2',
  session_id: 'owned-session', issuance_id: 'spot-issuance', response_no: 1,
  next_assignment: null, can_send_new_command: false,
  saved_answer: {first_play: 'DA', guaranteed_spot_tricks_from_position: 1, explanation},
});
const parts = root => root.children[0].children;

test('only terminal review with saved answer and no next assignment is accepted', () => {
  assert.equal(normalizeReview(review()).first_play, 'DA');
  assert.equal(normalizeReview(review()).guaranteed, 1);
  assert.equal(normalizeReview({...review(), exercise_id: 'spot.f3b8',
    saved_answer: {first_play: 'CA', guaranteed_spot_tricks_from_position: 0,
      explanation: 'К столу нет входа.'}}).guaranteed, 0);
  for (const invalid of [
    {...review(), status: 'NEXT_ACTIVITY'},
    {...review(), next_assignment: {exercise_id: 'spot.f3b8'}},
    {...review(), can_send_new_command: true},
    {...review(), expected_solution: 'teacher-only'},
    {...review(), saved_answer: {...review().saved_answer, first_play: ''}},
    {...review(), saved_answer: {...review().saved_answer, guaranteed_spot_tricks_from_position: 2}},
  ]) assert.throws(() => normalizeReview(invalid), /INVALID_REVIEW_PROJECTION/);
});

test('reopening and refreshing reload authoritative saved answer', async () => {
  let calls = 0;
  const load = async () => { calls++; return review(calls === 1 ? 'Первый ответ.' : 'Ответ после повторного открытия.'); };
  const firstRoot = container();
  const first = mountTeacherReview(firstRoot, load);
  await first.ready;
  assert.equal(first.getPhase(), 'review');
  assert.match(parts(firstRoot)[1].textContent, /Ожидаем проверки преподавателя/);
  assert.match(parts(firstRoot)[2].textContent, /первый ход: ♦A/);
  assert.match(parts(firstRoot)[2].textContent, /фоской: 1/);
  assert.match(parts(firstRoot)[3].textContent, /Первый ответ/);
  assert.equal(parts(firstRoot)[4].hidden, true);
  first.dispose();

  const reopenedRoot = container();
  const reopened = mountTeacherReview(reopenedRoot, load);
  await reopened.ready;
  assert.equal(calls, 2);
  assert.match(parts(reopenedRoot)[3].textContent, /Ответ после повторного открытия/);
  await reopened.refresh();
  assert.equal(calls, 3);
  assert.equal(reopened.getPhase(), 'review');
});

test('server failure hides stale answer and allows a retry without assigning work', async () => {
  let available = true;
  const root = container();
  const component = mountTeacherReview(root, async () => {
    if (!available) throw new Error('network unavailable');
    return review();
  });
  await component.ready;
  assert.match(parts(root)[2].textContent, /♦A/);
  available = false;
  await component.refresh();
  assert.equal(component.getPhase(), 'unavailable');
  assert.equal(parts(root)[2].textContent, '');
  assert.equal(parts(root)[3].textContent, '');
  assert.match(parts(root)[1].textContent, /сервер недоступен/);
  assert.equal(parts(root)[4].hidden, false);
  assert.equal(parts(root).some(node => /следующее задание|mastery/i.test(node.textContent)), false);
  available = true;
  await component.refresh();
  assert.equal(component.getPhase(), 'review');
});

test('reopening while the server is down never displays a cached answer', async () => {
  const root = container();
  const component = mountTeacherReview(root, async () => { throw new Error('offline'); });
  await component.ready;
  assert.equal(component.getPhase(), 'unavailable');
  assert.equal(parts(root)[2].textContent, '');
  assert.equal(parts(root)[3].textContent, '');
  assert.equal(parts(root)[4].hidden, false);
});

test('an unsafe server projection is hidden without exposing a next task', async () => {
  const root = container();
  const component = mountTeacherReview(root, async () => ({...review(), next_assignment: {exercise_id: 'spot.f3b8'}}));
  await component.ready;
  assert.equal(component.getPhase(), 'unavailable');
  assert.equal(parts(root)[2].textContent, '');
  assert.match(parts(root)[1].textContent, /данные сервера не прошли проверку/);
});

test('older refresh cannot replace a newer authoritative response', async () => {
  let resolveOld;
  const old = new Promise(resolve => { resolveOld = resolve; });
  let calls = 0;
  const root = container();
  const component = mountTeacherReview(root, ({signal}) => {
    calls++;
    if (calls === 1) { assert.equal(signal.aborted, false); return old; }
    return Promise.resolve(review('Новый подтверждённый ответ.'));
  });
  await component.refresh();
  resolveOld(review('Старый ответ.'));
  await component.ready;
  assert.match(parts(root)[3].textContent, /Новый подтверждённый/);
  assert.doesNotMatch(parts(root)[3].textContent, /Старый ответ/);
});

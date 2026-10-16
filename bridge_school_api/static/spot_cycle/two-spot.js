'use strict';

// Public student material only. The model answers live in the loopback server.
const positions = Object.freeze([
  {
    id: 'spot.a7c2', title: 'Бубны: как добраться до фоски?', target: 'бубновой',
    hand: {S: ['3'], H: ['2'], D: ['A', 'K'], C: ['A']},
    dummy: {S: [], H: ['A'], D: ['7', '6', '5'], C: ['2']},
    choices: [['DA', '♦A'], ['H2', '♥2']],
    task: 'Выбери первый ход для плана. Сколько будущих взяток именно ♦7/♦6/♦5 можно гарантировать: 0 или 1?',
    played: '♦2, ♦3, ♦4, ♦8',
  },
  {
    id: 'spot.f3b8', title: 'Трефы: фоска готова, а вход?', target: 'трефовой',
    hand: {S: ['3'], H: ['A'], D: ['A'], C: ['A', 'K']},
    dummy: {S: [], H: ['2'], D: ['2'], C: ['7', '6', '5']},
    choices: [['CA', '♣A'], ['HA', '♥A']],
    task: 'Выбери первый ход для проверки трефового плана. Сколько будущих взяток именно ♣7/♣6/♣5 можно гарантировать: 0 или 1?',
    played: '♣2, ♣3, ♣4, ♣8',
  },
]);
const suitSymbols = {S: '♠', H: '♥', D: '♦', C: '♣'};
const suitNames = {S: 'пики', H: 'червы', D: 'бубны', C: 'трефы'};

function initialState() { return {phase: 'question', index: 0, attempts: 0, answer: null, feedback: null}; }
function nextState(state, event) {
  if (event.type === 'ANSWER' && state.phase === 'question' && event.answer && event.feedback &&
      event.answer.exercise_id === positions[state.index].id && event.feedback.exercise_id === positions[state.index].id) {
    return {...state, phase: 'feedback', attempts: state.attempts + 1,
      answer: event.answer, feedback: event.feedback};
  }
  if (event.type === 'NEXT' && state.phase === 'feedback') {
    return {phase: 'question', index: (state.index + 1) % positions.length,
      attempts: state.attempts, answer: null, feedback: null};
  }
  return state;
}

function renderHand(container, hand) {
  container.replaceChildren();
  for (const suit of ['S', 'H', 'D', 'C']) {
    const row = document.createElement('div'); row.className = 'partial-suit';
    row.setAttribute('aria-label', suitNames[suit]);
    const marker = document.createElement('span'); marker.className = 'suit-row-label' + ('HD'.includes(suit) ? ' red-suit' : '');
    marker.textContent = suitSymbols[suit];
    const cards = document.createElement('div'); cards.className = 'cards';
    if (!hand[suit].length) { const empty = document.createElement('span'); empty.className = 'empty-suit'; empty.textContent = '—'; cards.append(empty); }
    for (const rank of hand[suit]) {
      const card = document.createElement('span'); card.className = 'playing-card' + ('HD'.includes(suit) ? ' red-card' : '');
      card.setAttribute('aria-label', suitNames[suit] + ' ' + rank);
      const value = document.createElement('span'); value.textContent = rank;
      const glyph = document.createElement('b'); glyph.textContent = suitSymbols[suit];
      card.append(value, glyph); cards.append(card);
    }
    row.append(marker, cards); container.append(row);
  }
}

function mount() {
  const get = id => document.getElementById(id);
  const form = get('answer-form');
  let state = initialState();
  function render() {
    const position = positions[state.index];
    get('exercise-progress').textContent = `Задача ${state.index + 1} из ${positions.length}`;
    get('lesson-number').textContent = String(state.index + 1).padStart(2, '0');
    get('exercise-title').textContent = position.title;
    get('task-text').textContent = position.task;
    get('count-question').textContent = `Сколько взяток ${position.target} фоской гарантировано?`;
    renderHand(get('dummy-cards'), position.dummy);
    renderHand(get('hand-cards'), position.hand);
    get('conditions').replaceChildren();
    for (const text of [
      `В целевой масти уже сыграны ${position.played}.`,
      'Оставшиеся карты этой масти у защиты разделены 2–2; точные ранги и другие масти скрыты.',
      'Игра без козыря. Считай взятку фоской стола, а не старшей картой руки.',
    ]) { const li = document.createElement('li'); li.textContent = text; get('conditions').append(li); }
    get('first-play-options').replaceChildren();
    for (const [value, label] of position.choices) {
      const choice = document.createElement('label');
      const input = document.createElement('input'); input.type = 'radio'; input.name = 'first_play'; input.value = value;
      const text = document.createElement('span'); text.textContent = label;
      choice.append(input, text); get('first-play-options').append(choice);
    }
    if (state.phase === 'question') {
      form.reset(); form.hidden = false; get('feedback').hidden = true; get('form-error').hidden = true;
    } else {
      form.hidden = true; get('feedback').hidden = false;
      get('feedback-title').textContent = state.feedback.choice_and_count_match
        ? 'Ход и число совпали с эталоном.' : 'Сверь ход и число с разбором.';
      get('your-explanation').textContent = `Ваше объяснение: ${state.answer.explanation}`;
      get('feedback-copy').textContent = state.feedback.model;
      get('feedback-review').textContent = state.feedback.explanation_review;
      get('retry-button').textContent = state.index === 0 ? 'Повторить на новой позиции' : 'Начать две задачи заново';
      get('feedback-title').focus?.();
    }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const first = form.querySelector('input[name="first_play"]:checked');
    const count = form.querySelector('input[name="guaranteed"]:checked');
    const explanation = get('reason').value.trim();
    if (!first || !count || !explanation) {
      get('form-error').textContent = 'Выбери первый ход и 0 или 1, затем объясни свой план.';
      get('form-error').hidden = false; return;
    }
    const answer = {exercise_id: positions[state.index].id, first_play: first.value,
      guaranteed: Number(count.value), explanation};
    try {
      const response = await fetch('./feedback', {method: 'POST', cache: 'no-store',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(answer)});
      if (!response.ok) throw new Error('Обратная связь сейчас недоступна. Попробуй ещё раз.');
      const feedback = await response.json();
      state = nextState(state, {type: 'ANSWER', answer, feedback});
      render();
    } catch (error) {
      get('form-error').textContent = error.message;
      get('form-error').hidden = false;
    }
  });
  get('retry-button').addEventListener('click', () => { state = nextState(state, {type: 'NEXT'}); render(); });
  render();
}

if (typeof module !== 'undefined') module.exports = {positions, initialState, nextState};
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
}

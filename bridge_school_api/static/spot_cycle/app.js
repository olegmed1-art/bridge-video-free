'use strict';

// The open cards and the given 2–2 condition come from the approved compact spot cycle.
const position = Object.freeze({
  dummy: { S: [], H: ['A'], D: ['7', '6', '5'], C: ['2'] },
  hand: { S: ['3'], H: ['2'], D: ['A', 'K'], C: ['A'] }
});
const suitSymbols = { S: '♠', H: '♥', D: '♦', C: '♣' };
const suitNames = { S: 'пики', H: 'червы', D: 'бубны', C: 'трефы' };

function nextState(state, event) {
  if (event.type === 'ANSWER') {
    if (!['0', '1'].includes(event.value) || !event.reason.trim()) return state;
    return { phase: 'feedback', attempts: state.attempts + 1, answer: event.value,
      reason: event.reason.trim() };
  }
  if (event.type === 'RETRY' && state.phase === 'feedback') {
    return { ...state, phase: 'question', answer: null, reason: '' };
  }
  return state;
}

function renderHand(container, hand) {
  for (const suit of ['S', 'H', 'D', 'C']) {
    const row = document.createElement('div');
    row.className = 'partial-suit';
    row.setAttribute('aria-label', suitNames[suit]);
    const marker = document.createElement('span');
    marker.className = 'suit-row-label' + ('HD'.includes(suit) ? ' red-suit' : '');
    marker.textContent = suitSymbols[suit];
    const cards = document.createElement('div');
    cards.className = 'cards';
    if (hand[suit].length === 0) {
      const empty = document.createElement('span');
      empty.className = 'empty-suit';
      empty.textContent = '—';
      cards.append(empty);
    }
    for (const rank of hand[suit]) {
      const card = document.createElement('span');
      card.className = 'playing-card' + ('HD'.includes(suit) ? ' red-card' : '');
      card.setAttribute('aria-label', suitNames[suit] + ' ' + rank);
      const value = document.createElement('span'); value.textContent = rank;
      const glyph = document.createElement('b'); glyph.textContent = suitSymbols[suit];
      card.append(value, glyph); cards.append(card);
    }
    row.append(marker, cards); container.append(row);
  }
}

function mount() {
  const form = document.getElementById('answer-form');
  const feedback = document.getElementById('feedback');
  const error = document.getElementById('form-error');
  let state = { phase: 'question', attempts: 0, answer: null, reason: '' };
  renderHand(document.getElementById('dummy-cards'), position.dummy);
  renderHand(document.getElementById('hand-cards'), position.hand);

  form.addEventListener('submit', event => {
    event.preventDefault();
    const picked = form.querySelector('input[name="answer"]:checked');
    const reason = document.getElementById('reason').value;
    if (!picked || !reason.trim()) {
      error.textContent = 'Выбери 0 или 1 и коротко объясни план.';
      error.hidden = false;
      return;
    }
    state = nextState(state, { type: 'ANSWER', value: picked.value, reason });
    error.hidden = true;
    form.hidden = true;
    feedback.hidden = false;
    feedback.classList.toggle('correct', state.answer === '1');
    document.getElementById('feedback-title').textContent = state.answer === '1'
      ? 'Верно: одну взятку можно гарантировать.'
      : 'Попробуй ещё раз: вход на стол здесь есть.';
    document.getElementById('feedback-copy').textContent =
      'После ♦A и ♦K у защиты не остаётся бубен по условию 2–2. ' +
      'Фоски ♦7, ♦6, ♦5 стола выработаны. ♥2 из руки к ♥A стола даёт вход: ' +
      'одна бубновая фоска сможет взять взятку. Это и есть разница между ' +
      '«выработать фоску» и «добраться до неё».';
    document.getElementById('attempt-count').textContent = 'Попытка ' + state.attempts + '. Объяснение показано после ответа.';
    document.getElementById('feedback-title').focus?.();
  });

  document.getElementById('retry-button').addEventListener('click', () => {
    state = nextState(state, { type: 'RETRY' });
    form.reset();
    form.hidden = false;
    feedback.hidden = true;
    error.hidden = true;
    form.querySelector('input[name="answer"]').focus();
  });
}

if (typeof module !== 'undefined') module.exports = { nextState, position };
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
}

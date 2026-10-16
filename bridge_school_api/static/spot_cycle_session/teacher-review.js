'use strict';
(function (root) {
  const SCHEMA = 'bridge-await-teacher-review-v1';
  const CARD = /^[SHDC](?:[2-9TJQKA])$/;
  const SUITS = {S: '♠', H: '♥', D: '♦', C: '♣'};
  const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const limited = (value, max) => typeof value === 'string' && value.trim() && value.length <= max;

  function normalizeReview(value) {
    if (!plain(value) || Object.keys(value).sort().join(',') !==
        ['schema', 'status', 'exercise_id', 'session_id', 'issuance_id', 'response_no',
          'next_assignment', 'can_send_new_command', 'saved_answer'].sort().join(',')) {
      throw new Error('INVALID_REVIEW_PROJECTION');
    }
    if (value.schema !== SCHEMA || value.status !== 'AWAIT_TEACHER_REVIEW' ||
        !['spot.a7c2', 'spot.f3b8'].includes(value.exercise_id) ||
        value.next_assignment !== null || value.can_send_new_command !== false ||
        !limited(value.session_id, 120) || !limited(value.issuance_id, 120) ||
        !Number.isInteger(value.response_no) || value.response_no < 1 ||
        !plain(value.saved_answer) || Object.keys(value.saved_answer).sort().join(',') !==
          ['first_play', 'guaranteed_spot_tricks_from_position', 'explanation'].sort().join(',') ||
        typeof value.saved_answer.first_play !== 'string' ||
        !CARD.test(value.saved_answer.first_play) ||
        !Number.isInteger(value.saved_answer.guaranteed_spot_tricks_from_position) ||
        ![0, 1].includes(value.saved_answer.guaranteed_spot_tricks_from_position) ||
        !limited(value.saved_answer.explanation, 4000)) {
      throw new Error('INVALID_REVIEW_PROJECTION');
    }
    return {exercise_id: value.exercise_id, session_id: value.session_id,
      issuance_id: value.issuance_id, response_no: value.response_no,
      first_play: value.saved_answer.first_play,
      guaranteed: value.saved_answer.guaranteed_spot_tricks_from_position,
      explanation: value.saved_answer.explanation};
  }

  function mountTeacherReview(container, load) {
    if (!container?.ownerDocument || typeof load !== 'function') throw new TypeError('review host and loader required');
    const doc = container.ownerDocument;
    const element = (tag, content) => {
      const node = doc.createElement(tag);
      if (content !== undefined) node.textContent = content;
      return node;
    };
    const section = element('section');
    section.setAttribute('aria-label', 'Ожидание проверки преподавателя');
    const heading = element('h2', 'Ответ сохранён');
    const status = element('p'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    const answers = element('ul');
    const explanation = element('p');
    const retry = element('button', 'Повторить загрузку'); retry.type = 'button';
    section.append(heading, status, answers, explanation, retry);
    container.replaceChildren(section);
    let generation = 0, controller = null, phase = 'loading';
    function clearAnswer() { answers.replaceChildren(); explanation.textContent = ''; }
    async function refresh() {
      if (phase === 'disposed') return 'disposed';
      const current = ++generation;
      controller?.abort(); controller = new AbortController();
      phase = 'loading'; clearAnswer(); retry.hidden = true;
      status.textContent = 'Проверяем сохранённый ответ…';
      try {
        const review = normalizeReview(await load({signal: controller.signal}));
        if (current !== generation || phase === 'disposed') return phase;
        const first = review.first_play;
        answers.append(element('li', `Ваш первый ход: ${SUITS[first[0]]}${first.slice(1)}`));
        answers.append(element('li', `Гарантированных взяток фоской: ${review.guaranteed}`));
        explanation.textContent = `Ваше объяснение: ${review.explanation}`;
        status.textContent = 'Ответ сохранён. Ожидаем проверки преподавателя. Следующего задания пока нет.';
        phase = 'review';
      } catch (error) {
        if (current !== generation || phase === 'disposed') return phase;
        clearAnswer(); retry.hidden = false;
        status.textContent = error?.message === 'INVALID_REVIEW_PROJECTION'
          ? 'Не удалось подтвердить сохранённый ответ: данные сервера не прошли проверку. Повторите загрузку.'
          : 'Не удалось подтвердить сохранённый ответ: сервер недоступен. Повторите загрузку.';
        phase = 'unavailable';
      }
      return phase;
    }
    retry.addEventListener('click', () => { void refresh(); });
    const ready = refresh();
    return {ready, refresh, getPhase: () => phase, dispose() {
      generation++; controller?.abort(); phase = 'disposed'; container.replaceChildren();
    }};
  }

  const api = {normalizeReview, mountTeacherReview};
  if (typeof module !== 'undefined') module.exports = api;
  else root.BridgeTeacherReview = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);

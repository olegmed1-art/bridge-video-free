'use strict';
(function () {
  function endpoint(value, route) {
    const url = new URL(value, window.location.href);
    if (url.origin !== window.location.origin || url.username || url.password ||
        !url.pathname.endsWith('/index.php') ||
        url.searchParams.get('route') !== 'practice/spot-cycle/' + route) {
      throw new Error('INVALID_SITE_ENDPOINT');
    }
    return url.href;
  }
  async function start() {
    const config = JSON.parse(document.getElementById('bridge-lesson-host').textContent);
    const transport = endpoint(config.transport, 'transport');
    const savedAnswer = endpoint(config.savedAnswer, 'saved-answer');
    const presentation = endpoint(config.presentation, 'assets/learner-presentation.json');
    async function call(operation, value) {
      const headers = {'Content-Type': 'application/json'};
      const csrf = document.querySelector('meta[name="bridge-school-csrf-token"]')?.content;
      if (csrf) headers['X-CSRF-Token'] = csrf;
      const response = await fetch(transport, {method: 'POST', headers,
        credentials: 'same-origin', cache: 'no-store', body: JSON.stringify({operation, value})});
      const data = await response.json();
      if (!response.ok) {
        const error = new Error('LESSON_UNAVAILABLE');
        error.code = 'LESSON_UNAVAILABLE'; error.definitive = response.status < 500;
        throw error;
      }
      return data;
    }
    async function loadSavedAnswer() {
      const response = await fetch(savedAnswer, {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) throw new Error('SAVED_ANSWER_UNAVAILABLE');
      return response.json();
    }
    const spotPresentation = await window.BridgeSpotLearner.loadPresentation(() =>
      fetch(presentation, {credentials: 'same-origin', cache: 'no-store'}));
    const store = {resume: () => call('resume', {}), execute: value => call('execute', value),
      acknowledge: value => call('acknowledge', value)};
    const lesson = window.BridgeLesson.mountSessionStore(store, {spotPresentation, loadSavedAnswer});
    await lesson.ready;
  }
  void start().catch(() => {
    const message = document.getElementById('connection-status');
    if (message) message.textContent = 'Задание временно недоступно. Попробуйте позже.';
    const submit = document.getElementById('check-button');
    if (submit) submit.disabled = true;
  });
})();

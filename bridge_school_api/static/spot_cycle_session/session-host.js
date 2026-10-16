'use strict';
(function () {
  async function call(operation, value) {
    const headers = {'Content-Type': 'application/json'};
    const csrf = document.querySelector('meta[name="bridge-school-csrf-token"]')?.content;
    if (csrf) headers['X-CSRF-Token'] = csrf;
    const response = await fetch('./transport', {
      method: 'POST',
      headers,
      cache: 'no-store',
      body: JSON.stringify({operation, value}),
    });
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(data.detail || 'LOCAL_SESSION_TRANSPORT_ERROR');
      error.code = data.detail || 'LOCAL_SESSION_TRANSPORT_ERROR';
      error.definitive = response.status < 500;
      throw error;
    }
    return data;
  }
  const store = {
    resume: () => call('resume', {}),
    execute: value => call('execute', value),
    acknowledge: value => call('acknowledge', value),
  };
  async function loadSavedAnswer() {
    const response = await fetch('./saved-answer', {cache: 'no-store'});
    if (!response.ok) throw new Error('SAVED_ANSWER_UNAVAILABLE');
    return response.json();
  }
  async function start() {
    const spotPresentation = await window.BridgeSpotLearner.loadPresentation();
    const lesson = window.BridgeLesson.mountSessionStore(store, {spotPresentation, loadSavedAnswer});
    await lesson.ready;
  }
  void start().catch(error => console.error('Local lesson session unavailable:', error.code || error.message));
})();

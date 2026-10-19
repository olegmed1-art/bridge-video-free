<section class="bridge-diana19" aria-label="Учебные разборы Диана19">
  <p class="stage">Staging · учебные разборы</p>
  <h1>Диана19: учебные разборы</h1>
  <p>Выберите вопрос, запишите ответ и откройте объяснение. Автоматической оценки нет.</p>
  <p class="scope-note">Точность ASR по аудио не подтверждена; это не проверенные цитаты преподавателя.</p>
  <label for="lesson-picker">Выберите урок</label><select id="lesson-picker"></select>
  <p id="diana-status" role="status" aria-live="polite">Загрузка уроков…</p>
  <section id="lesson-content" hidden>
    <h2 id="lesson-title" tabindex="-1"></h2><p id="lesson-question"></p>
    <div id="visible-holdings" class="holdings"></div>
    <ul id="lesson-conditions"></ul><div id="lesson-options"></div>
    <form id="answer-form">
      <label for="learner-answer">Ваш ответ и объяснение</label>
      <textarea id="learner-answer" rows="5" maxlength="4000" required></textarea>
      <button id="save-answer" type="submit">Сохранить ответ и открыть разбор</button>
    </form>
    <section id="saved-response" hidden><h3>Ваш сохранённый ответ</h3><p id="saved-answer"></p>
      <p>Ответ сохранён без оценки. Освоение темы автоматически не установлено.</p></section>
    <section id="lesson-explanation" hidden><h3>Разбор при заданных условиях</h3>
      <div id="explanation-text"></div><p id="source-timecode"></p>
      <a id="source-link" target="_blank" rel="noopener noreferrer">Открыть исходное видео</a>
      <p id="explanation-scope" class="scope-note"></p>
    </section>
  </section>
</section>

"""Optional reviewed visible-suit exercises with server-owned answer stages.

The accepted three-lesson journal and teacher engine are unchanged. New stage
answers use a separate existing DianaJournal, keyed by exercise and stage.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .diana19_staging import DianaJournal, DianaStagingService, digest

VISIBLE_IDS = frozenset({'diana19-visible-exercise-001', 'diana19-visible-exercise-002'})


class VisibleStageService(DianaStagingService):
    def __init__(self, entries, journal, base=None):
        super().__init__(None, journal)
        self.entries = {item['exercise_id']: item for item in entries}
        if set(self.entries) != VISIBLE_IDS:
            raise ValueError('reviewed exercise set required')
        if base is not None and base.journal.owner_id != journal.owner_id:
            raise ValueError('combined journal owner mismatch')
        self.base = base

    @classmethod
    def from_private(cls, private: Path, owner_id: str, base=None):
        private = Path(private).resolve()
        binding = json.loads((private/'diana19-visible-binding.json').read_bytes())
        if (binding.get('schema') != 'diana19-visible-local-binding-v1'
                or binding.get('scope') != 'LOCAL_SINGLE_USER'
                or binding.get('owner_id') != owner_id
                or binding.get('runtime') != 'diana19-visible-private.json'
                or binding.get('journal') != 'diana19-visible-answers.sqlite3'):
            raise ValueError('visible exercise binding rejected')
        runtime_path = private/binding['runtime']
        if runtime_path.is_symlink() or runtime_path.stat().st_size > 100000:
            raise ValueError('invalid private exercise package')
        raw = runtime_path.read_bytes()
        if digest(raw) != binding['runtime_sha256']:
            raise ValueError('private exercise package changed')
        runtime = json.loads(raw)
        if runtime.get('schema') != 'diana19-visible-private-stages-v1':
            raise ValueError('invalid private exercise schema')
        return cls(runtime['entries'], DianaJournal(private/binding['journal'], owner_id,
                   binding['runtime_sha256']), base)

    def catalog(self):
        lessons = self.base.catalog()['lessons'] if self.base is not None else []
        lessons += [{'lesson_id': item['exercise_id'], 'title_ru': item['title_ru']}
                    for item in self.entries.values()]
        return {'status': 'STAGING', 'local_single_user': True,
                'canonical_promotion_allowed': False, 'lessons': lessons}

    @staticmethod
    def _key(exercise_id, stage_id):
        return exercise_id+'/'+stage_id

    @staticmethod
    def _sha(question):
        return digest(json.dumps(question, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':')).encode())

    def _records(self, exercise_id):
        records = []
        gap = False
        for stage in self.entries[exercise_id]['stages']:
            saved = self.journal.saved(self._key(exercise_id, stage['stage_id']))
            if saved is None:
                gap = True
            else:
                if gap or saved['question_sha256'] != self._sha(stage['question']):
                    raise ValueError('saved stage source or prerequisite changed')
                records.append({**saved, 'stage_id': stage['stage_id'],
                                'assistance': stage.get('assistance', 'INDEPENDENT_RESPONSE')})
        return records

    def view(self, exercise_id):
        if exercise_id not in self.entries:
            if self.base is not None:
                return self.base.view(exercise_id)
            raise KeyError('unknown lesson')
        entry = self.entries[exercise_id]
        records = self._records(exercise_id)
        complete = len(records) == len(entry['stages'])
        stage = entry['stages'][-1 if complete else len(records)]
        # A later prompt, answer key and explanation are never included here
        # until the corresponding prerequisite has been committed to SQLite.
        return {'status': 'STAGING', 'question': copy.deepcopy(stage['question']),
                'question_sha256': self._sha(stage['question']),
                'stage': {'stage_id': stage['stage_id'], 'complete': complete,
                          'assistance': stage.get('assistance', 'INDEPENDENT_RESPONSE')},
                'saved': records[-1] if records else None, 'saved_answers': records,
                'explanation': copy.deepcopy(entry['explanation']) if complete else None,
                'automatic_assessment': False, 'canonical_promotion_allowed': False}

    def submit(self, exercise_id, message):
        if exercise_id not in self.entries:
            if self.base is not None:
                return self.base.submit(exercise_id, message)
            raise KeyError('unknown lesson')
        if type(message) is not dict or set(message) != {
                'request_id', 'question_sha256', 'answer', 'stage_id'}:
            raise ValueError('invalid staged response')
        stages = self.entries[exercise_id]['stages']
        index = next((i for i, stage in enumerate(stages)
                      if stage['stage_id'] == message['stage_id']), None)
        if index is None:
            raise ValueError('unknown stage')
        records = self._records(exercise_id)
        if index > len(records):
            raise ValueError('first answer has not been saved')
        stage = stages[index]
        value = {key: message[key] for key in ('request_id', 'question_sha256', 'answer')}
        # save() commits with synchronous=FULL before view() may reveal a stage.
        # An identical retry of an earlier stage remains idempotent after reveal.
        self.journal.save(self._key(exercise_id, stage['stage_id']),
                          self._sha(stage['question']), value)
        return self.view(exercise_id)

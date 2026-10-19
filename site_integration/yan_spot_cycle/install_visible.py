"""Install reviewed source projections privately; never edit the author's files."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import zipfile

from bridge_school_api.diana19_staging import DianaJournal, digest

ARCHIVE_NAME = 'diana19-two-exercises-split-reviewed-20261016.zip'
ARCHIVE_SHA = '68de356c6fc48a95e2d03baa117704657c9083a12062b2e294a3773f4fb170b5'
LEARNER_SHA = '2f99e179f9d29b2ed478134015af297c1424d07ff7aa0c30de2a27d5438bd010'


def install_visible(private: Path, source: Path, owner_id: str):
    private, source = Path(private).resolve(), Path(source).resolve()
    if source.is_dir():
        source = source/ARCHIVE_NAME
    names = ('diana19-visible-binding.json', 'diana19-visible-private.json',
             'diana19-visible-answers.sqlite3')
    if not private.is_dir() or any((private/name).exists() for name in names):
        raise ValueError('existing private directory and new exercise binding required')
    if source.is_symlink() or source.stat().st_size != 15422795 or digest(source.read_bytes()) != ARCHIVE_SHA:
        raise ValueError('reviewed split archive changed')
    with zipfile.ZipFile(source) as archive:
        if len(archive.namelist()) != 23 or len(set(archive.namelist())) != 23 or archive.testzip() is not None:
            raise ValueError('reviewed split archive catalog/CRC changed')
        manifest = json.loads(archive.read('MANIFEST.json'))
        pins = {'source_archive_sha256': ARCHIVE_SHA}
        for item in manifest['files']:
            value = archive.read(item['path'])
            if len(value) != item['bytes'] or digest(value) != item['sha256']:
                raise ValueError('reviewed split manifest changed')
            pins[item['path']] = item['sha256']
        key = json.loads(archive.read('server-only/GRADING_KEY.json'))
        student_raw = archive.read('learner-only/EXERCISES_RU.md')
        guide = archive.read('server-only/GRADING_GUIDE_RU.md').decode('utf-8').replace('\r\n', '\n')
        source_binding = json.loads(archive.read('provenance/SOURCE_BINDING.json'))
        learner_archive = archive.read('diana19-two-exercises-learner-only-20261016.zip')
    if (key.get('schema') != 'diana19-private-grading-key-v1'
            or key.get('audience') != 'SERVER_ONLY'
            or key['learner_document_sha256'] != digest(student_raw)
            or key['learner_archive_sha256'] != LEARNER_SHA
            or digest(learner_archive) != LEARNER_SHA
            or key['teacher_review'].get('accepted_for_ui') is not True
            or key.get('automatic_mastery_from_points') is not False
            or key.get('canonical_promotion_allowed') is not False):
        raise ValueError('reviewed learner/teacher binding rejected')
    guided = key['hint_stages']['guided_practice']
    if (key['hint_stages']['initial_independent_stage'] != []
            or guided['requires'] != {'first_independent_plan_saved': True}
            or guided['counts_as_unassisted_assessment'] is not False):
        raise ValueError('reviewed stage contract changed')
    student_text = student_raw.decode('utf-8').replace('\r\n', '\n')
    learner_sections = re.split(r'^## ', student_text, flags=re.M)
    sections = re.split(r'^## ', guide, flags=re.M)
    if (len(learner_sections) != 3 or len(sections) != 5
            or not sections[1].startswith('1.') or not sections[2].startswith('2.')):
        raise ValueError('reviewed explanation sections changed')
    conditions = learner_sections[0].split('\n\n')[1:]
    conditions.append('Реальные разыгрывающий, очередь хода, события и объяснение преподавателя в видео — UNKNOWN.')
    entries = []
    for i, exercise in enumerate(key['proof_model_inputs']):
        eid = exercise['exercise_id']
        title = learner_sections[i+1].splitlines()[0].split('. ', 1)[1]
        questions = re.findall(r'^\d+\. (.+)$', learner_sections[i+1].split('\n', 1)[1], flags=re.M)
        if len(questions) != 3 or key['displayed_number_to_internal_id'][str(i+1)] != eid:
            raise ValueError('reviewed learner question mapping changed')
        question = {'lesson_id': eid, 'title_ru': title,
            'question_ru': '\n\n'.join(questions),
            'conditions_ru': conditions,
            'visible_holdings': {seat: [rank+exercise['suit'] for rank in ranks]
                                 for seat, ranks in exercise['hands'].items()},
            'known_play': [], 'options': []}
        stages = [{'stage_id': 'initial', 'assistance': 'INDEPENDENT_RESPONSE', 'question': question}]
        if i == 0:
            stages.append({'stage_id': 'followup', 'assistance': 'GUIDED_PRACTICE', 'question': {
                **question, 'question_ru': guided['prompt_ru']}})
        # Keep each accepted explanation separate. Never return the other
        # exercise's answer, a grading rubric, source evidence, or a full config.
        explanation = sections[i+1].split('\n\n', 1)[1]
        explanation = explanation.split('\n\nОценка,')[0].strip()
        entries.append({'exercise_id': eid, 'title_ru': title,
            'stages': stages, 'explanation': {
                'paragraphs': [explanation],
                'source': {'video_label': 'DIANA19', 'video_url': None,
                    'video_link_status': 'UNAVAILABLE_IN_ACCEPTED_PACKAGE',
                    'timecode_start': exercise['source_timecodes'][0],
                    'timecode_end': exercise['source_timecodes'][1],
                    'source_record_id': exercise['source_record_id'],
                    'video_sha256': source_binding['retained_video_sha256_not_rehashed'],
                    'teacher_quote_verified': False, 'source_video_reopened': False},
                'scope_notice': 'Редакторское упражнение по видимым картам одной масти. Это не цитата преподавателя и не восстановление полной раздачи.'}})
    runtime = {'schema': 'diana19-visible-private-stages-v1', 'entries': entries,
               'source_pins': pins, 'canonical_promotion_allowed': False}
    encoded = (json.dumps(runtime, ensure_ascii=False, indent=2)+'\n').encode()
    runtime_sha = digest(encoded)
    binding = {'schema': 'diana19-visible-local-binding-v1', 'scope': 'LOCAL_SINGLE_USER',
        'owner_id': owner_id, 'runtime': names[1], 'runtime_sha256': runtime_sha,
        'journal': names[2]}
    # Install only after all source/schema checks. There are no learner answers.
    with (private/names[1]).open('xb') as output:
        output.write(encoded)
    DianaJournal.create(private/names[2], owner_id, runtime_sha)
    with (private/names[0]).open('x', encoding='utf-8') as output:
        json.dump(binding, output, indent=2)
        output.write('\n')
    return {'status': 'STAGING', 'exercise_ids': [item['exercise_id'] for item in entries],
            'answer_records_created': 0, 'runtime_sha256': runtime_sha}


if __name__ == '__main__':
    from bridge_school_api.local_single_user_lesson import read_context
    parser = argparse.ArgumentParser()
    parser.add_argument('--private-dir', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    owner = read_context(args.private_dir)['owner']['provider_subject']
    print(json.dumps(install_visible(args.private_dir, args.source, owner)))

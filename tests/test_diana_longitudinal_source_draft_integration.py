"""Synthetic postprocessor/PDF/quality integration; all external I/O is mocked."""
from contextlib import ExitStack, closing, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import fitz

import diana_longitudinal_postprocess_v4_2 as post
from bridge_contracts.video_canon_replay import digest
from bridge_contracts.video_replay_input import ReplayInputError, sha256
from diana_longitudinal_quality_v4_2 import build_quality_layer
from tests.test_video_replay_input import full_fixture, raw
from tools.replay_video_canon import stage_local


def fixture(constrained=False):
    master, mapping, selection = full_fixture()
    master['job_id'] = 'a' * 32
    master['createdAt'] = '2026-01-01T00:00:00Z'
    if constrained:
        master['transcript'].append({'segment_id': 'synthetic-overlap', 'start': 9.5, 'end': 10.5,
                                     'text': 'Synthetic overlap.'})
        mapping['intervals'].append({'interval_ref': 'synthetic-overlap', 'start': 9.5, 'end': 10.5})
        master['screenshots'][0]['time'] = 20
    source = json.dumps(master, indent=3).encode() + b'\n'
    mapping['job_id'] = master['job_id']
    mapping['sourceMasterJsonSha256'] = digest(master)
    selection['source_raw_sha256'] = sha256(source)
    selection['speaker_map_raw_sha256'] = sha256(raw(mapping))
    files = {'speaker_map': raw(mapping)}
    if constrained:
        files['constraints'] = raw({'example_end': 20, 'overlap': {'status': 'UNRESOLVED'},
                                    'frame_boundary_conflicts': ['synthetic-boundary']})
        selection['constraints_raw_sha256'] = sha256(files['constraints'])
    files['selection'] = raw(selection)
    metadata = {'drive_id': 'synthetic-report', 'master_json_sha256': sha256(source)}
    return master, source, metadata, files


class SourceDraftPostprocessTests(unittest.TestCase):
    def test_loader_retains_original_embedded_bytes_and_legacy_signature(self):
        master, source, _, _ = fixture()
        with fitz.open() as doc:
            doc.new_page()
            doc.embfile_add('master_analysis.json', source)
            pdf = doc.tobytes()
        done = {'masterPdf': {'driveId': 'synthetic-report', 'sha256': sha256(pdf),
                              'masterJsonSha256': sha256(source)}}
        with patch.object(post.base.io, 'download', side_effect=lambda t, i, p: p.write_bytes(pdf)):
            loaded, meta, exact = post.base._load_master_with_raw('synthetic', done)
            legacy, legacy_meta = post.base._load_master('synthetic', done)
            bad_done = deepcopy(done)
            bad_done['masterPdf']['masterJsonSha256'] = '0' * 64
            with self.assertRaisesRegex(RuntimeError, 'MASTER_JSON_SHA_MISMATCH'):
                post.base._load_master_with_raw('synthetic', bad_done)
        self.assertEqual(exact, source)
        self.assertEqual((loaded, meta), (legacy, legacy_meta))
        self.assertEqual(loaded, master)
        self.assertNotEqual(sha256(source), digest(master))

    def test_local_configuration_requires_selection_and_readable_files(self):
        self.assertIsNone(post._source_draft_files({}))
        with self.assertRaisesRegex(ReplayInputError, 'SELECTION_REQUIRED'):
            post._source_draft_files({'BRIDGE_VIDEO_SOURCE_DRAFT_CONSTRAINTS_PATH': 'synthetic'})
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ReplayInputError, 'SELECTION_READ_FAILED'):
                post._source_draft_files({'BRIDGE_VIDEO_SOURCE_DRAFT_SELECTION_PATH': str(Path(temp) / 'absent')})

    def test_master_document_raw_bytes_and_parsed_content_must_match(self):
        master, source, metadata, files = fixture()
        cases = [
            (master, source, dict(metadata, drive_id='different'), 'DOCUMENT_MISMATCH'),
            (master, source + b' ', metadata, 'BYTES_MISMATCH'),
            (dict(master, job_id='different'), source, metadata, 'CONTENT_MISMATCH'),
        ]
        for m, b, meta, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(ReplayInputError, code):
                post._prepare_source_draft(m, meta, b, files)

    def test_bounded_conversion_is_not_allowed_in_postprocessor(self):
        master, source, metadata, files = fixture()
        selection = json.loads(files['selection'])
        selection['input_kind'] = 'bounded_packet'
        files['selection'] = raw(selection)
        with self.assertRaisesRegex(ReplayInputError, 'FULL_MASTER_SELECTION_REQUIRED'):
            post._prepare_source_draft(master, metadata, source, files)

    def test_sidecar_tampering_and_missing_pin_fail_before_quality(self):
        master, source, metadata, files = fixture(True)
        for field in ('constraints', 'speaker_map'):
            with self.subTest(field=field), self.assertRaisesRegex(ReplayInputError, 'DIGEST_MISMATCH'):
                post._prepare_source_draft(master, metadata, source, {**files, field: files[field] + b' '})
        with self.assertRaisesRegex(ReplayInputError, 'PIN_AND_FILE_REQUIRED'):
            post._prepare_source_draft(master, metadata, source, {k: v for k, v in files.items() if k != 'constraints'})

    def test_missing_optional_map_preserves_record_but_withholds_knowledge(self):
        master, source, metadata, files = fixture()
        selection = json.loads(files['selection'])
        selection.pop('speaker_map_raw_sha256')
        packet = post._prepare_source_draft(master, metadata, source, {'selection': raw(selection)})
        replay = build_quality_layer(master, source_draft_input=packet)['video_canon_auto_pipeline']
        draft = replay['draft_knowledge']
        self.assertEqual(replay['status'], 'BLOCKED')
        self.assertIsNone(draft['knowledge'])
        self.assertEqual(draft['source_integrity_status'], 'INVALID')
        self.assertTrue({'DIGEST_INVALID', 'PARENT_CANONICAL_DIGEST_MISMATCH',
                         'TEACHER_IDENTITY_UNPROVEN'}.issubset({g['code'] for g in replay['gaps']}))
        self.assertEqual(draft['transcript'], packet['selected_transcript_segment'])
        self.assertEqual(len(replay['candidates']), 1)
        self.assertEqual(replay['video_canon_inputs'], {})
        self.assertEqual(replay['promotion_commands'], [])

    def test_constraints_conflicts_and_original_inputs_survive_quality(self):
        master, source, metadata, files = fixture(True)
        before = deepcopy((master, metadata, files))
        packet = post._prepare_source_draft(master, metadata, source, files)
        quality = build_quality_layer(master, source_draft_input=packet)
        replay = quality['video_canon_auto_pipeline']
        self.assertEqual(replay['status'], 'BLOCKED')
        self.assertEqual(replay['video_canon_inputs'], {})
        self.assertEqual(replay['promotion_commands'], [])
        self.assertIsNone(replay['draft_knowledge']['knowledge'])
        self.assertEqual(set(packet['input_builder_conflicts']), {
            'EPISODE_SEGMENT_OMITTED', 'FRAME_OUTSIDE_HALF_OPEN_SPEECH_INTERVAL',
            'OVERLAPPING_MAP_INTERVAL', 'OVERLAPPING_TRANSCRIPT_INTERVAL'})
        self.assertEqual(replay['draft_knowledge']['source_constraints'], json.loads(files['constraints']))
        self.assertEqual((master, metadata, files), before)
        self.assertEqual(quality['counts']['video_canon_auto_promotions_ready'], 0)
        self.assertEqual(quality['counts']['video_canon_candidates'], 0)
        self.assertEqual(quality['authority']['canon_activation'], 'DENY')

    def test_opt_in_draft_cannot_fall_through_to_ready_canon_aliases(self):
        master, source, metadata, files = fixture()
        packet = post._prepare_source_draft(master, metadata, source, files)
        master.update(video_canon_learning_candidate={}, video_canon_assertions=[],
                      video_canon_verification_bundles={'status': 'PASS'})
        with patch('diana_longitudinal_quality_v4_2.run_video_canon_auto_pipeline', side_effect=AssertionError('promotion route')):
            replay = build_quality_layer(master, source_draft_input=packet)['video_canon_auto_pipeline']
        self.assertEqual(replay['status'], 'BLOCKED')
        self.assertFalse(replay['video_canon_inputs'])
        self.assertIn('TEACHER_IDENTITY_UNPROVEN', {g['code'] for g in replay['gaps']})
        self.assertFalse(replay['authoritative_write_performed'])
        for value in ({'schema': 'video-canon-auto-pipeline-v1'}, [], 'synthetic'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'SOURCE_DRAFT_INPUT_SCHEMA_INVALID'):
                build_quality_layer(master, source_draft_input=value)

    def test_selection_changes_fingerprint_repeat_does_not(self):
        master, source, metadata, files = fixture()
        first = post._prepare_source_draft(master, metadata, source, files)
        selection = json.loads(files['selection'])
        selection['frame_ids'] = []
        second = post._prepare_source_draft(master, metadata, source, {**files, 'selection': raw(selection)})
        a = build_quality_layer(master, source_draft_input=first)
        self.assertEqual(a, build_quality_layer(master, source_draft_input=first))
        b = build_quality_layer(master, source_draft_input=second)
        self.assertNotEqual(a['incremental_processing']['input_fingerprint'], b['incremental_processing']['input_fingerprint'])

    def test_default_quality_is_unchanged_and_analysis_request_is_explicit(self):
        master, _, _, _ = fixture()
        self.assertEqual(build_quality_layer(master), build_quality_layer(master, source_draft_input=None))
        self.assertEqual(build_quality_layer(master)['video_canon_auto_pipeline']['status'], 'NOT_REQUESTED')
        master['video_canon_analysis_requested'] = True
        self.assertEqual(build_quality_layer(master)['video_canon_auto_pipeline']['status'], 'BLOCKED')

    def test_main_file_inputs_to_quality_to_disposable_staging_repeat(self):
        master, source, metadata, files = fixture(True)
        artifacts, summaries, persisted = {}, [], []
        with tempfile.TemporaryDirectory() as temp, closing(sqlite3.connect(':memory:')) as connection, ExitStack() as stack:
            env = {'BRIDGE_JOB_ID': master['job_id'], 'BRIDGE_OUTPUT_FOLDER_ID': 'synthetic-results'}
            for key, content in files.items():
                path = Path(temp) / (key + '.json')
                path.write_bytes(content)
                env['BRIDGE_VIDEO_SOURCE_DRAFT_' + key.upper() + '_PATH'] = str(path)
            stack.enter_context(patch.dict(os.environ, env, clear=True))
            stack.enter_context(patch.object(post.base, 'user_oauth_token', return_value='synthetic'))
            stack.enter_context(patch.object(post.base, '_latest_done', return_value=({'id': 'synthetic-done'}, {})))
            stack.enter_context(patch.object(post.base, '_load_master_with_raw', return_value=(master, metadata, source)))
            stack.enter_context(patch.object(post.base, '_load_master', side_effect=AssertionError('raw loader required')))
            stack.enter_context(patch.object(post.base, '_drive_metadata', return_value={'size': '1', 'parents': ['synthetic-source']}))
            stack.enter_context(patch.object(post.base, '_lesson_identity', return_value={'lesson_id': 'synthetic-lesson', 'lesson_number': 1, 'lesson_date_status': 'CONFIRMED'}))
            stack.enter_context(patch.object(post, '_reconstruct_from_master_pdf', return_value={'deals': [], 'qc': {}}))
            stack.enter_context(patch.object(post, '_trusted_correction_receipt_resolver', return_value=None))
            stack.enter_context(patch.object(post, 'execute_digest_pinned_dds3', side_effect=AssertionError('DDS called')))
            for function, value in (('_curriculum', {}), ('_teacher_brief_markdown', ''), ('_cards_markdown', '')):
                stack.enter_context(patch.object(post.base, function, return_value=value))
            stack.enter_context(patch.object(post, '_summary_markdown', return_value=''))
            def persist(payload):
                quality = payload['quality_v2']
                replay = quality['video_canon_auto_pipeline']
                self.assertEqual(replay['status'], 'BLOCKED')
                self.assertEqual(replay['candidates'][0]['candidate_type'], 'video_source_knowledge_draft')
                self.assertIn(replay['candidates'][0], quality['candidate_staging_records'])
                counts = stage_local(connection, replay)
                persisted.append(counts)
                return {'status': 'PERSISTED', **counts, 'candidate_records': len(quality['candidate_staging_records']),
                        'input_fingerprint': quality['incremental_processing']['input_fingerprint']}
            stack.enter_context(patch.object(post.base, '_persist_staging_if_configured', side_effect=persist))
            def upload(token, folder, path, mime):
                data = path.read_bytes()
                if path.name in artifacts:
                    self.assertEqual(data, artifacts[path.name])
                else:
                    artifacts[path.name] = data
                return {'id': 'synthetic-artifact', 'sha256': sha256(data)}
            stack.enter_context(patch.object(post, '_upload_idempotent_verified', side_effect=upload))
            for _ in range(2):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(post.main(), 0)
                summaries.append(json.loads(output.getvalue()))
            self.assertEqual(summaries[0]['generation_key'], summaries[1]['generation_key'])
            self.assertEqual(len(artifacts), 6)
            self.assertEqual(persisted, [{'inserted': 1, 'existing': 0}, {'inserted': 0, 'existing': 1}])
            self.assertEqual(connection.execute('SELECT count(*) FROM replay_staging').fetchone()[0], 1)
            for key, content in files.items():
                self.assertEqual((Path(temp) / (key + '.json')).read_bytes(), content)

    def test_invalid_selection_stops_main_before_quality_or_persistence(self):
        master, source, metadata, files = fixture()
        selection = json.loads(files['selection'])
        selection['source_raw_sha256'] = '0' * 64
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            path = Path(temp) / 'selection.json'
            path.write_bytes(raw(selection))
            stack.enter_context(patch.dict(os.environ, {'BRIDGE_JOB_ID': master['job_id'],
                'BRIDGE_OUTPUT_FOLDER_ID': 'synthetic-results',
                'BRIDGE_VIDEO_SOURCE_DRAFT_SELECTION_PATH': str(path)}, clear=True))
            stack.enter_context(patch.object(post.base, 'user_oauth_token', return_value='synthetic'))
            stack.enter_context(patch.object(post.base, '_latest_done', return_value=({}, {})))
            stack.enter_context(patch.object(post.base, '_load_master_with_raw', return_value=(master, metadata, source)))
            quality = stack.enter_context(patch.object(post, 'build_quality_layer'))
            persist = stack.enter_context(patch.object(post.base, '_persist_staging_if_configured'))
            upload = stack.enter_context(patch.object(post, '_upload_idempotent_verified'))
            with self.assertRaisesRegex(ReplayInputError, 'SOURCE_DIGEST_MISMATCH'):
                post.main()
            quality.assert_not_called()
            persist.assert_not_called()
            upload.assert_not_called()


if __name__ == '__main__':
    unittest.main()

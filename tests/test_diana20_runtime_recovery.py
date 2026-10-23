"""Synthetic ABI regressions; real pinned replay is recorded separately."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import zipfile

import cv2
import numpy as np

from video_adapters.diana20.runtime_recovery import Diana20RuntimeProvider
from bridge_vision.runtime_recovery_evidence import canonical_bytes
from bridge_vision.original_deal_solver import RecoveryInputError
from tools.diana20_recovery_replay import parse, replay


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        pixels = np.full((24, 32, 3), (40, 100, 180), dtype=np.uint8)
        ok, encoded = cv2.imencode('.jpg', pixels)
        self.assertTrue(ok)
        self.raw = encoded.tobytes()
        pixels = cv2.imdecode(np.frombuffer(self.raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        self.source, self.frame_sha = 'a' * 64, sha256(self.raw).hexdigest()
        self.roi_sha = sha256(pixels[2:10, 3:12].tobytes()).hexdigest()
        self.row = {'sequence': 0, 'receipt': {'frame_id': 'existing-id', 'source_sha256': self.source,
            'frame_sha256': self.frame_sha, 'timestamp_ms': 1010, 'decode_receipt': {
                'source_sha256': self.source, 'frame_sha256': self.frame_sha, 'actual_pts_ms': 1010,
                'actual_pts_seconds': 1.01, 'requested_time_seconds': 1.0, 'timestamp_basis': 'DECODER_ACTUAL_PTS'}},
            'payload': {'source_video_sha256': self.source, 'frame_sha256': self.frame_sha, 'source_timestamp_ms': 1010,
                'frame_size': [32, 24], 'frame_pixel_sha256_bgr': sha256(pixels.tobytes()).hexdigest(),
                'recognizer_version': 'bridgit-gambler-partial-pixels-v3', 'canonical_allowed': False,
                'complete_deal': False, 'board_identity': None, 'attempt_identity': None,
                'cards': [{'card': 'KS', 'seat': 'S', 'confidence': .999, 'status': 'MACHINE_HAND_CANDIDATE',
                    'receipt': {'roi_xyxy': [3, 2, 12, 10], 'roi_bgr_sha256': self.roi_sha}}], 'unknown': []}}
        self.checkpoint = {'schema': 'bridge-video-transcript-stage-checkpoint-v1', 'status': 'TRANSCRIPT_STAGE_COMPLETE',
            'sourceSha256': self.source, 'segments': [{'segment_id': 'seg', 'ordinal': 1, 'start': 1.005, 'end': 1.015,
                'text': 'West has ace of spades', 'unreliable': False, 'speaker_role_candidate': 'teacher',
                'speaker_role_confidence': .99, 'speaker': 'SPEAKER_A', 'source': 'local_asr'}]}

    def provider(self, checkpoint=None):
        return Diana20RuntimeProvider(transcript_checkpoint=checkpoint or self.checkpoint,
            transcript_artifact_sha256='b' * 64)

    def test_bound_bytes_and_actual_pts_do_not_grant_semantic_authority(self):
        p = self.provider()
        record = p.observe_saved_row(self.row, frame_bytes=self.raw)
        self.assertEqual(record['frame_byte_verification'], 'LOCAL_BYTES_HASHED')
        self.assertEqual(record['candidate_observations'][0]['roi_checks'], ['MATCH'])
        self.assertEqual(len(record['teacher_evidence']['segments']), 1)
        self.assertEqual(len(record['teacher_evidence']['declarations']), 1)
        result = p.finish()
        self.assertEqual(result['status'], 'CLOSED_UNKNOWN')
        self.assertEqual(result['temporal_recovery']['scopes'][0]['reason'], 'RUNTIME_SEMANTIC_GATES_UNKNOWN')
        self.assertEqual(result['hard_fact_count'], 0)
        self.assertEqual(record['retained_original_row'], self.row)
        self.assertFalse(record['source_original_bytes_verified'])
        self.assertEqual(record['candidate_observations'][0]['components']['rank']['candidate_domain'], ['K'])
        self.assertEqual(len(record['candidate_observations'][0]['components']['rank']['domain']), 13)

    def test_mutations_conflict_without_inference(self):
        for field, value in [('source_video_sha256', 'b' * 64), ('frame_sha256', 'b' * 64),
                ('source_timestamp_ms', 1000), ('frame_pixel_sha256_bgr', 'b' * 64), ('frame_size', [32, 25])]:
            row = deepcopy(self.row); row['payload'][field] = value
            p = self.provider(); p.observe_saved_row(row, frame_bytes=self.raw)
            self.assertEqual(p.finish()['status'], 'CONFLICT', field)
        for seconds in (True, 1.0, 1010):
            row = deepcopy(self.row); row['receipt']['decode_receipt']['actual_pts_seconds'] = seconds
            p = self.provider(); p.observe_saved_row(row, frame_bytes=self.raw)
            self.assertEqual(p.finish()['status'], 'CONFLICT')
        row = deepcopy(self.row); row['payload']['cards'][0]['receipt']['roi_bgr_sha256'] = 'c' * 64
        p = self.provider(); p.observe_saved_row(row, frame_bytes=self.raw)
        self.assertEqual(p.finish()['status'], 'CONFLICT')

    def test_missing_bytes_and_transcript_stay_unknown(self):
        p = Diana20RuntimeProvider()
        record = p.observe_saved_row(self.row)
        self.assertEqual(record['frame_byte_verification'], 'UNKNOWN_FRAME_BYTES_UNAVAILABLE')
        self.assertEqual(record['teacher_evidence']['status'], 'UNKNOWN_TRANSCRIPT_UNAVAILABLE')
        self.assertEqual(p.finish()['status'], 'CLOSED_UNKNOWN')

    def test_repetition_no_votes_and_changed_same_event_conflict(self):
        p = self.provider(); p.observe_saved_row(self.row, frame_bytes=self.raw)
        self.assertTrue(p.observe_saved_row(self.row)['duplicate_delivery'])
        self.assertEqual(len(p.finish()['records']), 1)
        changed = deepcopy(self.row); changed['payload']['cards'][0]['card'] = 'AS'
        p.observe_saved_row(changed, frame_bytes=self.raw)
        self.assertEqual(p.finish()['status'], 'CONFLICT')
        self.assertEqual(p.finish()['independent_vote_count'], 0)

    def test_different_source_or_board_is_never_joined(self):
        p = self.provider()
        p.observe_saved_row(self.row, frame_bytes=self.raw)
        other = deepcopy(self.row)
        other['sequence'] = 1
        other['payload']['board_identity'] = {'claimed_board': 2}
        other['receipt']['source_sha256'] = other['payload']['source_video_sha256'] = 'c' * 64
        other['receipt']['decode_receipt']['source_sha256'] = 'c' * 64
        record = p.observe_saved_row(other, frame_bytes=self.raw)
        self.assertEqual(record['teacher_evidence']['status'], 'UNKNOWN_TRANSCRIPT_SOURCE_MISMATCH')
        self.assertEqual(len(p.finish()['temporal_recovery']['scopes']), 2)

    def test_repeated_row_bytes_are_checked_and_missing_bytes_can_be_supplied(self):
        p = self.provider(); p.observe_saved_row(self.row)
        p.observe_saved_row(self.row, frame_bytes=self.raw)
        self.assertEqual(len(p.finish()['records']), 1)
        self.assertEqual(p.finish()['records'][0]['frame_byte_verification'], 'LOCAL_BYTES_HASHED')
        p.observe_saved_row(self.row, frame_bytes=b'wrong')
        self.assertEqual(p.finish()['status'], 'CONFLICT')
        p.observe_saved_row(self.row, frame_bytes=self.raw)
        self.assertEqual(p.finish()['status'], 'CONFLICT')

    def test_unbounded_segment_time_and_geometry_claim_fail_closed(self):
        checkpoint = deepcopy(self.checkpoint)
        checkpoint['segments'][0].update(start=10**400, end=10**401)
        p = self.provider(checkpoint)
        self.assertEqual(p.observe_saved_row(self.row)['teacher_evidence']['invalid_segment_indices'], [0])
        row = deepcopy(self.row)
        base_sha = sha256(canonical_bytes(row['payload'])).hexdigest()
        row['payload']['recognizer_update'] = {'original_payload_sha256': base_sha,
            'effective_cards': deepcopy(row['payload']['cards']), 'effective_unknown': [], 'runtime': {'geometry_enabled': True}}
        p = self.provider(); p.observe_saved_row(row)
        self.assertEqual(p.finish()['status'], 'CONFLICT')

    def test_review_flags_or_conditional_seat_mapping_cannot_open_gate(self):
        row = deepcopy(self.row)
        row['payload'].update(authority='INDEPENDENT_REVIEW', human_verified=True, board_identity={'number': 4},
            seat_mapping={'current_frame_pixels_verified': True, 'accepted_as_observation': True,
                          'complete_mapping': {'top': 'N', 'right': 'E', 'bottom': 'S', 'left': 'W'}})
        p = self.provider(); record = p.observe_saved_row(row, frame_bytes=self.raw)
        self.assertTrue(all(v == 'UNKNOWN' for v in record['semantic_gates'].values()))
        self.assertFalse(p.finish()['semantic_admission_supported'])

    def test_frame_evidence_seam_preserves_full_payload(self):
        p = self.provider()
        record = p.observe_frame_evidence(SimpleNamespace(observation=self.row['payload']),
            receipt=self.row['receipt'], sequence=0, frame_bytes=self.raw)
        self.assertEqual(record['retained_original_row']['payload'], self.row['payload'])
        self.row['payload']['cards'].clear()
        self.assertEqual(len(p.finish()['records'][0]['candidate_observations']), 1)

    def test_hypothesis_and_endpoint_no_positive_binding(self):
        checkpoint = deepcopy(self.checkpoint)
        checkpoint['segments'][0]['text'] = 'If West had ace of spades'
        p = self.provider(checkpoint)
        record = p.observe_saved_row(self.row)
        self.assertEqual(record['teacher_evidence']['segments'][0]['modality'], 'HYPOTHETICAL')
        self.assertEqual(record['teacher_evidence']['declarations'], [])
        checkpoint['segments'][0]['end'] = 1.01
        p = self.provider(checkpoint)
        self.assertEqual(p.observe_saved_row(self.row)['teacher_evidence']['segments'], [])

    def test_malformed_inputs_reject(self):
        for value in (None, [], True):
            with self.assertRaises(RecoveryInputError):
                self.provider().observe_saved_row(value)
        for field, value in [('sequence', True), ('sequence', -1), ('payload', None)]:
            row = deepcopy(self.row); row[field] = value
            with self.assertRaises(RecoveryInputError): self.provider().observe_saved_row(row)
        for value in (True, [], 'receipt'):
            row = deepcopy(self.row); row['payload']['cards'][0]['receipt'] = value
            with self.assertRaises(RecoveryInputError): self.provider().observe_saved_row(row)
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}'):
            with self.assertRaises(RecoveryInputError): parse(raw)

    def test_file_replay_checks_pins_and_writes_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = root/'observations.jsonl'; rows.write_bytes(canonical_bytes(self.row)+b'\n')
            transcript = root/'transcript.json'; transcript.write_bytes(canonical_bytes(self.checkpoint))
            manifest = {'schema': 'diana20-card-evidence-export-v1', 'source': {'sha256': self.source},
                'frames': [{'evidence_id': 'existing-id', 'time': 1.0, 'sha256': self.frame_sha,
                            'export_sha256': self.frame_sha, 'export_path': 'frames/x.jpg'}]}
            metadata = root/'frames.json'; metadata.write_bytes(canonical_bytes(manifest))
            archive = root/'frames.zip'
            with zipfile.ZipFile(archive,'w') as z: z.writestr('frames/x.jpg',self.raw)
            kwargs = dict(observations=rows, observations_sha256=sha256(rows.read_bytes()).hexdigest(), output=root/'out',
                frame_archive=archive, frame_archive_sha256=sha256(archive.read_bytes()).hexdigest(),
                frame_manifest=metadata, frame_manifest_sha256=sha256(metadata.read_bytes()).hexdigest(),
                transcript=transcript, transcript_sha256=sha256(transcript.read_bytes()).hexdigest())
            report = replay(**kwargs)
            self.assertEqual(report['counts']['local_frame_bytes_verified'], 1)
            self.assertEqual(report['status'], 'CLOSED_UNKNOWN')
            self.assertEqual(parse((root/'out/runtime-evidence.jsonl').read_bytes())['retained_original_row'], self.row)
            with self.assertRaises(FileExistsError): replay(**kwargs)
            kwargs['observations_sha256'] = '0' * 64
            with self.assertRaisesRegex(RecoveryInputError, 'PIN_MISMATCH'): replay(**kwargs)

    def test_replay_malformed_receipt_is_stable_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root/'bad.jsonl'
            for key, value in [('frame_id', []), ('frame_id', {}), ('decode_receipt', ['bad']),
                               ('timestamp_ms', True), ('source_sha256', None)]:
                row = deepcopy(self.row); row['receipt'][key] = value
                path.write_bytes(canonical_bytes(row)+b'\n')
                with self.assertRaisesRegex(RecoveryInputError, 'ROW_BINDING_SHAPE'):
                    replay(observations=path, observations_sha256=sha256(path.read_bytes()).hexdigest(), output=root/'out')


if __name__ == '__main__':
    unittest.main()

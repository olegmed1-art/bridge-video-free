#!/usr/bin/env python3
from pathlib import Path
import hashlib, os, tempfile, time

import run_drive_3_1_free as base
from bridge_worker_3_1_free import ALGORITHM_REVISION, ALGORITHM_VERSION, bridge_term_hits, stable_job_id


def _video_candidates(t):
    # Accept any Drive file whose MIME type is video/*; ffmpeg handles the actual container.
    return base.search(t, "trashed=false and mimeType contains 'video/'")


def _safe_report_stem(name: str) -> str:
    stem = Path(name or 'video').stem.strip() or 'video'
    # Avoid path separators/control characters while preserving a readable Russian title.
    return ''.join('_' if ord(ch) < 32 or ch in '/\\' else ch for ch in stem)[:120]


def main(token_func):
    job = os.environ['BRIDGE_JOB_ID']
    t = token_func()
    candidates = _video_candidates(t)
    matches = [f for f in candidates if stable_job_id('drive', f['id']) == job]
    if len(matches) != 1:
        raise RuntimeError('BLOCKED_IDENTITY')

    srcm = matches[0]
    parent = (srcm.get('parents') or [None])[0]
    if not parent:
        raise RuntimeError('BLOCKED_IDENTITY')

    base.safe(job_id=job, stage='FETCHING', exit_code=0, size_bytes=int(srcm.get('size') or 0))

    with tempfile.TemporaryDirectory(prefix='bridge-') as td:
        work = Path(td)
        source_suffix = Path(srcm.get('name') or '').suffix.lower()
        if not source_suffix or len(source_suffix) > 12:
            source_suffix = '.video'
        video = work / ('source' + source_suffix)
        base.download(t, srcm['id'], video)
        dur = base.duration(video)
        ps = base.perms(t, srcm['id'])
        passport = {
            'driveId': srcm['id'],
            'name': srcm['name'],
            'mimeType': srcm.get('mimeType'),
            'sizeBytes': video.stat().st_size,
            'durationSeconds': dur,
            'sha256': base.sha(video),
            'parentFolderId': parent,
            'permissions': base.pmatrix(ps),
        }

        course, cid = base.course_text(t)
        blocks, qc, ok = base.transcribe(video, work, dur)
        if not ok:
            raise RuntimeError('ASR_QC_FAILED')

        critical = [b['start'] for b in blocks if bridge_term_hits(b['text'])]
        p1, p2 = base.visual(video, work, dur, critical)
        if not p2['gapCheckPass']:
            raise RuntimeError('VISUAL_GAP_CHECK_FAILED')

        eps = base.analyze(blocks, course)
        report = work / f"{_safe_report_stem(srcm['name'])} — анализ 3.1 FREE.pdf"
        base.pdf_report(report, passport, blocks, qc, eps, p1, p2)
        q = base.pdfqc(report)
        if not q['ok']:
            raise RuntimeError('PDF_QC_FAILED')

        up = base.upload_file(t, parent, report, 'application/pdf')
        have = {base.pkey(x) for x in base.perms(t, up['id']) if x.get('role') != 'owner'}
        for p in ps:
            if p.get('role') != 'owner' and base.pkey(p) not in have:
                base.add_perm(t, up['id'], p)

        access = base.pmatrix(base.perms(t, up['id'])) == base.pmatrix(ps)
        if not access:
            raise RuntimeError('PDF_ACCESS_MISMATCH')

        chk = work / 'recheck.bin'
        base.download(t, srcm['id'], chk)
        recheck = base.sha(chk) == passport['sha256'] and chk.stat().st_size == passport['sizeBytes']
        if not recheck:
            raise RuntimeError('ORIGINAL_REVERIFY_FAILED')

        done = {
            'schema': 'bridge-video-ai-done',
            'algorithmVersion': ALGORITHM_VERSION,
            'algorithmRevision': ALGORITHM_REVISION,
            'status': 'AI_DONE',
            'job_id': job,
            'original': passport,
            'pdf': {
                'driveId': up['id'],
                'name': up['name'],
                'sizeBytes': int(up.get('size') or 0),
                'pages': q['pages'],
                'sha256': q['sha256'],
                'access_match': access,
            },
            'speech': {
                'blockCount': len(blocks),
                'qcCount': len(qc),
                'unreliableCount': sum(b['unreliable'] for b in blocks),
            },
            'visual': {
                'pass1': p1['status'],
                'pass2': p2['status'],
                'gapCheckPass': p2['gapCheckPass'],
            },
            'methodologySource': {
                'driveId': cid,
                'sha256': hashlib.sha256(course.encode()).hexdigest(),
            },
            'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        }
        base.upload_json(t, parent, f'AI_DONE_{job}.json', done)
        done_sha = q['sha256']

    base.upload_json(t, parent, f'CLEANUP_ACK_{job}.json', {
        'status': 'CLEANUP_ACK',
        'job_id': job,
        'reportSha256': done_sha,
        'temporaryRunnerDataDeleted': True,
        'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    })
    base.safe(job_id=job, stage='CLEANUP_ACK', exit_code=0)

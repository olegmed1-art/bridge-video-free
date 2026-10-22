"""Exact selected sealed-artifact reader for private staging. No name search."""
import copy

from .receipt_encoding import canonical_bytes, strict_loads, sha256
from .terminal_finish_contract import Assignment, require

PRIVATE_LIMIT = 4 * 1024 * 1024
MASTER_LIMIT = 32 * 1024 * 1024


def extract_embedded_master(pdf_bytes):
    """Production extractor; dependency is imported only during explicit IO."""
    import fitz
    with fitz.open(stream=pdf_bytes,filetype='pdf') as document:
        require('master_analysis.json' in document.embfile_names(),'PRIVATE_MASTER_NOT_EMBEDDED')
        raw=document.embfile_get('master_analysis.json')
    require(type(raw) is bytes and 2<=len(raw)<=MASTER_LIMIT,'PRIVATE_MASTER_SIZE')
    return raw


def read_selected_sealed_bundle(claim,selected,token,*,assignment,
                                metadata_reader=None,downloader=None,
                                master_extractor=extract_embedded_master):
    """Use real full terminal verification, then stage the exact selected bytes.

    Optional ports are trusted launcher/test IO adapters, never authority. The
    mandatory original owner effect and DB lease/approval gates stay in force.
    """
    from . import terminal_evidence_v2 as terminal
    from .owner_effects import terminal_effect,assert_terminal_authorized
    require(type(assignment) is Assignment,'PRIVATE_ASSIGNMENT_UNBOUND')
    assignment.check_claim(claim)
    selected_input=selected
    selected_raw=canonical_bytes(dict(selected))
    selected=copy.deepcopy(dict(selected))
    require(selected.get('result_mode')=='SHADOW_REVIEW_ONLY'
            and selected.get('database_persistence_allowed') is False
            and 'private_staging' not in selected and 'saved_frame_observations' in selected
            and 'completed_report_baseline' in selected,'PRIVATE_SEALED_SELECTION_REQUIRED')
    metadata_reader=terminal.file_metadata if metadata_reader is None else metadata_reader
    downloader=terminal.download_file if downloader is None else downloader
    ports=dict(metadata_reader=metadata_reader,downloader=downloader)
    with terminal_effect(claim,required=True):
        # Existing verifier validates observations, baseline, PDF and exact
        # Drive revisions. Keep the private bound on EVERY AI_DONE download.
        terminal.reverify_terminal_output_live(claim,selected,token,
            max_ai_done_bytes=PRIVATE_LIMIT,**ports)
        assert_terminal_authorized(claim,required=True)
        ai,done_raw=terminal._read_live(selected['ai_done_drive_id'],token=token,
            expected_parent=claim['output_folder_id'],expected_mime='application/json',
            suffix='.json',max_bytes=PRIVATE_LIMIT,**ports)
        pdf,pdf_raw=terminal._read_live(selected['master_pdf_drive_id'],token=token,
            expected_parent=claim['output_folder_id'],expected_mime='application/pdf',
            suffix='.pdf',max_bytes=512*1024*1024,**ports)
        artifacts={a['kind']:a for a in selected['artifact_manifest']['artifacts']}
        for kind,meta in (('ai_done',ai),('master_pdf',pdf)):
            require(artifacts[kind]==dict(kind=kind,locator='gdrive:file:'+meta['drive_id'],**meta),
                    'PRIVATE_ARTIFACT_REVISION_CHANGED')
        require(ai['name']==terminal.sealed_ai_done_name(claim)
                and ai['sha256']==selected['ai_done_sha256']
                and pdf['sha256']==selected['master_pdf_sha256'],'PRIVATE_EXACT_ARTIFACT_CHANGED')
        done=strict_loads(done_raw,max_bytes=PRIVATE_LIMIT)
        require(type(done) is dict and 'savedFrameObservations' in done
                and 'completedReportBaseline' in done,'PRIVATE_SEALED_DONE_REQUIRED')
        master_raw=master_extractor(pdf_raw)
        require(type(master_raw) is bytes and 2<=len(master_raw)<=MASTER_LIMIT,'PRIVATE_MASTER_SIZE')
        require(sha256(master_raw)==done['masterPdf']['masterJsonSha256'],'PRIVATE_MASTER_HASH')
        master=strict_loads(master_raw,max_bytes=MASTER_LIMIT)
        require(type(master) is dict and master.get('job_id')==claim['stable_job_key']
                and master.get('algorithmRevision')==claim['algorithm_revision']
                and master.get('source',{}).get('sha256')==assignment.value['source_sha256']
                and done.get('original',{}).get('sha256')==assignment.value['source_sha256']
                and selected['saved_frame_observations'].get('source_sha256')==assignment.value['source_sha256'],
                'PRIVATE_MASTER_SOURCE_GENERATION')
        assert_terminal_authorized(claim,required=True)
        assignment.check_claim(claim)
        require(canonical_bytes(dict(selected_input))==selected_raw
                and canonical_bytes(selected)==selected_raw,'PRIVATE_SELECTION_CHANGED')
    return master_raw.decode('utf-8'),done_raw.decode('utf-8'),ai['drive_id']


def stage_selected_drive_result_private(open_connection,*,claim,selected,token,
                                        assignment,admission,metadata_reader=None,
                                        downloader=None,master_extractor=extract_embedded_master):
    from database.video_one_job_private_result import _scope,stage_and_read_private_result
    from .owner_effects import assert_terminal_authorized
    scope=_scope(admission)
    require(type(assignment) is Assignment,'PRIVATE_ASSIGNMENT_UNBOUND')
    assignment.check_claim(claim,scope[3])
    a=assignment.value;v=a['video']
    require(scope==(a['admission_id'],v['job_id'],v['lease_token'],v['worker_key'],
                    v['attempt_count'],a['source_fingerprint']),'PRIVATE_ADMISSION_ASSIGNMENT')
    from database.video_post_finish_receipt import assert_enrolled_assignment_on_connection
    assert_terminal_authorized(claim,required=True)
    with open_connection() as preflight:
        with preflight.transaction():
            assert_enrolled_assignment_on_connection(preflight,assignment)
            assert_terminal_authorized(claim,required=True)
    master,done,drive_id=read_selected_sealed_bundle(claim,selected,token,
        assignment=assignment,metadata_reader=metadata_reader,downloader=downloader,
        master_extractor=master_extractor)
    assert_terminal_authorized(claim,required=True)
    return stage_and_read_private_result(open_connection,admission=admission,
        master_json_text=master,ai_done_json_text=done,ai_done_drive_id=drive_id,assignment=assignment)

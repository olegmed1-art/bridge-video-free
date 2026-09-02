from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

import pytest

from universal_video import neon_worker
from universal_video.result_contract import ResultContractError, synthetic_result_contract_self_test, verify_drive_result_contract


def fixture():
    payload=b"%PDF-1.7\nissue-881\n"; digest=hashlib.sha256(payload).hexdigest()
    meta={"id":"result-file-123456","name":"result.pdf","mimeType":"application/pdf","size":str(len(payload)),"parents":["output-folder-123456"],"md5Checksum":hashlib.md5(payload,usedforsecurity=False).hexdigest()}
    claim={"stable_job_key":"1"*32,"source_file_id":"source-file-123456","output_folder_id":"output-folder-123456"}
    done={"masterPdf":{"driveId":meta["id"],"sha256":digest}}
    def metadata(_id:str,_token:str)->Mapping[str,Any]: return dict(meta)
    def download(_id:str,dest:Path,_token:str,**_:Any)->Mapping[str,Any]:
        dest.write_bytes(payload); result=dict(meta); result["_download_sha256"]=digest; result["_download_md5"]=meta["md5Checksum"]; return result
    return digest,meta,claim,done,metadata,download


def test_synthetic_contract_is_readback_bound():
    result=synthetic_result_contract_self_test(); receipt=result["terminal_receipt"]
    assert receipt["status"]=="PASS" and receipt["drive_readback_verified"] is True
    assert receipt["publication_state"]=="NOT_PUBLISHED" and receipt["canonical_promotion_allowed"] is False
    assert result["artifact_manifest_sha256"]==receipt["artifact_manifest_sha256"]


def test_real_contract_shape_passes_only_after_download():
    digest,_,claim,done,metadata,download=fixture()
    result=verify_drive_result_contract(claim,done,token="test",metadata_reader=metadata,downloader=download)
    assert result["artifact_manifest"]["artifacts"][0]["sha256"]==digest
    assert result["terminal_receipt"]["status"]=="PASS"


@pytest.mark.parametrize("mode,code",[("unreadable","UV_RESULT_DRIVE_READBACK_FAILED"),("checksum","UV_RESULT_CHECKSUM_MISMATCH"),("parent","UV_RESULT_METADATA_MISMATCH"),("changed","UV_RESULT_METADATA_CHANGED_DURING_READBACK")])
def test_result_contract_fail_closed(mode,code):
    _,meta,claim,done,metadata,download=fixture(); calls=0
    def bad_meta(file_id:str,token:str):
        nonlocal calls; calls+=1; result=dict(metadata(file_id,token))
        if mode=="parent": result["parents"]=["wrong-folder"]
        if mode=="changed" and calls>1: result["name"]="changed.pdf"
        return result
    def bad_download(file_id:str,dest:Path,token:str,**kwargs:Any):
        if mode=="unreadable": raise OSError("simulated")
        result=dict(download(file_id,dest,token,**kwargs))
        if mode=="checksum": result["_download_sha256"]="f"*64
        return result
    with pytest.raises(ResultContractError) as exc:
        verify_drive_result_contract(claim,done,token="test",metadata_reader=bad_meta,downloader=bad_download)
    assert exc.value.error_code==code


def claim():
    return {"job_id":"job-123","batch_id":"batch-123","lease_token":"lease-123","sequence":1,"source_folder_id":"source-folder-123","output_folder_id":"output-folder-123","work_folder_id":"work-folder-123","processing_profile":neon_worker.APPROVED_PROFILE,"algorithm_revision":neon_worker.APPROVED_REVISION,"source_file_id":"source-file-123","source_name":"source.mp4","source_mime_type":"video/mp4","source_size_bytes":12345678,"source_checksum":"md5:"+"a"*32,"stable_job_key":"1"*32,"is_canary":True,"attempt_count":1}


@pytest.mark.parametrize("failure",[ResultContractError("UV_RESULT_DRIVE_READBACK_FAILED"),neon_worker.NeonVideoTimeoutError("VIDEO_QUEUE_PROCESSING_TIMEOUT")])
def test_timeout_or_readback_failure_retries_without_finish(monkeypatch,failure):
    events=[]
    monkeypatch.setattr(neon_worker,"retry_job",lambda *_a,**k:(events.append("retry:"+k["error_code"]) or {"job_status":"QUEUED"}))
    monkeypatch.setattr(neon_worker,"finish_job",lambda *_a,**_k:(events.append("finish") or {"job_status":"REVIEW_READY"}))
    result=neon_worker.process_claim("postgres://unused",claim(),"worker-1",processor=lambda _c:(_ for _ in ()).throw(failure))
    assert result["job_status"]=="QUEUED" and events[0].startswith("retry:UV_") and "finish" not in events


def test_interrupted_worker_never_finishes(monkeypatch):
    events=[]; monkeypatch.setattr(neon_worker,"retry_job",lambda *_a,**_k:events.append("retry")); monkeypatch.setattr(neon_worker,"finish_job",lambda *_a,**_k:events.append("finish"))
    with pytest.raises(KeyboardInterrupt): neon_worker.process_claim("postgres://unused",claim(),"worker-1",processor=lambda _c:(_ for _ in ()).throw(KeyboardInterrupt()))
    assert events==[]

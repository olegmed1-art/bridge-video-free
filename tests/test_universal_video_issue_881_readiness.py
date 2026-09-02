from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from universal_video import neon_worker
from universal_video.result_contract import ResultContractError, synthetic_result_contract_self_test, verify_drive_result_contract
from universal_video.workload_lock import LOCK_FILE_NAME, shared_workload_lock


ROOT = Path(__file__).resolve().parents[1]


def fixture():
    payload=b"%PDF-1.7\nissue-881\n"; digest=hashlib.sha256(payload).hexdigest()
    meta={"id":"result-file-123456","name":"résultat-δ.pdf","mimeType":"application/pdf","size":str(len(payload)),"parents":["output-folder-123456"],"md5Checksum":hashlib.md5(payload,usedforsecurity=False).hexdigest()}
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
    canonical=json.dumps(result["artifact_manifest"],sort_keys=True,separators=(",",":"),ensure_ascii=False)
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest()==result["artifact_manifest_sha256"]


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


def test_attestation_exclusive_lock_blocks_worker_claim_path(tmp_path: Path):
    spool=tmp_path/"spool"; spool.mkdir(); lock_path=spool/LOCK_FILE_NAME; lock_path.touch(mode=0o640)
    with lock_path.open("r",encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            with shared_workload_lock(spool,blocking=False):
                pass
    with shared_workload_lock(spool,blocking=False):
        pass


def test_precanary_fences_services_claims_and_uses_captured_image_id():
    script=(ROOT/"ops/oracle_universal_video_precanary_attest.sh").read_text(encoding="utf-8")
    run_image=script[script.index("run_image(){"):script.index("verify_image_identity\nprintf",script.index("run_image(){"))]
    assert 'mask_service_for_window "$SOURCE_SERVICE"' in script
    assert 'mask_service_for_window "$CONTAINER_SERVICE"' in script
    assert 'quiesce_residents' in script
    assert 'quiesce_service "$SOURCE_SERVICE" "$source_state"' in script
    assert 'quiesce_service "$CONTAINER_SERVICE" "$container_state"' in script
    assert "active but masked and cannot be restored safely" in script
    assert "both Universal Video residents are active; refusing ambiguous restore" in script
    assert 'stopped_services+=("$service")' in script
    assert 'systemctl start "$service"' in script
    assert 'systemctl is-active --quiet "$service"' in script
    assert "flock --exclusive --nonblock 9" in script
    assert '"$image_id" "$@"' in run_image and '"$image" "$@"' not in run_image
    assert 'org.opencontainers.image.revision' in script
    assert script.index("flock --exclusive --nonblock 9") < script.index("run_image python")

    source_worker=(ROOT/"universal_video/spool_worker.py").read_text(encoding="utf-8")
    neon_worker_source=(ROOT/"universal_video/neon_worker.py").read_text(encoding="utf-8")
    assert "with shared_workload_lock(spool_root):" in source_worker
    assert "with shared_workload_lock():" in neon_worker_source


def test_installer_readiness_and_service_pin_use_captured_image_id():
    installer=(ROOT/"ops/oracle_universal_video_container_install.sh").read_text(encoding="utf-8")
    captured=installer.index('image_id="$(docker image inspect')
    readiness=installer.index("Run container-only readiness gate")
    readiness_run=installer.index("docker run --rm",readiness)
    install_unit=installer.index('install -m 0644',readiness_run)
    readiness_block=installer[readiness:install_unit]
    assert 'UNIVERSAL_VIDEO_IMAGE=$image_id' in installer[captured:readiness]
    assert 'verify_image_identity' in readiness_block
    assert 'org.opencontainers.image.revision' in installer[captured:readiness]
    assert '"$image_id" true' in readiness_block
    assert '"$image" true' not in readiness_block


def test_external_precanary_is_manual_and_compares_install_digest():
    workflow=(ROOT/".github/workflows/issue-881-precanary-evidence.yml").read_text(encoding="utf-8")
    assert workflow.count("if: github.event_name == 'workflow_dispatch'") == 4
    assert 'attested_digest="$(sed' in workflow
    assert '"$attested_digest" == "$installed_digest"' in workflow

from __future__ import annotations
import json, os, subprocess
from .service import DDS3Config, DDS_UPSTREAM

def engine_readiness(config:DDS3Config|None=None)->dict:
    cfg=config or DDS3Config()
    path=cfg.executable
    if not os.path.isfile(path) or not os.access(path,os.X_OK):
        return {"status":"unavailable","engine":"DDS3","engine_version":DDS_UPSTREAM,"executable":path,"reason":"DDS3_EXECUTABLE_MISSING","fallback_used":False}
    deal='N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3'
    try:
        p=subprocess.run([path,'N','None',deal],capture_output=True,text=True,timeout=min(cfg.timeout_seconds,5),check=False)
        if p.returncode!=0:return {"status":"unavailable","engine":"DDS3","engine_version":DDS_UPSTREAM,"reason":"DDS3_SELFTEST_FAILED","fallback_used":False}
        d=json.loads(p.stdout)
        if d.get('par_score_ns')!=-110:return {"status":"unavailable","engine":"DDS3","engine_version":DDS_UPSTREAM,"reason":"DDS3_SELFTEST_MISMATCH","fallback_used":False}
    except Exception:
        return {"status":"unavailable","engine":"DDS3","engine_version":DDS_UPSTREAM,"reason":"DDS3_SELFTEST_ERROR","fallback_used":False}
    return {"status":"ready","engine":"DDS3","engine_version":DDS_UPSTREAM,"executable":path,"fallback_used":False}

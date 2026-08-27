"""Validation boundary for human-labelled Bridgit glyph gold manifests."""
from __future__ import annotations
import hashlib,json
from typing import Any,Mapping,Sequence
GOLD_MANIFEST_VERSION="bridgit-glyph-gold-v1";_ALLOWED_KINDS={"rank","suit"}
def canonical_gold_manifest(entries:Sequence[Mapping[str,Any]])->dict[str,Any]:
    if not isinstance(entries,Sequence) or isinstance(entries,(str,bytes)) or not entries:raise ValueError("gold entries must be a non-empty array")
    normalized=[];seen=set()
    for raw in entries:
        if not isinstance(raw,Mapping):raise ValueError("gold entry must be an object")
        frame_sha=str(raw.get("frame_sha256") or "").lower()
        if len(frame_sha)!=64 or any(c not in "0123456789abcdef" for c in frame_sha):raise ValueError("frame_sha256 must be a sha256 hex digest")
        kind=str(raw.get("kind") or "").lower()
        if kind not in _ALLOWED_KINDS:raise ValueError("gold kind must be rank or suit")
        label=str(raw.get("label") or "").strip().upper()
        if not label:raise ValueError("gold label must be explicit")
        try:x,y,w,h=(int(raw[k]) for k in ("x","y","w","h"))
        except (KeyError,TypeError,ValueError) as exc:raise ValueError("gold crop geometry is invalid") from exc
        if min(x,y)<0 or min(w,h)<=0:raise ValueError("gold crop geometry is invalid")
        key=(frame_sha,kind,x,y,w,h)
        if key in seen:raise ValueError("duplicate gold crop")
        seen.add(key);normalized.append({"frame_sha256":frame_sha,"kind":kind,"label":label,"x":x,"y":y,"w":w,"h":h})
    normalized.sort(key=lambda e:(e["frame_sha256"],e["kind"],e["y"],e["x"],e["label"]))
    payload={"version":GOLD_MANIFEST_VERSION,"entries":normalized};canonical=json.dumps(payload,sort_keys=True,separators=(",",":")).encode();payload["manifest_sha256"]=hashlib.sha256(canonical).hexdigest();return payload
__all__=["GOLD_MANIFEST_VERSION","canonical_gold_manifest"]

import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from faster_whisper import WhisperModel

SCOPES = ["https://www.googleapis.com/auth/drive"]


def run(cmd):
    subprocess.run(cmd, check=True)


def ffprobe_duration(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ], text=True).strip()
    return float(out)


def download_drive_file(service, file_id, dest):
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(dest, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def ensure_folder(service, parent_id, name):
    escaped = name.replace("'", "\\'")
    q = (
        f"'{parent_id}' in parents and trashed=false and "
        f"mimeType='application/vnd.google-apps.folder' and name='{escaped}'"
    )
    res = service.files().list(q=q, spaces="drive", fields="files(id,name)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    if res.get("files"):
        return res["files"][0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
    return service.files().create(body=meta, fields="id", supportsAllDrives=True).execute()["id"]


def upload(service, parent_id, path, mime="application/octet-stream"):
    media = MediaFileUpload(str(path), mimetype=mime, resumable=True, chunksize=8 * 1024 * 1024)
    meta = {"name": path.name, "parents": [parent_id]}
    return service.files().create(body=meta, media_body=media, fields="id,name,size", supportsAllDrives=True).execute()


def srt_time(seconds):
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def main():
    file_id = os.environ["DRIVE_FILE_ID"]
    secret_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    model_name = os.environ.get("WHISPER_MODEL", "small")

    creds_info = json.loads(secret_json)
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    meta = drive.files().get(
        fileId=file_id,
        fields="id,name,mimeType,size,modifiedTime,parents",
        supportsAllDrives=True,
    ).execute()
    parents = meta.get("parents", [])
    if not parents:
        raise RuntimeError("У исходного файла не найден родительский каталог Drive")
    output_parent = os.environ.get("DRIVE_OUTPUT_FOLDER_ID") or parents[0]

    work = pathlib.Path("work")
    work.mkdir(exist_ok=True)
    source = work / meta["name"]
    download_drive_file(drive, file_id, source)
    duration = ffprobe_duration(source)

    audio = work / "audio.wav"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(audio)])

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(audio), language="ru", vad_filter=True, beam_size=5)

    txt = work / "transcript.txt"
    srt = work / "transcript.srt"
    rows = []
    with txt.open("w", encoding="utf-8") as tf, srt.open("w", encoding="utf-8") as sf:
        for idx, seg in enumerate(segments, start=1):
            text = seg.text.strip()
            rows.append({"start": seg.start, "end": seg.end, "text": text})
            tf.write(f"[{srt_time(seg.start).replace(',', '.')[:-4]}] {text}\n")
            sf.write(f"{idx}\n{srt_time(seg.start)} --> {srt_time(seg.end)}\n{text}\n\n")

    frames_dir = work / "frames"
    frames_dir.mkdir(exist_ok=True)
    t = 0.0
    while t < duration:
        stamp = int(t)
        out = frames_dir / f"frame_{stamp:06d}s.jpg"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(source), "-frames:v", "1", "-q:v", "3", str(out)])
        t += 600.0

    manifest = {
        "schemaVersion": "1.0",
        "algorithm": "bridge-video-free-3.1-stage1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "original": {
            "drive_id": meta["id"],
            "name": meta["name"],
            "mime_type": meta.get("mimeType"),
            "size_bytes": int(meta.get("size", 0)),
            "modified_time": meta.get("modifiedTime"),
            "parent_ids": parents,
            "duration_seconds": duration,
        },
        "transcription": {
            "language": getattr(info, "language", "ru"),
            "model": model_name,
            "segments": len(rows),
        },
        "control_frames": len(list(frames_dir.glob("*.jpg"))),
    }
    manifest_path = work / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    folder = ensure_folder(drive, output_parent, f"AI PREPARING - {meta['name']}")
    uploaded = []
    uploaded.append(upload(drive, folder, txt, "text/plain"))
    uploaded.append(upload(drive, folder, srt, "application/x-subrip"))
    uploaded.append(upload(drive, folder, manifest_path, "application/json"))
    for frame in sorted(frames_dir.glob("*.jpg")):
        uploaded.append(upload(drive, folder, frame, "image/jpeg"))

    print(json.dumps({"status": "OK", "output_folder_id": folder, "uploaded": uploaded}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise

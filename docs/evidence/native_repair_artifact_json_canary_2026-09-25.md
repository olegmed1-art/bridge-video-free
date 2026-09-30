# Native REPAIR pilot target: inline artifact manifest JSON

Date: 2026-09-25
Base main: `4b0fd7398b185957bf9d20048183cffbeb00c1d3`
Status: draft target only; no Autopilot task or permit has been issued.

## Reproduction

The CLI explicitly accepts `--metadata-json` as a JSON file **or inline JSON object**. On the base revision, `tools/artifact_manifest_v1.py::_load_json_arg` calls `Path(value).is_file()` before parsing inline JSON. A valid inline object whose argument has a component longer than the filesystem filename limit raises `OSError: [Errno 36] File name too long` instead of returning the object.

Read-only reproduction from the repository root:

```bash
python -c 'from tools.artifact_manifest_v1 import _load_json_arg; import json; print(_load_json_arg(json.dumps({"note":"x"*300}),{}))'
```

The observed traceback points to `Path(value).is_file()`. The same code and behavior were checked locally; the main branch file at the pinned base still contains that call.

## Exact repair contract for a separate reviewed task

- Work only in this repository and on this PR's exact verified head.
- Allowed changed files: `tools/artifact_manifest_v1.py` and `tests/test_artifact_manifest_pptx_qa.py`.
- Accept inline JSON longer than a filename component without probing that string as a filesystem path.
- Preserve the existing JSON-file argument behavior, default for empty input, and invalid JSON errors.
- Add a focused regression for both long inline JSON and a real JSON file path. Run that focused test.
- Do not alter this contract file, unrelated source, student data, Canon, migrations, credentials, infrastructure or production.
- A generated patch is evidence only. Publication to this PR needs separate validation and review; merge/deploy are outside scope.

The current native SQL permit is READ_ONLY and cannot authorize REPAIR. This target does not activate the worker, change the queue, reserve a slot or submit a provider task.

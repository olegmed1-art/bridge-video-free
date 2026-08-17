from __future__ import annotations

"""Fail-closed command facade for the DDS stage runner.

The historical implementation lives in :mod:`run_stage_impl`.  Keeping this
small facade means a direct ``python run_stage.py evaluate`` invocation is
blocked before a database is opened or any run metadata is written unless the
explicit data-bound authorization context has already been established by
``authorized_run_stage.py``.
"""

import uuid

import run_stage_impl as _impl
from run_authorization import validate_engine_environment

# Public compatibility surface used by preparation code and tests.
load_jsonl = _impl.load_jsonl
_task_path = _impl._task_path
task_in_stage = _impl.task_in_stage
cmd_prepare = _impl.cmd_prepare
cmd_reinterpret = _impl.cmd_reinterpret
cmd_followups = _impl.cmd_followups
cmd_plan = _impl.cmd_plan
cmd_audit = _impl.cmd_audit
cmd_correct = _impl.cmd_correct
cmd_report = _impl.cmd_report


def cmd_evaluate(args) -> None:
    validate_engine_environment()
    _impl.cmd_evaluate(args)


def parser():
    return _impl.parser()


def __getattr__(name: str):
    return getattr(_impl, name)


def main() -> None:
    args = parser().parse_args()
    if getattr(args, "run_id", None) is None:
        args.run_id = uuid.uuid4().hex[:12]
    if getattr(args, "command", None) == "evaluate":
        # This is deliberately before args.func(args), and therefore before the
        # implementation opens SQLite, records a run, locks predictions or
        # calls DDS.
        validate_engine_environment()
    args.func(args)


if __name__ == "__main__":
    main()

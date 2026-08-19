import os

import bridge_ai_compute_worker as worker


def test_missing_credentials_fail_closed():
    old_base = os.environ.pop("BRIDGE_API_BASE_URL", None)
    old_token = os.environ.pop("BRIDGE_API_TOKEN", None)
    try:
        try:
            worker.load_config()
        except RuntimeError:
            pass
        else:
            raise AssertionError("worker accepted missing credentials")
    finally:
        if old_base is not None:
            os.environ["BRIDGE_API_BASE_URL"] = old_base
        if old_token is not None:
            os.environ["BRIDGE_API_TOKEN"] = old_token


def test_no_engine_fails_closed():
    config = worker.Config("https://example.invalid", "token", None, None, 5.0)
    try:
        worker.choose_engine(config, {"position": {}})
    except RuntimeError:
        pass
    else:
        raise AssertionError("worker fabricated a result without an engine")

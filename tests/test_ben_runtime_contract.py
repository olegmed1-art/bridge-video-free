from assistant_lab.ben_runtime import validate_local_ben_url


def test_ben_url_requires_localhost_8085():
    assert validate_local_ben_url("http://127.0.0.1:8085") == "http://127.0.0.1:8085"
    for value in ("https://127.0.0.1:8085", "http://example.com:8085", "http://127.0.0.1:9999"):
        try:
            validate_local_ben_url(value)
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"unsafe BEN URL accepted: {value}")

"""Preserve the existing build command unless the separate book hook stops it."""
import subprocess
import sys
from .build_once import main as observe_book


def main():
    result = observe_book()
    if result:
        return result
    return subprocess.run([sys.executable, "-m", "tools.canon_auth.build_once"], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())

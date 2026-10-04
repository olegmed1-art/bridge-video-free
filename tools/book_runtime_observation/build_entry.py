"""Temporary validation entry: every invocation fails; no canon delegation."""
from .build_once import main as observe_book


def main():
    observe_book()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

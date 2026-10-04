"""Run: python -m experiments.tournament_teacher --test-only < request.json"""
import argparse
import json
import sys

from .consumer import decide


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-only", action="store_true")
    args = parser.parse_args()
    try:
        request = json.load(sys.stdin)
    except (ValueError, UnicodeError, RecursionError):
        request = None
    print(json.dumps(decide(request, enabled=args.test_only), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

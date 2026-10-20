#!/usr/bin/env python3
"""Keep sensitive-path required-reviewer org rulesets equal to reviewed config (#1816)."""
import argparse
import json
from pathlib import Path
import sys

# `python3 -I` does not put the script directory on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from org_ruleset_api import ContractError, load_json  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/required-reviewer-rulesets.json"


def read_config(path=CONFIG):
    return load_json(path.read_text(encoding="utf-8"), str(path))


def parse_arguments(arguments):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("render",))
    return parser.parse_args(arguments)


def main(arguments=None):
    parse_arguments(sys.argv[1:] if arguments is None else arguments)
    config = read_config()
    print(json.dumps(config["rulesets"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)

"""Cache workflow fixture YAML parsed by the conformance matrix."""

from functools import lru_cache
from pathlib import Path

import yaml


@lru_cache(maxsize=None)
def load_yaml_document(path: Path) -> dict:
    """Parse a checked-in fixture once per test process.

    Callers must treat returned documents as read-only. Mutation cases parse
    fresh documents and write them to unique temporary paths.
    """
    return yaml.safe_load(path.read_text(encoding='utf-8'))

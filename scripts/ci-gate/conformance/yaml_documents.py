"""Cache workflow fixture YAML parsed by the conformance matrix."""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import yaml


@lru_cache(maxsize=None)
def _parsed_yaml_document(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def load_yaml_document(path: Path) -> dict:
    """Parse a checked-in fixture once per test process.

    Each caller receives its own copy: copying a parsed document is far cheaper
    than parsing it again, and a caller that edits its copy cannot change what
    the next test reads.
    """
    return deepcopy(_parsed_yaml_document(path))

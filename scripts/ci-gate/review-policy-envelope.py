#!/usr/bin/env python3
import argparse
import base64
import json
import re
import sys

LEGACY_FIELDS = ("actor", "actor_permission", "budget_usd", "model", "pricing_version", "provider")
FIELDS = (
    "actor", "actor_permission", "authority", "budget_usd", "fallback_budget_usd",
    "fallback_model", "model", "pricing_version", "provider",
)
EXPLICIT_FIELDS = (*FIELDS, "explicit_rereview")
MAX_ENVELOPE_BYTES = 2048


def canonical(policy):
    keys = tuple(sorted(policy)) if isinstance(policy, dict) else ()
    if keys not in (LEGACY_FIELDS, FIELDS, tuple(sorted(EXPLICIT_FIELDS))):
        raise ValueError("policy must contain exactly one supported field set")
    string_fields = tuple(field for field in keys if field != "explicit_rereview")
    if any(not isinstance(policy[field], str) for field in string_fields):
        raise ValueError("every policy field must be a string")
    if "explicit_rereview" in keys and not isinstance(policy["explicit_rereview"], bool):
        raise ValueError("explicit_rereview must be a boolean")
    return json.dumps(policy, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def reject_duplicates(pairs):
    policy = {}
    for key, value in pairs:
        if key in policy:
            raise ValueError(f"duplicate policy field: {key}")
        policy[key] = value
    return policy


def encode(raw):
    policy = json.loads(raw, object_pairs_hook=reject_duplicates)
    encoded = base64.urlsafe_b64encode(canonical(policy)).rstrip(b"=")
    if len(encoded) > MAX_ENVELOPE_BYTES:
        raise ValueError("policy envelope is oversized")
    return encoded.decode("ascii")


def decode(envelope):
    if not envelope or len(envelope.encode("ascii", "ignore")) > MAX_ENVELOPE_BYTES:
        raise ValueError("policy envelope is empty or oversized")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", envelope):
        raise ValueError("policy envelope has invalid alphabet or padding")
    padding = "=" * (-len(envelope) % 4)
    try:
        raw = base64.b64decode(envelope + padding, altchars=b"-_", validate=True)
        text = raw.decode("utf-8")
    except (UnicodeError, ValueError) as error:
        raise ValueError("policy envelope cannot be decoded") from error
    try:
        policy = json.loads(text, object_pairs_hook=reject_duplicates)
    except json.JSONDecodeError as error:
        # A duplicate field is reported by reject_duplicates itself, not masked
        # as undecodable.
        raise ValueError("policy envelope cannot be decoded") from error
    expected = canonical(policy)
    if raw != expected or encode(expected) != envelope:
        raise ValueError("policy JSON or envelope is not canonical")
    return expected.decode("utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("encode", "decode"))
    parser.add_argument("value")
    args = parser.parse_args()
    try:
        print(encode(args.value) if args.mode == "encode" else decode(args.value))
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(f"review policy rejected: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

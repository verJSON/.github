#!/usr/bin/env bash
# Unit tests for assert-release-head-ci.sh (ADR 0291).
set -euo pipefail

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
script=$here/assert-release-head-ci.sh
# Companion of scripts/assert-release-head-ci.sh (ADR 0291).
sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
pass=0
fail=0

pass() { echo "PASS: $1"; pass=$((pass + 1)); }
fail() { echo "FAIL: $1" >&2; fail=$((fail + 1)); }

run_against() {
  local body=$1
  shift
  local handshake
  handshake=$(mktemp)
  python3 - "$body" "$handshake" <<'PY' &
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

body = json.loads(sys.argv[1])
handshake = sys.argv[2]

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_GET(self):
        if "/commits/" not in self.path or "/check-runs" not in self.path:
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps({"total_count": len(body), "check_runs": body}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
with open(handshake, "w", encoding="utf-8") as handle:
    handle.write(f"http://{httpd.server_address[0]}:{httpd.server_address[1]}\n")
httpd.serve_forever()
PY
  local server_pid=$!
  for _ in $(seq 1 100); do
    if [[ -s "$handshake" ]]; then
      break
    fi
    sleep 0.02
  done
  local base
  base=$(head -n 1 "$handshake")
  rm -f "$handshake"
  local status=0
  GITHUB_API_URL=$base GH_TOKEN=test-token \
    "$script" verJSON/example "$sha" "$@" >/tmp/assert-release-head-ci.out 2>/tmp/assert-release-head-ci.err || status=$?
  kill "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
  return "$status"
}

chmod +x "$script"

actions_success='[{"id":10,"name":"tests","status":"completed","conclusion":"success","app":{"id":15368}}]'
if run_against "$actions_success" tests; then
  pass "success from GitHub Actions"
else
  fail "success from GitHub Actions"
fi

if run_against '[]' tests; then
  fail "missing context should fail"
else
  pass "missing context fails"
fi

if run_against '[{"id":11,"name":"tests","status":"completed","conclusion":"failure","app":{"id":15368}}]' tests; then
  fail "failure conclusion should fail"
else
  pass "failure conclusion fails"
fi

if run_against '[{"id":12,"name":"tests","status":"in_progress","conclusion":null,"app":{"id":15368}}]' tests; then
  fail "pending status should fail"
else
  pass "pending status fails"
fi

if run_against '[{"id":13,"name":"tests","status":"completed","conclusion":"success","app":{"id":999}}]' tests; then
  fail "wrong app should fail"
else
  pass "wrong app fails"
fi

if run_against '[{"id":13,"name":"tests","status":"completed","conclusion":"skipped","app":{"id":15368}}]' tests; then
  fail "skipped should fail"
else
  pass "skipped fails"
fi

latest_wins='[{"id":1,"name":"tests","status":"completed","conclusion":"failure","app":{"id":15368}},{"id":99,"name":"tests","status":"completed","conclusion":"success","app":{"id":15368}}]'
if run_against "$latest_wins" tests; then
  pass "latest check-run id wins"
else
  fail "latest check-run id wins"
fi

multi='[{"id":1,"name":"tests","status":"completed","conclusion":"success","app":{"id":15368}},{"id":2,"name":"lint","status":"completed","conclusion":"success","app":{"id":15368}}]'
if run_against "$multi" tests lint; then
  pass "multiple contexts succeed"
else
  fail "multiple contexts succeed"
fi

if env -u GH_TOKEN -u GITHUB_TOKEN "$script" verJSON/example "$sha" tests >/dev/null 2>&1; then
  fail "missing token should fail"
else
  pass "missing token fails"
fi

if GH_TOKEN=x "$script" verJSON/example not-a-sha tests >/dev/null 2>&1; then
  fail "bad sha should fail"
else
  pass "bad sha fails"
fi

echo "$pass passed, $fail failed"
[[ "$fail" -eq 0 ]]

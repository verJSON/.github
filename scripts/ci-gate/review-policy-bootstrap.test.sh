#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/gate-rearm.yml"
fixture="$root/scripts/ci-gate/fixtures/ai-review-caller-a6b3ccc.yml"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

awk '
  /          select_compatible_review_policy\(\) \{/ { found=1 }
  found && /^          }$/ { sub(/^          /, ""); print; exit }
  found { sub(/^          /, ""); print }
' "$workflow" >"$tmp/selector.sh"
[ -s "$tmp/selector.sh" ] || { echo "FAIL: compatibility selector is missing"; exit 1; }

mkdir "$tmp/bin"
cat >"$tmp/bin/gh" <<'SH'
#!/usr/bin/env bash
if [ "${CALLER_READ_FAIL:-false}" = true ]; then exit 1; fi
cat "$CALLER_FILE"
SH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH" ACTIONS_TOKEN=actions-token TARGET_REPO=Verjson/example DEFAULT_BRANCH=main
export AI_REVIEW_DISPATCH_WORKFLOW=ai-review-merge.yml AI_REVIEW_LEGACY_DISPATCH_WORKFLOW=
# shellcheck source=/dev/null
source "$tmp/selector.sh"
decode_envelope() {
  python3 -c 'import base64,sys; value=sys.argv[1]; print(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode())' "$1"
}

policy='{"actor":"trusted-arm","actor_permission":"automation","authority":"human","budget_usd":"auto","explicit_rereview":false,"fallback_budget_usd":"","fallback_model":"","model":"auto","pricing_version":"anthropic-native-v1","provider":"anthropic"}'
export CALLER_FILE="$fixture"
legacy="$(select_compatible_review_policy "$policy")"
decoded="$(decode_envelope "$legacy")"
jq -e 'keys == ["actor","actor_permission","budget_usd","model","pricing_version","provider"]' <<<"$decoded" >/dev/null

cp "$fixture" "$tmp/lookalike.yml"
printf '# attacker-controlled lookalike\n' >>"$tmp/lookalike.yml"
CALLER_FILE="$tmp/lookalike.yml"
current="$(select_compatible_review_policy "$policy")"
decoded="$(decode_envelope "$current")"
jq -e '.authority == "human" and .explicit_rereview == false and has("fallback_model")' <<<"$decoded" >/dev/null

CALLER_FILE="$fixture"
for mutation in \
  '.authority="ai-merge"' \
  '.provider="deepseek"' \
  '.pricing_version="forged"' \
  '.fallback_model="deepseek-v4-flash"' \
  '.fallback_budget_usd="1.00"' \
  '.explicit_rereview=true'; do
  changed="$(jq -c "$mutation" <<<"$policy")"
  if select_compatible_review_policy "$changed" >/dev/null 2>&1; then
    echo "FAIL: legacy caller accepted $mutation"; exit 1
  fi
done

export CALLER_READ_FAIL=true
if select_compatible_review_policy "$policy" >/dev/null 2>&1; then
  echo "FAIL: caller read failure selected a policy"; exit 1
fi

echo "PASS: legacy policy recovery is exact-caller-bound, non-widenable, and fail-closed"

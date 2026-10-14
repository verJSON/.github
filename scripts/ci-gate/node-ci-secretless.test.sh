#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/node-ci.yml"
failures=0

pass() { printf 'ok - %s\n' "$1"; }
fail() { printf 'not ok - %s\n' "$1" >&2; failures=$((failures + 1)); }

python3 - "$workflow" <<'PY' \
  && pass "secretless mode separates credentialed acquisition from PR execution" \
  || fail "secretless mode does not preserve the two-job credential boundary"
import sys
import yaml

doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
inputs = doc[True]["workflow_call"]["inputs"]
secrets = doc[True]["workflow_call"]["secrets"]
jobs = doc["jobs"]
assert inputs["secretless-pr"]["default"] is False
assert "Callers mapping github.token must grant" in inputs["secretless-pr"]["description"]
assert "When mapping github.token" in secrets["NODE_AUTH_TOKEN"]["description"]
assert inputs["approved-internal-packages"]["default"] == ""
assert inputs["approved-internal-scopes"]["default"] == "@verjson"

acquire = jobs["acquire-secretless-dependencies"]
build = jobs["build-test"]
assert acquire["permissions"] == {"contents": "read", "packages": "read"}
assert "inputs.secretless-pr" in acquire["if"]
assert acquire["runs-on"] == "${{ fromJSON(vars.CI_LANE_UNTRUSTED || '[\"ubuntu-24.04\"]') }}"
assert "acquire-secretless-dependencies" in build["needs"]
assert build["permissions"] == {"contents": "read"}
assert build["runs-on"].startswith("${{ inputs.secretless-pr && fromJSON(vars.CI_LANE_UNTRUSTED")
assert build["runs-on"].index("inputs.secretless-pr") < build["runs-on"].index("inputs.runner")

acquire_steps = acquire["steps"]
boundary = next(step for step in acquire_steps if step.get("name") == "Enforce the secretless event boundary")
assert boundary["env"]["HEAD_REPOSITORY"] == "${{ github.event.pull_request.head.repo.full_name }}"
assert boundary["env"]["REPOSITORY"] == "${{ github.repository }}"
assert "same-repository pull request" in boundary["run"]
primary_checkout_indexes = [i for i, step in enumerate(acquire_steps)
                            if str(step.get("uses", "")).startswith("actions/checkout@")
                            and "repository" not in step.get("with", {})]
assert len(primary_checkout_indexes) == 1
primary_checkout_index = primary_checkout_indexes[0]
consumer_config_indexes = [i for i, step in enumerate(acquire_steps)
                           if step.get("name") == "Reject consumer-controlled npm configuration"]
assert len(consumer_config_indexes) == 1
consumer_config_index = consumer_config_indexes[0]
auxiliary_checkout_index = next(i for i, step in enumerate(acquire_steps) if step.get("name") == "Acquire immutable auxiliary source")
install_index = next(i for i, step in enumerate(acquire_steps) if step.get("name") == "Populate verified private dependency cache")
consumer_config = acquire_steps[consumer_config_index]
assert primary_checkout_index < consumer_config_index < auxiliary_checkout_index < install_index
assert "find . -name .npmrc" in consumer_config["run"]
install = next(step for step in acquire_steps if step.get("name") == "Populate verified private dependency cache")
assert install["env"]["NODE_AUTH_TOKEN"] == "${{ secrets.NODE_AUTH_TOKEN }}"
assert install["env"]["NPM_CONFIG_USERCONFIG"].startswith("${{ runner.temp }}/")
assert "find . -name .npmrc" not in install["run"]
assert 'npm cache add "$resolved"' in install["run"]
assert 'rm -rf "$NPM_CONFIG_CACHE/_cacache/index-v5"' in install["run"]
checkout = next(step for step in acquire_steps if str(step.get("uses", "")).startswith("actions/checkout@"))
assert checkout["with"]["persist-credentials"] is False

build_steps = build["steps"]
build_checkout = next(step for step in build_steps if str(step.get("uses", "")).startswith("actions/checkout@"))
assert "inputs.secretless-pr || inputs.secretless-trusted-ref" in build_checkout["with"]["persist-credentials"]
assert "inputs.secretless-pr || inputs.secretless-trusted-ref" in build_checkout["with"]["submodules"]
assert "inputs.secretless-pr || inputs.secretless-trusted-ref" in build_checkout["with"]["token"]
root_install = next(step for step in build_steps if step.get("run") == "npm ci" and "working-directory" not in step)
assert "!(inputs.secretless-pr || inputs.secretless-trusted-ref)" in root_install["if"]
for command in ("npm run build", "npm run typecheck --if-present", "npm test", "npm run lint --if-present"):
    step = next(step for step in build_steps if step.get("run") == command)
    assert "secrets." not in str(step.get("env", {}))
PY

acquire_script="$(mktemp)"
boundary_script="$(mktemp)"
consumer_config_script="$(mktemp)"
python3 - "$workflow" "$boundary_script" "$consumer_config_script" > "$acquire_script" <<'PY'
import sys
import yaml

doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
for step in doc["jobs"]["acquire-secretless-dependencies"]["steps"]:
    if step.get("name") == "Enforce the secretless event boundary":
        open(sys.argv[2], "w", encoding="utf-8").write(step["run"])
    if step.get("name") == "Reject consumer-controlled npm configuration":
        open(sys.argv[3], "w", encoding="utf-8").write(step["run"])
    if step.get("name") == "Populate verified private dependency cache":
        print(step["run"])
PY

if EVENT_NAME=pull_request HEAD_REPOSITORY=Verjson/example REPOSITORY=verJSON/example \
    NODE_AUTH_TOKEN=token SCHEMA_DIR='' APPROVED_INTERNAL_PACKAGES='' \
    SECRETLESS_PR=true SECRETLESS_TRUSTED_REF=false bash "$boundary_script" \
    && ! EVENT_NAME=pull_request HEAD_REPOSITORY=attacker/fork REPOSITORY=verJSON/example \
      NODE_AUTH_TOKEN=token SCHEMA_DIR='' APPROVED_INTERNAL_PACKAGES='' \
      SECRETLESS_PR=true SECRETLESS_TRUSTED_REF=false bash "$boundary_script" >/dev/null 2>&1; then
  pass "case-varied same-repository PRs are admitted and fork PRs fail closed"
else
  fail "secretless event admission does not enforce same-repository pull requests"
fi

acquire_fixture="$(mktemp -d)"
mkdir -p "$acquire_fixture/bin" "$acquire_fixture/clean" "$acquire_fixture/malicious" "$acquire_fixture/trusted-auxiliary"
printf '%s\n' \
  'registry=https://attacker.invalid/' \
  '//attacker.invalid/:_authToken=${NODE_AUTH_TOKEN}' \
  > "$acquire_fixture/malicious/.npmrc"
printf '%s\n' '#!/usr/bin/env bash' \
  'printf '\''%s\n'\'' "$*" >> "$NPM_STUB_LOG"' \
  '[ -z "${NPM_STUB_CONFIG_LOG:-}" ] || "$REAL_NPM" config get registry > "$NPM_STUB_CONFIG_LOG"' \
  'if [ "$1 $2" = "cache add" ]; then' \
  '  while IFS=$'\''\t'\'' read -r _ digest_hex; do' \
  '    content="$NPM_CONFIG_CACHE/_cacache/content-v2/sha512/${digest_hex:0:2}/${digest_hex:2:2}/${digest_hex:4}"' \
  '    mkdir -p "$(dirname "$content")" "$NPM_CONFIG_CACHE/_cacache/index-v5"' \
  '    printf '\''cached private package\n'\'' > "$content"' \
  '  done < "$PRIVATE_CACHE_ENTRIES"' \
  'fi' \
  > "$acquire_fixture/bin/npm"
chmod +x "$acquire_fixture/bin/npm"

trusted_user_config="$acquire_fixture/runner/secretless-acquisition.npmrc"
trusted_global_config="$acquire_fixture/runner/secretless-empty-global.npmrc"
trusted_output="$acquire_fixture/runner/acquisition.output"
trusted_auxiliary_output="$acquire_fixture/runner/auxiliary-acquisition.output"
mkdir -p "$acquire_fixture/runner"
private_digest="$(printf 'cached private package\n' | sha512sum | cut -d' ' -f1)"
private_entries="$acquire_fixture/runner/private-entries"
printf 'https://npm.pkg.github.com/download/@verjson/identity-contracts/1.2.3/abc\t%s\n' \
  "$private_digest" > "$private_entries"
if (cd "$acquire_fixture/clean" && PATH="$acquire_fixture/bin:$PATH" \
    NODE_AUTH_TOKEN='package-secret' NPM_STUB_LOG="$acquire_fixture/npm.log" \
    APPROVED_INTERNAL_SCOPES=$'@tequityapp\n@verjson' \
    NPM_CONFIG_USERCONFIG="$trusted_user_config" \
    NPM_CONFIG_GLOBALCONFIG="$trusted_global_config" PRIVATE_CACHE_ENTRIES="$private_entries" \
    RUNNER_TEMP="$acquire_fixture/runner" RUN_ID=8001 RUN_ATTEMPT=1 GITHUB_OUTPUT="$trusted_output" \
    bash "$acquire_script") \
    && trusted_cache="$(sed -n 's/^cache-dir=//p' "$trusted_output")" \
    && grep -qF 'cache add https://npm.pkg.github.com/download/@verjson/identity-contracts/1.2.3/abc' "$acquire_fixture/npm.log" \
    && grep -qF 'cache verify --cache ' "$acquire_fixture/npm.log" \
    && [ ! -e "$trusted_cache/_cacache/index-v5" ] \
    && grep -qFx 'registry=https://registry.npmjs.org/' "$trusted_user_config" \
    && grep -qFx '@tequityapp:registry=https://npm.pkg.github.com/' "$trusted_user_config" \
    && grep -qFx '@verjson:registry=https://npm.pkg.github.com/' "$trusted_user_config" \
    && grep -qFx '//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}' "$trusted_user_config" \
    && ! grep -q 'package-secret' "$trusted_user_config" \
    && [ ! -s "$trusted_global_config" ]; then
  pass "acquisition forces a trusted host-scoped npm config outside the checkout"
else
  fail "acquisition npm configuration is not fixed and host-scoped"
fi

if (cd "$acquire_fixture/malicious" && bash "$consumer_config_script") >/dev/null 2>&1 \
    || [ -e "$acquire_fixture/malicious.log" ]; then
  fail "a PR-controlled external-registry npmrc reached authenticated npm"
else
  pass "a PR-controlled external-registry/token-interpolation npmrc fails before npm"
fi

real_npm="$(command -v npm)"
if (cd "$acquire_fixture/trusted-auxiliary" && bash "$consumer_config_script" \
    && mkdir -p "$acquire_fixture/trusted-auxiliary/.worker-schema" \
    && printf '%s\n' 'registry=https://attacker.invalid/' \
      '//attacker.invalid/:_authToken=${NODE_AUTH_TOKEN}' \
      > "$acquire_fixture/trusted-auxiliary/.worker-schema/.npmrc" \
    && cd "$acquire_fixture/trusted-auxiliary" \
    && PATH="$acquire_fixture/bin:$PATH" REAL_NPM="$real_npm" \
      NODE_AUTH_TOKEN='package-secret' NPM_STUB_LOG="$acquire_fixture/trusted-auxiliary.log" \
      NPM_STUB_CONFIG_LOG="$acquire_fixture/trusted-auxiliary-config.log" \
      APPROVED_INTERNAL_SCOPES=$'@tequityapp\n@verjson' \
      NPM_CONFIG_USERCONFIG="$trusted_user_config" \
      NPM_CONFIG_GLOBALCONFIG="$trusted_global_config" PRIVATE_CACHE_ENTRIES="$private_entries" \
      RUNNER_TEMP="$acquire_fixture/runner" RUN_ID=8002 RUN_ATTEMPT=1 GITHUB_OUTPUT="$trusted_auxiliary_output" \
      bash "$acquire_script") \
    && grep -qF 'cache add https://npm.pkg.github.com/download/@verjson/identity-contracts/1.2.3/abc' "$acquire_fixture/trusted-auxiliary.log" \
    && grep -qFx 'https://registry.npmjs.org/' "$acquire_fixture/trusted-auxiliary-config.log" \
    && ! grep -qF 'attacker.invalid' "$acquire_fixture/trusted-auxiliary-config.log"; then
  pass "trusted auxiliary root npmrc is materialized after the consumer guard and ignored by npm"
else
  fail "trusted auxiliary root npmrc affected consumer validation or npm acquisition"
fi

validator="$(mktemp)"
trap 'rm -f "$validator"' EXIT
awk '
  /- name: Validate approved internal dependency lock/ { found = 1; next }
  found && /python3 -I - <<'"'"'PY'"'"'/ { copying = 1; next }
  copying && /^          PY$/ { exit }
  copying { sub(/^          /, ""); print }
' "$workflow" > "$validator"

run_validator() {
  local fixture="$1" approved="$2" scopes="${3:-@verjson}" policy="${4:-}"
  local entries="$fixture/private-cache-entries"
  rm -f "$entries"
  (cd "$fixture" && APPROVED_INTERNAL_PACKAGES="$approved" \
    APPROVED_INTERNAL_SCOPES="$scopes" PRIVATE_CACHE_ENTRIES="$entries" \
    TRUSTED_PACKAGE_POLICY="$policy" python3 "$validator")
}

fixture="$(mktemp -d)"
trap 'rm -f "$validator" "$acquire_script" "$boundary_script" "$consumer_config_script"; rm -rf "$fixture" "$acquire_fixture"' EXIT
mkdir -p "$fixture/valid" "$fixture/missing-integrity" "$fixture/second-scope" "$fixture/scoped-root" "$fixture/hidden-name" "$fixture/dot-root" "$fixture/unapproved" "$fixture/external" "$fixture/unused" "$fixture/alias" "$fixture/direct" "$fixture/dot-segment" "$fixture/backslash"

fixture_integrity="sha512-$(printf 'cached private package\n' | openssl dgst -sha512 -binary | base64 -w0)"

make_lock() {
  local dir="$1" resolved="$2"
  printf '%s\n' "{\"lockfileVersion\":3,\"packages\":{\"\":{},\"node_modules/@verjson/identity-contracts\":{\"name\":\"@verjson/identity-contracts\",\"resolved\":\"$resolved\",\"integrity\":\"$fixture_integrity\"}}}" > "$dir/package-lock.json"
}
make_lock "$fixture/valid" "https://npm.pkg.github.com/download/@verjson/identity-contracts/1.2.3/abc"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/@verjson/identity-contracts":{"name":"@verjson/identity-contracts","resolved":"https://npm.pkg.github.com/download/@verjson/identity-contracts/1.2.3/abc"}}}' > "$fixture/missing-integrity/package-lock.json"
printf '%s\n' "{\"lockfileVersion\":3,\"packages\":{\"\":{},\"node_modules/@tequityapp/tequity-schema\":{\"name\":\"@tequityapp/tequity-schema\",\"resolved\":\"https://npm.pkg.github.com/download/@tequityapp/tequity-schema/1.2.3/abc\",\"integrity\":\"$fixture_integrity\"},\"node_modules/@verjson/identity-contracts\":{\"name\":\"@verjson/identity-contracts\",\"resolved\":\"https://npm.pkg.github.com/download/@verjson/identity-contracts/1.2.3/abc\",\"integrity\":\"$fixture_integrity\"}}}" > "$fixture/second-scope/package-lock.json"
printf '%s\n' "{\"lockfileVersion\":3,\"packages\":{\"\":{\"name\":\"@verjson/catalog-worker\",\"version\":\"1.0.0\"},\"node_modules/@verjson/identity-contracts\":{\"name\":\"@verjson/identity-contracts\",\"resolved\":\"https://npm.pkg.github.com/download/@verjson/identity-contracts/1.2.3/abc\",\"integrity\":\"$fixture_integrity\"}}}" > "$fixture/scoped-root/package-lock.json"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{"name":"@verjson/catalog-worker"},"packages/hidden":{"name":"@verjson/unapproved-private"}}}' > "$fixture/hidden-name/package-lock.json"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{"name":"@verjson/catalog-worker"},".":{"name":"@verjson/unapproved-private"}}}' > "$fixture/dot-root/package-lock.json"
make_lock "$fixture/unapproved" "https://npm.pkg.github.com/download/@verjson/identity-contracts/1.2.3/abc"
make_lock "$fixture/external" "https://attacker.invalid/identity-contracts.tgz"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{}}}' > "$fixture/unused/package-lock.json"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/@verjson/identity-contracts":{"name":"attacker-alias","resolved":"https://attacker.invalid/alias.tgz"}}}' > "$fixture/alias/package-lock.json"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/innocent-name":{"resolved":"https://npm.pkg.github.com/download/@verjson/unapproved-private/1.0.0/abc"}}}' > "$fixture/direct/package-lock.json"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/@verjson/identity-contracts":{"resolved":"https://npm.pkg.github.com/download/@verjson/identity-contracts/../../@verjson/unapproved-private/1.0.0/abc"}}}' > "$fixture/dot-segment/package-lock.json"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/@verjson/identity-contracts":{"resolved":"https://npm.pkg.github.com/download/@verjson/identity-contracts/..%5C..%5C@verjson%5Cunapproved-private%5C1.0.0/abc"}}}' > "$fixture/backslash/package-lock.json"

run_validator "$fixture/valid" '@verjson/identity-contracts' >/dev/null 2>&1 \
  && pass "an exact allowlisted GitHub Packages dependency is accepted" \
  || fail "an exact allowlisted GitHub Packages dependency was rejected"
if run_validator "$fixture/missing-integrity" '@verjson/identity-contracts' >/dev/null 2>&1; then
  fail "an approved private package without lock integrity was accepted"
else
  pass "every approved private package requires exact sha512 lock integrity"
fi
package_policy='{"scopes":["@tequityapp","@verjson"],"packages":["@tequityapp/tequity-schema","@verjson/identity-contracts"]}'
run_validator "$fixture/second-scope" $'@tequityapp/tequity-schema\n@verjson/identity-contracts' \
  $'@tequityapp\n@verjson' "$package_policy" >/dev/null 2>&1 \
  && pass "exact packages under two approved scopes are accepted" \
  || fail "a second exact approved internal scope was rejected"
if run_validator "$fixture/second-scope" $'@tequityapp/tequity-schema\n@verjson/identity-contracts' \
    $'@attacker\n@verjson' "$package_policy" >/dev/null 2>&1; then
  fail "PR-controlled internal scopes overrode trusted repository policy"
else
  pass "PR-controlled internal scopes cannot expand trusted package policy"
fi
if run_validator "$fixture/second-scope" $'@tequityapp/tequity-schema\n@verjson/identity-contracts' '@verjson' >/dev/null 2>&1; then
  fail "a package under an unapproved scope was accepted"
else
  pass "a package under an unapproved scope is rejected"
fi
if run_validator "$fixture/valid" '@verjson/identity-contracts' '@verjson/../attacker' >/dev/null 2>&1; then
  fail "a malformed internal scope was accepted"
else
  pass "malformed internal scopes fail closed"
fi
run_validator "$fixture/scoped-root" '@verjson/identity-contracts' >/dev/null 2>&1 \
  && pass "a scoped @verjson root project is ignored while its dependency is validated" \
  || fail "a scoped @verjson root project was treated as an installed dependency"
if run_validator "$fixture/hidden-name" '' >/dev/null 2>&1; then
  fail "an internal dependency hidden under a non-root lock path was ignored"
else
  pass "a named internal dependency under a non-root lock path is validated"
fi
if run_validator "$fixture/dot-root" '' >/dev/null 2>&1; then
  fail "a dot-shaped lock path was mistaken for the root project entry"
else
  pass "only the exact empty lock path receives the root-project exemption"
fi
if run_validator "$fixture/unapproved" '' >/dev/null 2>&1; then
  fail "an unapproved internal dependency was accepted"
else
  pass "an unapproved internal dependency is rejected"
fi
if run_validator "$fixture/external" '@verjson/identity-contracts' >/dev/null 2>&1; then
  fail "an approved name redirected to an external registry was accepted"
else
  pass "an approved name redirected to an external registry is rejected"
fi
if run_validator "$fixture/unused" '@verjson/identity-contracts' >/dev/null 2>&1; then
  fail "an unused approval was accepted"
else
  pass "an unused approval is rejected"
fi
if run_validator "$fixture/alias" '@verjson/identity-contracts' >/dev/null 2>&1; then
  fail "an internal package path aliased to an attacker-controlled package was accepted"
else
  pass "an internal package path alias is rejected"
fi
if run_validator "$fixture/direct" '@verjson/identity-contracts' >/dev/null 2>&1; then
  fail "a direct URL to an unapproved private package was accepted"
else
  pass "a direct URL to an unapproved private package is rejected"
fi
if run_validator "$fixture/dot-segment" '@verjson/identity-contracts' >/dev/null 2>&1; then
  fail "a dot-segment URL normalizing to an unapproved private package was accepted"
else
  pass "a dot-segment private package URL is rejected"
fi
if run_validator "$fixture/backslash" '@verjson/identity-contracts' >/dev/null 2>&1; then
  fail "a backslash URL normalizing to an unapproved private package was accepted"
else
  pass "a backslash-normalized private package URL is rejected"
fi

# #1782: a temporary-root sandbox binding /usr must also bind the Debian
# alternatives farm, or /usr/bin/awk, cc, and similar links dangle. The real
# Bubblewrap regressions run only on the hosted compatibility lane; this keeps
# every sandbox step honest on every runner.
python3 - "$workflow" "${workflow%/*}/node-ci-protected.yml" <<'PY' \
  && pass "every temporary-root Node CI sandbox binds /etc/alternatives read-only (#1782)" \
  || fail "a temporary-root Node CI sandbox omits /etc/alternatives (#1782)"
import sys
import yaml

expected = {
    "Rebuild exact approved lifecycle packages without credentials",
    "Run exact credentialless consumer script plan",
    "Run runtime-resolved compatibility lanes without credentials",
}
for path in sys.argv[1:]:
    steps = yaml.safe_load(open(path, encoding="utf-8"))["jobs"]["build-test"]["steps"]
    sandboxed = {
        step["name"]: step["run"]
        for step in steps
        if '"--tmpfs", "/",' in step.get("run", "")
    }
    assert expected <= set(sandboxed), (path, sorted(sandboxed))
    for name, run in sandboxed.items():
        # A commented-out bind must not satisfy the check.
        run = "\n".join(
            line for line in run.splitlines() if not line.lstrip().startswith("#")
        )
        assert run.count('"--tmpfs", "/",') == 1, (path, name)
        assert (
            '(Path("/etc/alternatives"), "/etc/alternatives"),' in run
            or '(Path("/etc/alternatives").resolve(strict=True), "/etc/alternatives"),' in run
            or '"--ro-bind", Path("/etc/alternatives").resolve(strict=True).as_posix(), "/etc/alternatives",' in run
            or '"--ro-bind-try", "/etc/alternatives", "/etc/alternatives",' in run
        ), (path, name)
PY

grep -qF 'ACTIONS_ID_TOKEN_REQUEST_TOKEN ACTIONS_ID_TOKEN_REQUEST_URL' "$workflow" \
  && grep -qF 'printf '\''NPM_CONFIG_USERCONFIG=%s\n'\''' "$workflow" \
  && pass "PR execution persistently scrubs package, Git, cloud, and OIDC credential paths" \
  || fail "PR execution credential scrubbing is incomplete"

exit "$failures"

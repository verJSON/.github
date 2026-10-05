#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/contract/scripts"
cp "$root/scripts/gen-container-candidate.sh" \
  "$root/scripts/container_release_manifest.py" \
  "$root/scripts/container_private_dependencies.py" \
  "$root/scripts/container_dependency_transfer.py" \
  "$root/scripts/container_candidate_retry.py" \
  "$root/scripts/container_cosign_provenance.py" \
  "$root/scripts/container_registry_destinations.py" \
  "$tmp/contract/scripts/"
git -C "$tmp/contract" init -q
git -C "$tmp/contract" config user.name fixture
git -C "$tmp/contract" config user.email fixture@example.invalid
git -C "$tmp/contract" add scripts
git -C "$tmp/contract" commit -qm fixture
ref="$(git -C "$tmp/contract" rev-parse HEAD)"
generator="$tmp/contract/scripts/gen-container-candidate.sh"

for fixture in single multi; do
  consumer="$tmp/$fixture"
  mkdir -p "$consumer/.github/workflows" "$consumer/scripts"
  cp "$root/scripts/fixtures/container-candidate/$fixture.json" "$consumer/container-candidate.json"
  (cd "$consumer" && "$generator" workflow "$ref" container-candidate.json) \
    > "$consumer/.github/workflows/container-candidate.yml"
  (cd "$consumer" && "$generator" validator "$ref" container-candidate.json) \
    > "$consumer/scripts/container_release_manifest.py"
  (cd "$consumer" && "$generator" destination-helper "$ref" container-candidate.json) \
    > "$consumer/scripts/container_registry_destinations.py"
  (cd "$consumer" && "$generator" contract-test "$ref" container-candidate.json) \
    > "$consumer/scripts/container-candidate-contract.test.sh"
  chmod +x "$consumer/scripts/"*.sh "$consumer/scripts/"*.py
  jq -e '.images | length > 0' "$consumer/container-candidate.json" >/dev/null
  bash "$consumer/scripts/container-candidate-contract.test.sh"
  cp "$consumer/.github/workflows/container-candidate.yml" "$consumer/.github/workflows/container-candidate.yml.clean"
  sed -i '0,/^    with:$/s//    secrets: inherit\n&/' "$consumer/.github/workflows/container-candidate.yml"
  if bash "$consumer/scripts/container-candidate-contract.test.sh" >/dev/null 2>&1; then
    echo "generated contract accepted credential-routing tampering" >&2
    exit 1
  fi
  mv "$consumer/.github/workflows/container-candidate.yml.clean" "$consumer/.github/workflows/container-candidate.yml"
  cp "$consumer/.github/workflows/container-candidate.yml" "$consumer/.github/workflows/container-candidate.yml.clean"
  sed -i '0,/^      actions: read$/d' "$consumer/.github/workflows/container-candidate.yml"
  if bash "$consumer/scripts/container-candidate-contract.test.sh" >/dev/null 2>&1; then
    echo "generated contract accepted an unsatisfied reusable Actions permission" >&2
    exit 1
  fi
  mv "$consumer/.github/workflows/container-candidate.yml.clean" "$consumer/.github/workflows/container-candidate.yml"
  cp "$consumer/.github/workflows/container-candidate.yml" "$consumer/.github/workflows/container-candidate.yml.clean"
  sed -i 's#container-candidate-publish.yml#container-candidate.yml#' "$consumer/.github/workflows/container-candidate.yml"
  if bash "$consumer/scripts/container-candidate-contract.test.sh" >/dev/null 2>&1; then
    echo "generated contract accepted publication through the read-only entrypoint" >&2
    exit 1
  fi
  mv "$consumer/.github/workflows/container-candidate.yml.clean" "$consumer/.github/workflows/container-candidate.yml"
  cp "$consumer/.github/workflows/container-candidate.yml" "$consumer/.github/workflows/container-candidate.yml.clean"
  sed -i '/^  validate:/,/^  publish:/ s/^      contents: read$/      contents: write/' "$consumer/.github/workflows/container-candidate.yml"
  if bash "$consumer/scripts/container-candidate-contract.test.sh" >/dev/null 2>&1; then
    echo "generated contract accepted write authority in pull-request validation" >&2
    exit 1
  fi
  mv "$consumer/.github/workflows/container-candidate.yml.clean" "$consumer/.github/workflows/container-candidate.yml"
done

private_consumer="$tmp/private-generated"
mkdir -p "$private_consumer/.github/workflows" "$private_consumer/scripts"
jq '.privateNodePackages = ["@verjson/private-package"]' \
  "$root/scripts/fixtures/container-candidate/single.json" \
  > "$private_consumer/container-candidate.json"
(cd "$private_consumer" && "$generator" workflow "$ref" container-candidate.json) \
  > "$private_consumer/.github/workflows/container-candidate.yml"
(cd "$private_consumer" && "$generator" validator "$ref" container-candidate.json) \
  > "$private_consumer/scripts/container_release_manifest.py"
(cd "$private_consumer" && "$generator" destination-helper "$ref" container-candidate.json) \
  > "$private_consumer/scripts/container_registry_destinations.py"
(cd "$private_consumer" && "$generator" contract-test "$ref" container-candidate.json) \
  > "$private_consumer/scripts/container-candidate-contract.test.sh"
chmod +x "$private_consumer/scripts/"*.sh "$private_consumer/scripts/"*.py
PRIVATE_CALLER="$private_consumer/.github/workflows/container-candidate.yml" python3 - <<'PY'
import os
import yaml

with open(os.environ["PRIVATE_CALLER"], encoding="utf-8") as stream:
    caller = yaml.safe_load(stream)

validate = caller["jobs"]["validate"]
assert validate["permissions"] == {"actions": "read", "contents": "read"}, (
    "private-package pull-request validation must remain credential-free"
)
assert "secrets" not in validate, (
    "private-package contents and credentials must not enter PR validation"
)
publish = caller["jobs"]["publish"]
assert publish["secrets"] == {
    "NODE_AUTH_TOKEN": "${{ secrets.NODE_AUTH_TOKEN }}"
}, "trusted publication must retain the narrowly scoped package credential"
PY
bash "$private_consumer/scripts/container-candidate-contract.test.sh"

workflow="$root/.github/workflows/container-candidate.yml"
publish_workflow="$root/.github/workflows/container-candidate-publish.yml"
canary="$root/.github/workflows/container-candidate-reusable-contract.yml"
CALLER_WORKFLOW="$tmp/single/.github/workflows/container-candidate.yml" \
CALLEE_WORKFLOW="$workflow" PUBLISH_WORKFLOW="$publish_workflow" \
CANARY_WORKFLOW="$canary" python3 - <<'PY'
import copy
import os

import yaml


with open(os.environ["CALLER_WORKFLOW"], encoding="utf-8") as stream:
    caller = yaml.safe_load(stream)
with open(os.environ["CALLEE_WORKFLOW"], encoding="utf-8") as stream:
    callee = yaml.safe_load(stream)
with open(os.environ["PUBLISH_WORKFLOW"], encoding="utf-8") as stream:
    publisher = yaml.safe_load(stream)
with open(os.environ["CANARY_WORKFLOW"], encoding="utf-8") as stream:
    canary = yaml.safe_load(stream)

validation_permissions = {"actions": "read", "contents": "read"}
publication_permissions = {
    "actions": "read",
    "contents": "read",
    "packages": "write",
    "id-token": "write",
}
assert caller["jobs"]["validate"]["permissions"] == validation_permissions, (
    "generated pull-request validation permissions are not exact"
)
assert caller["jobs"]["publish"]["permissions"] == publication_permissions, (
    "generated publication permissions are not exact"
)
assert "secrets" not in caller["jobs"]["validate"], (
    "public-only pull-request validation must not receive secrets"
)
assert "secrets" not in caller["jobs"]["publish"], (
    "public-only publication must not receive an unnecessary package token"
)
authority_condition = (
    "(github.event_name == 'push' && github.ref == 'refs/heads/main' "
    "&& github.event.repository.default_branch == 'main') || "
    "(github.event_name == 'workflow_dispatch' "
    "&& github.repository == 'Verjson/.github' "
    "&& github.ref == format('refs/heads/{0}', github.event.repository.default_branch))"
)
candidate_condition = (
    "always() && (" + authority_condition + ") "
    "&& needs.prepare.result == 'success' "
    "&& needs.publish-base.result == 'success' "
    "&& needs.publish-derived.result == 'success' "
    "&& needs.attest-sbom.result == 'success' "
    "&& (needs.mirror-gar.result == 'success' || needs.mirror-gar.result == 'skipped')"
)
mirror_gar_condition = (
    "always() && (" + authority_condition + ") "
    "&& needs.prepare.result == 'success' "
    "&& needs.publish-base.result == 'success' "
    "&& needs.publish-derived.result == 'success' "
    "&& needs.attest-sbom.result == 'success' "
    "&& needs.prepare.outputs.has-gar == 'true'"
)
expected_conditions = {
    "publish-base": (
        "always() && (" + authority_condition + ") "
        "&& needs.prepare.result == 'success' "
        "&& (needs.acquire-private-node-dependencies.result == 'success' "
        "|| needs.acquire-private-node-dependencies.result == 'skipped')"
    ),
    "publish-derived": (
        "always() && (" + authority_condition + ") "
        "&& needs.prepare.result == 'success' "
        "&& (needs.acquire-private-node-dependencies.result == 'success' "
        "|| needs.acquire-private-node-dependencies.result == 'skipped') "
        "&& needs.publish-base.result == 'success'"
    ),
    "attest-sbom": (
        "always() && (" + authority_condition + ") "
        "&& needs.prepare.result == 'success' "
        "&& needs.publish-base.result == 'success' "
        "&& needs.publish-derived.result == 'success'"
    ),
    "mirror-gar": mirror_gar_condition,
    "candidate-manifest": candidate_condition,
}
expected_acquisition_condition = (
    "always() && (" + authority_condition + ") "
    "&& needs.prepare.result == 'success' "
    "&& needs.prepare.outputs.has-private-node-packages == 'true'"
)
expected_pr_runner_selector = (
    "${{ github.event_name != 'pull_request' && inputs.runner != '' && fromJSON(inputs.runner) "
    "|| github.repository_owner != 'Verjson' && 'ubuntu-24.04' "
    "|| github.event.repository.private == true && fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') "
    "|| fromJSON(vars.CI_LANE_UNTRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') }}"
)

def validate_authority(read_only, publication):
    assert set(read_only["jobs"]) == {
        "prepare", "skip-private-node-build", "pull-request-build"
    }, "read-only entrypoint contains publication authority"
    assert set(publication["jobs"]) == {
        "prepare", "acquire-private-node-dependencies", "publish-base",
        "publish-derived", "attest-sbom", "mirror-gar", "candidate-manifest"
    }, "publication entrypoint has an unexpected static graph"
    for job_name in publication["jobs"]:
        assert publication["jobs"][job_name]["runs-on"] == "ubuntu-24.04", (
            f"deployable publication job {job_name} must use an independently trusted hosted runner"
        )
    acquisition = publication["jobs"]["acquire-private-node-dependencies"]
    assert acquisition["if"] == expected_acquisition_condition, (
        "private dependency acquisition must run only on trusted publication events"
    )
    assert acquisition["outputs"]["transfer-encryption-key"] == (
        "${{ steps.package-node-modules.outputs.encryption-key }}"
    )
    package_transfer = next(
        step for step in acquisition["steps"] if step.get("id") == "package-node-modules"
    )
    assert 'container_dependency_transfer.py" encrypt' in package_transfer["run"]
    assert 'rm -f "$TRANSFER_DIR/container-node-modules.tgz"' in package_transfer["run"]
    assert "encryption-key=%s" in package_transfer["run"]
    assert read_only["permissions"] == {"contents": "read"}
    workflow_call = read_only.get("on", read_only.get(True, {})).get("workflow_call", {})
    assert "secrets" not in workflow_call, "read-only entrypoint must not declare secret inputs"
    assert "acquisition-sha256" not in workflow_call.get("inputs", {}), (
        "read-only entrypoint must not accept private-acquisition code"
    )
    pull_request_build = read_only["jobs"]["pull-request-build"]
    assert pull_request_build["needs"] == "prepare"
    assert "needs.prepare.outputs.has-private-node-packages == 'false'" in pull_request_build["if"], (
        "private-package pull requests must skip all PR-controlled Docker instructions"
    )
    for job_name in ("prepare", "skip-private-node-build", "pull-request-build"):
        assert read_only["jobs"][job_name]["runs-on"] == expected_pr_runner_selector, (
            f"read-only {job_name} runner selection must keep caller overrides off pull requests"
        )
    pr_steps = pull_request_build["steps"]
    assert not any(step.get("uses", "").startswith("actions/cache/restore@") for step in pr_steps), (
        "PR builds must not restore a private dependency cache"
    )
    assert not any("tar -x" in step.get("run", "") for step in pr_steps), (
        "PR builds must not extract private dependency contents"
    )
    assert not any("secrets." in str(step) for step in pr_steps), (
        "PR-controlled Docker instructions must not receive a secret reference"
    )
    empty_context = next(step for step in pr_steps if step.get("name") == "Prepare credential-free dependency build context")
    assert 'mkdir "$context"' in empty_context["run"]
    skip_job = read_only["jobs"]["skip-private-node-build"]
    assert "needs.prepare.outputs.has-private-node-packages == 'true'" in skip_job["if"]
    assert any("privateNodePackages is configured" in step.get("run", "") for step in skip_job["steps"])
    assert publication["permissions"] == {"contents": "read"}
    for job_name, job in read_only["jobs"].items():
        requested = job.get("permissions", read_only["permissions"])
        assert set(requested).issubset(validation_permissions), (
            f"read-only entrypoint job {job_name} requests publication authority"
        )
        assert all(level == "read" for level in requested.values()), (
            f"read-only entrypoint job {job_name} requests write authority"
        )
    for job_name in ("prepare", "acquire-private-node-dependencies"):
        requested = publication["jobs"][job_name].get(
            "permissions", publication["permissions"]
        )
        assert requested == {"contents": "read"}, (
            f"publication {job_name} must remain exact read-only preparation"
        )
        if job_name == "prepare":
            read_only_job = copy.deepcopy(read_only["jobs"][job_name])
            publication_job = copy.deepcopy(publication["jobs"][job_name])
            read_only_job.pop("runs-on")
            publication_job.pop("runs-on")
            assert publication_job["outputs"].pop("mirror-matrix", None) == (
                "${{ steps.config.outputs.mirror-matrix }}"
            )
            publication_config = next(
                step for step in publication_job["steps"] if step.get("id") == "config"
            )
            publication_config_lines = publication_config["run"].splitlines()
            mirror_matrix_line = (
                'mirror_matrix="$(jq -c \'[.[] | select(.gar.provider == "gar")] | '
                'if length == 0 then [{}] else . end\' <<<"$matrix")"'
            )
            mirror_matrix_output_line = '  echo "mirror-matrix=$mirror_matrix"'
            assert publication_config_lines.count(mirror_matrix_line) == 1
            assert publication_config_lines.count(mirror_matrix_output_line) == 1
            publication_config["run"] = "\n".join(
                line
                for line in publication_config_lines
                if line not in {mirror_matrix_line, mirror_matrix_output_line}
            ) + ("\n" if publication_config["run"].endswith("\n") else "")
            assert read_only_job == publication_job, (
                f"shared trusted-input job {job_name} drifted beyond its trust-isolated runner assignment"
            )
    prepare_steps = read_only["jobs"]["prepare"]["steps"]
    checkout = next(step for step in prepare_steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {
        "path": ".container-candidate-source-${{ github.run_id }}-${{ github.run_attempt }}",
        "persist-credentials": False,
    }, "prepare checkout is not run-isolated and credentialless"
    cleanup = next(step for step in prepare_steps if step.get("name") == "Remove isolated candidate source")
    assert cleanup["if"] == "always()"
    assert cleanup["run"] == 'rm -rf "$SOURCE_PATH"'
    build_steps = read_only["jobs"]["pull-request-build"]["steps"]
    build_checkout = next(step for step in build_steps if step.get("uses", "").startswith("actions/checkout@"))
    assert build_checkout["with"] == {
        "path": ".container-candidate-build-source-${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.variant }}",
        "persist-credentials": False,
    }, "pull-request build checkout is not matrix-isolated and credentialless"
    build_paths = next(step for step in build_steps if step.get("id") == "build-paths")
    assert build_paths["name"] == "Validate isolated candidate build paths"
    assert build_paths["env"] == {
        "SOURCE_PATH": ".container-candidate-build-source-${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.variant }}",
        "CONTEXT_RELATIVE_PATH": "${{ matrix.context }}",
        "FILE_RELATIVE_PATH": "${{ matrix.file }}",
    }
    assert 'realpath -e "$source_root/$CONTEXT_RELATIVE_PATH"' in build_paths["run"]
    assert 'realpath -e "$source_root/$FILE_RELATIVE_PATH"' in build_paths["run"]
    assert 'case "$context_path" in "$source_root"|"$source_root"/*)' in build_paths["run"]
    assert 'case "$file_path" in "$source_root"/*)' in build_paths["run"]
    docker_build = next(step for step in build_steps if step.get("uses", "").startswith("docker/build-push-action@"))
    assert docker_build["with"]["context"] == "${{ steps.build-paths.outputs.context }}"
    assert docker_build["with"]["file"] == "${{ steps.build-paths.outputs.file }}"
    build_cleanup = next(step for step in build_steps if step.get("name") == "Remove isolated candidate build source")
    assert build_cleanup["if"] == "always()"
    assert build_cleanup["run"] == 'rm -rf "$SOURCE_PATH"'
    levels = {None: 0, "none": 0, "read": 1, "write": 2}
    for job_name, job in publication["jobs"].items():
        requested = job.get("permissions", publication["permissions"])
        for permission, level in requested.items():
            assert permission in publication_permissions, (
                f"publication caller omits {job_name} permission {permission}"
            )
            assert levels[level] <= levels[publication_permissions[permission]], (
                f"caller cannot satisfy {job_name} permission {permission}: {level}"
            )
    for job_name, expected in expected_conditions.items():
        actual = " ".join(publication["jobs"][job_name]["if"].split())
        assert actual == expected, (
            f"{job_name} publication authority predicate drifted"
        )
    assert set(publication["jobs"]["candidate-manifest"]["needs"]) == candidate_producers, (
        "candidate manifest direct producers drifted"
    )

candidate_producers = {"prepare", "publish-base", "publish-derived", "attest-sbom", "mirror-gar"}
candidate_job = publisher["jobs"]["candidate-manifest"]
assert candidate_job["if"] == candidate_condition

# GitHub skips a job with any skipped dependency unless always() opts the job into
# evaluation. All direct producers must then be terminal-success: an unrelated
# optional dependency may skip without suppressing a complete manifest, while a
# skipped direct producer remains fail closed.
def candidate_manifest_runs(results):
    return (
        candidate_job["if"].startswith("always()")
        and all(results[name] == "success" for name in candidate_producers - {"mirror-gar"})
        and results["mirror-gar"] in ("success", "skipped")
    )

assert candidate_manifest_runs({
    "prepare": "success",
    "publish-base": "success",
    "publish-derived": "success",
    "attest-sbom": "success",
    "mirror-gar": "skipped",
}), "optional skipped dependency suppressed complete candidate manifest"
for terminal_status in ("skipped", "cancelled", "failure"):
    for producer in candidate_producers - {"mirror-gar"}:
        results = {name: "success" for name in candidate_producers}
        results["mirror-gar"] = "skipped"
        results[producer] = terminal_status
        assert not candidate_manifest_runs(results), (
            f"{terminal_status} {producer} admitted candidate manifest"
        )
for terminal_status in ("cancelled", "failure"):
    results = {name: "success" for name in candidate_producers}
    results["mirror-gar"] = terminal_status
    assert not candidate_manifest_runs(results), (
        f"{terminal_status} mirror-gar admitted candidate manifest"
    )

for unsafe_needs in (
    candidate_producers - {"attest-sbom"},
    candidate_producers - {"mirror-gar"},
    candidate_producers | {"acquire-private-node-dependencies"},
):
    mutated = copy.deepcopy(publisher)
    mutated["jobs"]["candidate-manifest"]["needs"] = sorted(unsafe_needs)
    try:
        validate_authority(callee, mutated)
        raise AssertionError("candidate manifest producer graph mutation escaped")
    except AssertionError as error:
        assert "direct producers drifted" in str(error)

unsafe_conditions = [candidate_condition.removeprefix("always() && ")]
unsafe_conditions.extend(
    candidate_condition.replace(f" && needs.{producer}.result == 'success'", "")
    for producer in candidate_producers - {"mirror-gar"}
)
unsafe_conditions.append(candidate_condition.replace(
    " && (needs.mirror-gar.result == 'success' || needs.mirror-gar.result == 'skipped')", ""
))
for unsafe in unsafe_conditions:
    mutated = copy.deepcopy(publisher)
    mutated["jobs"]["candidate-manifest"]["if"] = unsafe
    try:
        validate_authority(callee, mutated)
        raise AssertionError("candidate manifest skip/partial-success mutation escaped")
    except AssertionError as error:
        assert "authority predicate drifted" in str(error)

validate_authority(callee, publisher)
mutated = copy.deepcopy(publisher)
mutated["jobs"]["prepare"]["env"] = {"DRIFT": "true"}
try:
    validate_authority(callee, mutated)
    raise AssertionError("shared preparation drift was accepted")
except AssertionError as error:
    assert "shared trusted-input job prepare drifted" in str(error)
mutated = copy.deepcopy(publisher)
mutated["jobs"]["acquire-private-node-dependencies"]["permissions"] = {"contents": "write"}
try:
    validate_authority(callee, mutated)
    raise AssertionError("unguarded acquisition write widening was accepted")
except AssertionError as error:
    assert "exact read-only preparation" in str(error)
mutated = copy.deepcopy(publisher)
mutated["jobs"]["publish-base"]["if"] += " || github.event_name == 'pull_request'"
try:
    validate_authority(callee, mutated)
    raise AssertionError("pull-request publication disjunction was accepted")
except AssertionError as error:
    assert "authority predicate drifted" in str(error)
assert canary["jobs"]["validate"]["permissions"] == validation_permissions
assert canary["jobs"]["publish"]["permissions"] == publication_permissions
assert canary["jobs"]["validate"]["uses"] == "./.github/workflows/container-candidate.yml"
assert canary["jobs"]["publish"]["uses"] == "./.github/workflows/container-candidate-publish.yml"
assert canary["jobs"]["publish"]["if"] == (
    "github.event_name == 'workflow_dispatch' "
    "&& github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
), "privileged canary dispatch must be bound to the repository default branch"
expected_runner = (
    "${{ github.repository_owner == 'Verjson' && "
    "(vars.CI_RUNNER_DEFAULT || '[\"self-hosted\",\"general\"]') || "
    "'[\"ubuntu-24.04\"]' }}"
)
for job in ("validate", "publish"):
    assert canary["jobs"][job]["with"]["runner"] == expected_runner, (
        "reusable-call canary must use the canonical organization-aware runner lane"
    )
PY
python3 "$root/scripts/container_private_dependencies.test.py"
python3 "$root/scripts/container_registry_destinations.test.py"
python3 "$root/scripts/container_oci_index.test.py"
[ "$(jq -r '((.privateNodePackages // []) | length > 0)' "$root/scripts/fixtures/container-candidate/single.json")" = false ]
private_config="$tmp/private-container-candidate.json"
jq '.privateNodePackages = ["@verjson/private-package"]' \
  "$root/scripts/fixtures/container-candidate/single.json" >"$private_config"
[ "$(jq -r '((.privateNodePackages // []) | length > 0)' "$private_config")" = true ]

prepare_script="$tmp/prepare-config.sh"
mock_bin="$tmp/mock-bin"
runner_temp="$tmp/runner-temp"
mkdir -p "$mock_bin" "$runner_temp"
cat >"$mock_bin/curl" <<'CURL'
#!/usr/bin/env bash
set -euo pipefail
url=""
output=""
while (($#)); do
  case "$1" in
    -fsSL) shift ;;
    -o) output="$2"; shift 2 ;;
    *) url="$1"; shift ;;
  esac
done
[[ "$url" == */scripts/container_registry_destinations.py ]]
[[ -n "$output" ]]
cp "$CONTAINER_DESTINATION_HELPER" "$output"
CURL
chmod +x "$mock_bin/curl"
extract_config_run() {
  local source=$1
  local destination=$2

  if ! awk '
    function fail(message) {
      print message > "/dev/stderr"
      failed = 1
      exit 1
    }

    in_script && /^          / {
      sub(/^          /, "")
      print
      if ($0 !~ /^[[:space:]]*$/) found_body = 1
      next
    }
    in_script && /^ *$/ { print ""; next }
    in_script { exit }

    $0 == "  prepare:" {
      found_prepare = 1
      in_prepare = 1
      next
    }
    in_prepare && /^  [A-Za-z0-9_.-]+:/ {
      fail("prepare job ended before its config run block")
    }
    in_prepare && $0 == "      - id: config" {
      if (found_step) fail("prepare job contains multiple config steps")
      found_step = 1
      in_step = 1
      next
    }
    in_step && /^      - / { fail("config step is missing a run block") }
    in_step && /^        run:/ {
      if ($0 != "        run: |") {
        fail("config step run block is not a literal block")
      }
      found_run = 1
      in_step = 0
      in_script = 1
      next
    }

    END {
      if (failed) exit 1
      if (!found_prepare) fail("prepare job is missing")
      if (!found_step) fail("config step is missing")
      if (!found_run) fail("config step is missing a run block")
      if (!found_body) fail("config step run block is empty")
    }
  ' "$source" >"$destination"; then
    rm -f "$destination"
    return 1
  fi
}

extraction_fixture="$tmp/config-step.yml"
cat >"$extraction_fixture" <<'YAML'
jobs:
  prepare:
    steps:
      - id: config
        name: Prepare candidate configuration
        shell: bash
        env:
          OPTIONAL_KEY: present
        run: |
          printf 'config\n' >"$EXTRACTION_MARKER"
      - name: Unrelated step
        run: |
          printf 'unrelated\n' >"$EXTRACTION_MARKER"
YAML
extracted_fixture_script="$tmp/extracted-config-step.sh"
extract_config_run "$extraction_fixture" "$extracted_fixture_script"
extraction_marker="$tmp/extraction-marker"
EXTRACTION_MARKER="$extraction_marker" bash "$extracted_fixture_script"
grep -qx config "$extraction_marker"

missing_run_fixture="$tmp/config-step-missing-run.yml"
cat >"$missing_run_fixture" <<'YAML'
jobs:
  prepare:
    steps:
      - id: config
        name: Missing run block
  later:
    steps:
      - id: config
        run: |
          exit 0
YAML
if extract_config_run "$missing_run_fixture" "$tmp/missing-run.sh" 2>/dev/null; then
  echo "config extraction accepted a missing run block" >&2
  exit 1
fi

malformed_run_fixture="$tmp/config-step-malformed-run.yml"
cat >"$malformed_run_fixture" <<'YAML'
jobs:
  prepare:
    steps:
      - id: config
        shell: bash
        run: >
          exit 0
YAML
if extract_config_run "$malformed_run_fixture" "$tmp/malformed-run.sh" 2>/dev/null; then
  echo "config extraction accepted a malformed run block" >&2
  exit 1
fi

empty_run_fixture="$tmp/config-step-empty-run.yml"
cat >"$empty_run_fixture" <<'YAML'
jobs:
  prepare:
    steps:
      - id: config
        run: |

YAML
if extract_config_run "$empty_run_fixture" "$tmp/empty-run.sh" 2>/dev/null; then
  echo "config extraction accepted an empty run block" >&2
  exit 1
fi

blank_line_fixture="$tmp/config-step-blank-line.yml"
printf '%s\n' \
  'jobs:' \
  '  prepare:' \
  '    steps:' \
  '      - id: config' \
  '        run: |' \
  '          printf '\''before\n'\'' >"$EXTRACTION_MARKER"' \
  '        ' \
  '          printf '\''after\n'\'' >>"$EXTRACTION_MARKER"' \
  >"$blank_line_fixture"
blank_line_script="$tmp/config-step-blank-line.sh"
extract_config_run "$blank_line_fixture" "$blank_line_script"
blank_line_marker="$tmp/config-step-blank-line-marker"
EXTRACTION_MARKER="$blank_line_marker" bash "$blank_line_script"
[ "$(sed -n '1p' "$blank_line_marker")" = before ]
[ "$(sed -n '2p' "$blank_line_marker")" = after ]

extract_config_run "$workflow" "$prepare_script"
bash -n "$prepare_script"
first_adoption="$tmp/first-adoption"
mkdir -p "$first_adoption"
git -C "$first_adoption" init -q
git -C "$first_adoption" config user.name fixture
git -C "$first_adoption" config user.email fixture@example.invalid
printf 'base\n' >"$first_adoption/README.md"
git -C "$first_adoption" add README.md
git -C "$first_adoption" commit -qm base-without-container-config
first_adoption_base="$(git -C "$first_adoption" rev-parse HEAD)"
cp "$root/scripts/fixtures/container-candidate/single.json" \
  "$first_adoption/container-candidate.json"
git -C "$first_adoption" add container-candidate.json
git -C "$first_adoption" commit -qm add-container-config
[ ! -e "$first_adoption/package-lock.json" ]
if git -C "$first_adoption" cat-file -e "$first_adoption_base:container-candidate.json" 2>/dev/null; then
  echo "first-adoption fixture unexpectedly has candidate config on its base" >&2
  exit 1
fi
first_adoption_output="$tmp/first-adoption-output"
(
  cd "$first_adoption"
  CONFIG_RELATIVE_PATH=container-candidate.json \
    CONTAINER_DESTINATION_HELPER="$tmp/contract/scripts/container_registry_destinations.py" \
    CONTRACT_REF="$ref" \
    GITHUB_OUTPUT="$first_adoption_output" \
    GITHUB_REPOSITORY=Verjson/example \
    GITHUB_REPOSITORY_OWNER=Verjson \
    GITHUB_RUN_ATTEMPT=1 \
    GITHUB_RUN_ID=12345 \
    JOB_WORKFLOW_SHA="$ref" \
    RETRY_SHA256="$(printf 'c%.0s' {1..64})" \
    RUNNER_TEMP="$runner_temp" \
    PATH="$mock_bin:$PATH" \
    SOURCE_PATH=. \
    bash "$prepare_script"
)
grep -qx 'has-private-node-packages=false' "$first_adoption_output"

case_variant_consumer="$tmp/case-variant-consumer"
mkdir -p "$case_variant_consumer"
cp "$root/scripts/fixtures/container-candidate/canary.json" \
  "$case_variant_consumer/container-candidate.json"
case_variant_output="$tmp/case-variant-output"
(
  cd "$case_variant_consumer"
  CONFIG_RELATIVE_PATH=container-candidate.json \
    CONTAINER_DESTINATION_HELPER="$tmp/contract/scripts/container_registry_destinations.py" \
    CONTRACT_REF="$ref" \
    GITHUB_OUTPUT="$case_variant_output" \
    GITHUB_REPOSITORY=verJSON/.github \
    GITHUB_REPOSITORY_OWNER=verJSON \
    GITHUB_RUN_ATTEMPT=1 \
    GITHUB_RUN_ID=12345 \
    JOB_WORKFLOW_SHA="$ref" \
    RETRY_SHA256="$(printf 'c%.0s' {1..64})" \
    RUNNER_TEMP="$runner_temp" \
    PATH="$mock_bin:$PATH" \
    SOURCE_PATH=. \
    bash "$prepare_script"
)
grep -qx 'has-gar=false' "$case_variant_output"

run_invalid_config() {
  local config_path=$1
  local output=$2

  if (
    cd "$first_adoption"
      CONFIG_RELATIVE_PATH="$config_path" \
      CONTAINER_DESTINATION_HELPER="$tmp/contract/scripts/container_registry_destinations.py" \
      CONTRACT_REF="$ref" \
      GITHUB_OUTPUT="$output" \
      GITHUB_REPOSITORY=Verjson/example \
      GITHUB_REPOSITORY_OWNER=Verjson \
      GITHUB_RUN_ATTEMPT=1 \
      GITHUB_RUN_ID=12345 \
      JOB_WORKFLOW_SHA="$ref" \
      RETRY_SHA256="$(printf 'c%.0s' {1..64})" \
      RUNNER_TEMP="$runner_temp" \
      PATH="$mock_bin:$PATH" \
      SOURCE_PATH=. \
      bash "$prepare_script"
  ) >/dev/null 2>&1; then
    echo "candidate config unexpectedly passed: $config_path" >&2
    exit 1
  fi
}

cp "$root/scripts/fixtures/container-candidate/single.json" "$tmp/outside-candidate.json"
run_invalid_config ../outside-candidate.json "$tmp/traversal-output"
ln -s "$tmp/outside-candidate.json" "$first_adoption/escape.json"
run_invalid_config escape.json "$tmp/symlink-output"
for field in context file; do
  jq --arg field "$field" '.images[0][$field] = "../../outside"' \
    "$root/scripts/fixtures/container-candidate/single.json" \
    > "$first_adoption/malicious-$field.json"
  run_invalid_config "malicious-$field.json" "$tmp/malicious-$field-output"
done
if (cd "$first_adoption" && "$generator" workflow "$ref" 'a/../../outside.json') >/dev/null 2>&1; then
  echo "generator accepted traversal-bearing config-path" >&2
  exit 1
fi

build_paths_script="$tmp/validate-build-paths.sh"
BUILD_PATHS_SCRIPT="$build_paths_script" WORKFLOW="$workflow" python3 - <<'PY'
import os
import yaml

with open(os.environ["WORKFLOW"], encoding="utf-8") as stream:
    workflow = yaml.safe_load(stream)
step = next(
    item for item in workflow["jobs"]["pull-request-build"]["steps"]
    if item.get("id") == "build-paths"
)
with open(os.environ["BUILD_PATHS_SCRIPT"], "w", encoding="utf-8") as stream:
    stream.write(step["run"])
PY
build_source="$tmp/build-source"
outside_build="$tmp/outside-build"
mkdir -p "$build_source/context" "$outside_build/context"
touch "$build_source/Dockerfile" "$outside_build/Dockerfile"
GITHUB_OUTPUT="$tmp/valid-build-paths-output" SOURCE_PATH="$build_source" \
  CONTEXT_RELATIVE_PATH=context FILE_RELATIVE_PATH=Dockerfile \
  bash "$build_paths_script"
grep -qx "context=$(realpath -e "$build_source/context")" "$tmp/valid-build-paths-output"
grep -qx "file=$(realpath -e "$build_source/Dockerfile")" "$tmp/valid-build-paths-output"
ln -s "$outside_build/context" "$build_source/escaped-context"
if GITHUB_OUTPUT="$tmp/escaped-context-output" SOURCE_PATH="$build_source" \
  CONTEXT_RELATIVE_PATH=escaped-context FILE_RELATIVE_PATH=Dockerfile \
  bash "$build_paths_script" >/dev/null 2>&1; then
  echo "candidate context symlink escape unexpectedly passed" >&2
  exit 1
fi
ln -s "$outside_build/Dockerfile" "$build_source/escaped-Dockerfile"
if GITHUB_OUTPUT="$tmp/escaped-file-output" SOURCE_PATH="$build_source" \
  CONTEXT_RELATIVE_PATH=context FILE_RELATIVE_PATH=escaped-Dockerfile \
  bash "$build_paths_script" >/dev/null 2>&1; then
  echo "candidate Dockerfile symlink escape unexpectedly passed" >&2
  exit 1
fi

repository_marker="$tmp/repository-injection-executed"
jq --arg repository "ghcr.io/verjson/x'; touch $repository_marker; #'" \
  '.images[0].repository = $repository' \
  "$root/scripts/fixtures/container-candidate/single.json" \
  > "$first_adoption/malicious-repository.json"
run_invalid_config malicious-repository.json "$tmp/malicious-repository-output"
[ ! -e "$repository_marker" ] || {
  echo "repository configuration executed as shell source" >&2
  exit 1
}

platform_marker="$tmp/platform-injection-executed"
jq --arg os "linux'; touch $platform_marker; #'" \
  '.images[0].platforms[0].os = $os' \
  "$root/scripts/fixtures/container-candidate/single.json" \
  > "$first_adoption/malicious-platform.json"
run_invalid_config malicious-platform.json "$tmp/malicious-platform-output"
[ ! -e "$platform_marker" ] || {
  echo "platform configuration executed as shell source" >&2
  exit 1
}

jq '.images[0].platforms = [
  {"os":"linux","architecture":"amd64-v8"},
  {"os":"linux","architecture":"amd64","variant":"v8"}
]' "$root/scripts/fixtures/container-candidate/single.json" \
  > "$first_adoption/colliding-platform-fields.json"
run_invalid_config colliding-platform-fields.json "$tmp/colliding-platform-fields-output"

jq '
  .images[0].variant = "a-b"
  | .images[0].platforms = [{"os":"linux","architecture":"amd64"}]
  | .images[1].variant = "a"
  | .images[1].baseVariant = "a-b"
  | .images[1].platforms = [{"os":"b-linux","architecture":"amd64"}]
' "$root/scripts/fixtures/container-candidate/multi.json" \
  > "$first_adoption/colliding-image-platform-fields.json"
run_invalid_config colliding-image-platform-fields.json "$tmp/colliding-image-platform-fields-output"

lifecycle="$tmp/lifecycle"
mkdir -p "$lifecycle/package"
cat > "$lifecycle/package/package.json" <<JSON
{"name":"lifecycle-probe","version":"1.0.0","scripts":{"postinstall":"touch $tmp/lifecycle-exfiltrated"}}
JSON
cat > "$lifecycle/package.json" <<'JSON'
{"name":"consumer","version":"1.0.0","dependencies":{"lifecycle-probe":"file:package"}}
JSON
flock /tmp/npm-ci-host.lock npm install --prefix "$lifecycle" --package-lock-only --ignore-scripts --no-audit --no-fund >/dev/null
flock /tmp/npm-ci-host.lock npm ci --prefix "$lifecycle" --ignore-scripts --no-audit --no-fund >/dev/null
[ ! -e "$tmp/lifecycle-exfiltrated" ] || { echo "npm lifecycle executed during credentialed acquisition" >&2; exit 1; }
grep -q "github.event_name == 'pull_request'" "$workflow"
grep -q "github.event_name == 'push'" "$publish_workflow"
grep -q 'github.event.repository.default_branch' "$publish_workflow"
grep -q 'packages: write' "$publish_workflow"
grep -q 'id-token: write' "$publish_workflow"
! grep -Eq 'attestations: write|actions/attest|gh attestation verify' "$publish_workflow"
grep -q 'sigstore/cosign-installer@[0-9a-f]\{40\} # v4.1.2' "$publish_workflow"
grep -q 'oras-project/setup-oras@[0-9a-f]\{40\} # v2.0.2' "$publish_workflow"
grep -q '"attest-blob"' "$root/scripts/container_cosign_provenance.py"
grep -q '"verify-blob-attestation"' "$root/scripts/container_cosign_provenance.py"
grep -q 'promotionEligible:false' "$publish_workflow"
grep -qF 'spdx-document --sbom-index "$RUNNER_TEMP/sbom-index.json"' "$publish_workflow"
grep -qF -- '--platform "$platform_key" > sbom.spdx.json' "$publish_workflow"
grep -qF 'predicateType:"https://spdx.dev/Document/v2.3"' "$publish_workflow"
grep -qF 'python3 "$helper" index --index "$raw_index" --reviewed-platforms "$reviewed_platforms"' "$publish_workflow"
grep -qF 'python3 "$helper" spdx-evidence --manifest "$evidence_manifest"' "$publish_workflow"
grep -qF 'platforms:$platforms' "$publish_workflow"
grep -q 'commit identity already records a different digest' "$publish_workflow"
grep -q 'imagetools create -t "\$commit_tag"' "$publish_workflow"
mirror_gar_job="$(awk '/^  mirror-gar:/{seen=1} /^  candidate-manifest:/{seen=0} seen' "$publish_workflow")"
grep -q "github.event_name == 'push'" <<<"$mirror_gar_job"
grep -q "needs.prepare.outputs.has-gar == 'true'" <<<"$mirror_gar_job"
grep -q 'id-token: write' <<<"$mirror_gar_job"
grep -q 'google-github-actions/auth@[0-9a-f]\{40\}' <<<"$mirror_gar_job"
grep -q 'steps.gar-auth.outputs.auth_token' <<<"$mirror_gar_job"
grep -qF '[.[] | select(.variant == $variant)] | if length == 1 then (.[0] | del(.variant))' "$publish_workflow"
grep -q -- '"cp", "--recursive"' "$root/scripts/container_registry_destinations.py"
grep -q 'destination digest differs from the candidate digest' "$root/scripts/container_registry_destinations.py"
grep -q 'needs.mirror-gar.result' "$publish_workflow"
grep -qF 'CALLER_WORKFLOW_REF: ${{ github.workflow_ref }}' "$publish_workflow"
grep -qF 'CALLER_WORKFLOW_SHA: ${{ github.workflow_sha }}' "$publish_workflow"
grep -qF 'expected-publisher-workflow-ref "Verjson/.github/.github/workflows/container-candidate-publish.yml@$CONTRACT_REF"' "$publish_workflow"
if awk '/^  pull-request-build:/{seen=1} /^  publish-base:/{seen=0} seen' "$workflow" | grep -E 'attestations: write|packages: write|id-token: write|docker/login-action|push: true' >/dev/null; then
  echo "pull-request build exposes a publication capability" >&2
  exit 1
fi
attest_sbom_job="$(awk '/^  attest-sbom:/{seen=1} /^  candidate-manifest:/{seen=0} seen' "$publish_workflow")"
attest_sbom_permissions="$(awk '
  /^    permissions:$/ { seen=1; next }
  seen && /^    [A-Za-z0-9_.-]+:/ { exit }
  seen { print }
' <<<"$attest_sbom_job" | sed 's/^      //')"
expected_attest_sbom_permissions="$(cat <<'PERMISSIONS'
actions: read
contents: read
id-token: write
packages: write
PERMISSIONS
)"
[ "$attest_sbom_permissions" = "$expected_attest_sbom_permissions" ] || {
  echo "SBOM publication permissions are not exact least privilege" >&2
  exit 1
}
if grep -Eq 'secrets\.|NODE_AUTH_TOKEN|NPM_TOKEN|AWS_|AZURE_|GOOGLE_' <<<"$attest_sbom_job"; then
  echo "SBOM publication exposes a credential beyond its job token" >&2
  exit 1
fi
grep -qF 'uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1' <<<"$attest_sbom_job" || {
  echo "SBOM evidence binding cannot read the reviewed configuration" >&2
  exit 1
}
publish_derived_job="$(awk '/^  publish-derived:/{seen=1} /^  attest-sbom:/{seen=0} seen' "$publish_workflow")"
grep -qF 'REPOSITORY: ${{ matrix.repository }}' <<<"$publish_derived_job"
grep -qF 'IMAGE_VARIANT: ${{ matrix.variant }}' <<<"$publish_derived_job"
grep -qF 'BASE_VARIANT: ${{ matrix.baseVariant }}' <<<"$publish_derived_job"
if grep -qF -- "repository='\${{ matrix.repository }}'" <<<"$publish_derived_job" \
  || grep -qF -- "--arg variant '\${{ matrix.variant }}'" <<<"$publish_derived_job" \
  || grep -qF -- "--arg baseVariant '\${{ matrix.baseVariant }}'" <<<"$publish_derived_job"; then
  echo "publish-derived embeds candidate configuration into shell source" >&2
  exit 1
fi
[ "$(grep -cF 'uses: ./.github/workflows/container-candidate.yml' "$canary")" -eq 1 ] \
  && [ "$(grep -cF 'uses: ./.github/workflows/container-candidate-publish.yml' "$canary")" -eq 1 ] || {
  echo "reusable-call canary does not exercise both trust paths" >&2
  exit 1
}
jq -e '.repository == "verJSON/.github" and .images[0].platforms == [{"os":"linux","architecture":"amd64"}]' \
  "$root/scripts/fixtures/container-candidate/canary.json" >/dev/null
prepare_job="$(awk '/^  prepare:/{seen=1} /^  pull-request-build:/{seen=0} seen' "$workflow")"
acquisition_job="$(awk '/^  acquire-private-node-dependencies:/{seen=1} /^  publish-base:/{seen=0} seen' "$publish_workflow")"
grep -qF 'has-private-node-packages: ${{ steps.config.outputs.has-private-node-packages }}' <<<"$prepare_job"
grep -qF 'has-private-node-packages=$(jq -r' <<<"$prepare_job"
grep -qF 'length > 0)' <<<"$prepare_job"
grep -qF "needs.prepare.outputs.has-private-node-packages == 'true'" <<<"$acquisition_job"
grep -qF "github.event_name == 'push'" <<<"$acquisition_job"
grep -qF "github.event_name == 'workflow_dispatch'" <<<"$acquisition_job"
grep -qF 'NODE_AUTH_TOKEN: ${{ secrets.NODE_AUTH_TOKEN }}' <<<"$acquisition_job"
grep -qF "# static schema predates job.workflow_sha." "$workflow"
grep -qF 'JOB_WORKFLOW_SHA: ${{ fromJSON(toJSON(job)).workflow_sha }}' "$workflow"
grep -qF '[ "$CONTRACT_REF" = "$JOB_WORKFLOW_SHA" ]' "$workflow"
grep -qF 'npm ci --ignore-scripts --no-audit --no-fund' <<<"$acquisition_job"
grep -qF 'corepack pnpm install --frozen-lockfile --ignore-scripts' <<<"$acquisition_job"
grep -qF 'NPM_CONFIG_USERCONFIG="$user_config"' <<<"$acquisition_job"
grep -qF 'NPM_CONFIG_GLOBALCONFIG="$global_config"' <<<"$acquisition_job"
grep -qF 'env -i \' <<<"$acquisition_job"
grep -qF 'if find . -name .npmrc -print -quit' <<<"$acquisition_job"
grep -qF 'git show "$BASE_SHA:$CONFIG_PATH"' <<<"$acquisition_job"
grep -qF '[ "$base_approved" = "$APPROVED_PRIVATE_PACKAGES" ]' <<<"$acquisition_job"

bootstrap="$tmp/bootstrap"
mkdir -p "$bootstrap"
git -C "$bootstrap" init -q
git -C "$bootstrap" config user.name fixture
git -C "$bootstrap" config user.email fixture@example.invalid
cat > "$bootstrap/container-candidate.json" <<'JSON'
{"privateNodePackages":[]}
JSON
git -C "$bootstrap" add container-candidate.json
git -C "$bootstrap" commit -qm base-config
base_sha="$(git -C "$bootstrap" rev-parse HEAD)"
cat > "$bootstrap/container-candidate.json" <<'JSON'
{"privateNodePackages":["@verjson/private-package"]}
JSON
head_approved="$(jq -ce '.privateNodePackages // []' "$bootstrap/container-candidate.json")"
base_approved="$(git -C "$bootstrap" show "$base_sha:container-candidate.json" | jq -ce '.privateNodePackages // []')"
if [ "$base_approved" = "$head_approved" ]; then
  echo "first private-package PR self-authorized credential use" >&2
  exit 1
fi
git -C "$bootstrap" add container-candidate.json
git -C "$bootstrap" commit -qm reviewed-private-config
base_sha="$(git -C "$bootstrap" rev-parse HEAD)"
base_approved="$(git -C "$bootstrap" show "$base_sha:container-candidate.json" | jq -ce '.privateNodePackages // []')"
[ "$base_approved" = "$head_approved" ] || {
  echo "reviewed base allowlist did not authorize second-stage caller adoption" >&2
  exit 1
}

grep -qF 'npm ci --ignore-scripts' <<<"$acquisition_job"
! grep -Eq 'npm (install|run|exec|rebuild)|pnpm (run|exec|rebuild)|yarn' <<<"$acquisition_job"
! grep -Eq 'subprocess|os\.system|extract(all)?\(' "$root/scripts/container_private_dependencies.py"
grep -qF 'transfer-cache-key: ${{ steps.create-node-modules-cache-key.outputs.cache-key }}' <<<"$acquisition_job"
grep -qF 'openssl rand -hex 32' <<<"$acquisition_job"
grep -qF '[[ "$nonce" =~ ^[0-9a-f]{64}$ ]]' <<<"$acquisition_job"
grep -qF 'container-node-modules-${RUN_ID}-${RUN_ATTEMPT}-${nonce}' <<<"$acquisition_job"
grep -qF 'uses: actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9' <<<"$acquisition_job"
grep -qF 'path: .verjson-container-node-modules-${{ github.run_id }}-${{ github.run_attempt }}' <<<"$acquisition_job"
grep -qF 'key: ${{ steps.create-node-modules-cache-key.outputs.cache-key }}' <<<"$acquisition_job"
if grep -qF 'actions/upload-artifact@' <<<"$acquisition_job"; then
  echo "private dependency acquisition still uses organization artifact storage" >&2
  exit 1
fi
grep -qF 'name: Remove local acquisition and transfer state' <<<"$acquisition_job"
grep -qF 'if: always()' <<<"$acquisition_job"
pr_build_block="$(awk '/^  pull-request-build:/{seen=1} seen && /^  [A-Za-z0-9_.-]+:/{if (seen++ > 1) exit} seen {print}' "$workflow")"
grep -qF "needs.prepare.outputs.has-private-node-packages == 'false'" <<<"$pr_build_block"
grep -qF 'mkdir "$context"' <<<"$pr_build_block"
grep -qF 'build-contexts: verjson_node_modules=${{ runner.temp }}/container-node-modules-context' <<<"$pr_build_block"
  if grep -Eq 'actions/cache/restore@|container-node-modules.tgz|\.verjson-container-node-modules-|secrets\.NODE_AUTH_TOKEN' <<<"$pr_build_block" \
    || grep -Fq 'NODE_AUTH_TOKEN: ${{ secrets.NODE_AUTH_TOKEN }}' <<<"$pr_build_block"; then
  echo "pull-request build can receive private dependency contents or credentials" >&2
  exit 1
fi
for build_job in publish-base publish-derived; do
  build_block="$(awk -v start="  $build_job:" '
    $0 == start { seen=1; next }
    seen && /^  [A-Za-z0-9_.-]+:/ { exit }
    seen { print }
  ' "$publish_workflow")"
  job_if="$(awk '
    /^    if:/ { seen=1; print; next }
    seen && /^      / { print; next }
    seen { exit }
  ' <<<"$build_block")"
  [ "$(grep -c '^    if:' <<<"$build_block")" -eq 1 ]
  grep -qx '    if: >-' <<<"$job_if"
  grep -qx '      always()' <<<"$job_if"
  grep -qx "      && needs.prepare.result == 'success'" <<<"$job_if"
  grep -qx "      && (needs.acquire-private-node-dependencies.result == 'success'" <<<"$job_if"
  grep -qx "      || needs.acquire-private-node-dependencies.result == 'skipped')" <<<"$job_if"
  grep -qF 'name: Prepare credential-free dependency build context' <<<"$build_block"
  grep -qF '[ ! -e "$context" ] && [ ! -L "$context" ]' <<<"$build_block"
  grep -qF 'build-contexts: verjson_node_modules=${{ runner.temp }}/container-node-modules-context' <<<"$build_block"
  grep -qF 'uses: actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9' <<<"$build_block"
  grep -qF 'path: .verjson-container-node-modules-${{ github.run_id }}-${{ github.run_attempt }}' <<<"$build_block"
  grep -qF 'key: ${{ needs.acquire-private-node-dependencies.outputs.transfer-cache-key }}' <<<"$build_block"
  grep -qF 'fail-on-cache-miss: true' <<<"$build_block"
  grep -qF 'name: Remove local node_modules transfer state' <<<"$build_block"
  [ "$(grep -cF "needs.prepare.outputs.has-private-node-packages == 'true'" <<<"$build_block")" -eq 4 ]
  grep -qF 'run: rm -rf "$TRANSFER_DIR"' <<<"$build_block"
  if grep -qF 'restore-keys:' <<<"$build_block" || grep -qF 'actions/download-artifact@' <<<"$build_block"; then
    echo "$build_job permits an inexact cache restore or still uses artifact storage" >&2
    exit 1
  fi
  grep -qF '.verjson-lock-sha256' <<<"$build_block"
  grep -qF "NODE_AUTH_TOKEN: ''" <<<"$build_block"
  grep -qF "ACTIONS_ID_TOKEN_REQUEST_TOKEN: ''" <<<"$build_block"
  if grep -Eq 'secrets\.|secret-envs:|^[[:space:]]+secrets:' <<<"$build_block"; then
    echo "$build_job exposes a credential to Docker execution" >&2
    exit 1
  fi
done

WORKFLOW="$workflow" PUBLISH_WORKFLOW="$publish_workflow" python3 - <<'PY'
import os
import yaml

with open(os.environ["WORKFLOW"], encoding="utf-8") as stream:
    workflow = yaml.safe_load(stream)
with open(os.environ["PUBLISH_WORKFLOW"], encoding="utf-8") as stream:
    publisher = yaml.safe_load(stream)

private_gate = "needs.prepare.outputs.has-private-node-packages == 'true'"
for job_name in ("publish-base", "publish-derived"):
    source = workflow if job_name == "pull-request-build" else publisher
    steps = source["jobs"][job_name]["steps"]
    context_steps = [
        step for step in steps
        if step.get("name") == "Prepare credential-free dependency build context"
    ]
    cache_steps = [
        step for step in steps
        if step.get("uses", "").startswith("actions/cache/restore@")
    ]
    verification_steps = [
        step for step in steps
        if "credential-free node_modules context" in step.get("name", "")
    ]
    cleanup_steps = [
        step for step in steps
        if step.get("name", "").startswith("Remove local node_modules")
    ]
    build_steps = [
        step for step in steps
        if step.get("uses", "").startswith("docker/build-push-action@")
    ]

    assert len(context_steps) == 1, f"{job_name}: dependency context step missing"
    assert private_gate not in context_steps[0].get("if", ""), (
        f"{job_name}: empty dependency context is private-package gated"
    )
    for label, guarded_steps in (
        ("cache restore", cache_steps),
        ("lock verification", verification_steps),
        ("transfer cleanup", cleanup_steps),
    ):
        assert len(guarded_steps) == 1, f"{job_name}: {label} step missing"
        assert private_gate in guarded_steps[0].get("if", ""), (
            f"{job_name}: {label} is not private-package gated"
        )
    assert len(build_steps) == 1, f"{job_name}: Docker build step missing"
    assert build_steps[0]["with"]["build-contexts"] == (
        "verjson_node_modules=${{ runner.temp }}/container-node-modules-context"
    ), f"{job_name}: dependency build context is not unconditional"
PY

non_node_runner_temp="$tmp/non-node-runner-temp"
mkdir -p "$non_node_runner_temp"
NON_NODE_SCRIPT="$tmp/non-node-context.sh" WORKFLOW="$workflow" python3 - <<'PY'
import os
import yaml

with open(os.environ["WORKFLOW"], encoding="utf-8") as stream:
    workflow = yaml.safe_load(stream)
steps = workflow["jobs"]["pull-request-build"]["steps"]
step = next(
    item for item in steps
    if item.get("name") == "Prepare credential-free dependency build context"
)
with open(os.environ["NON_NODE_SCRIPT"], "w", encoding="utf-8") as stream:
    stream.write(step["run"])
PY
(
  cd "$tmp/single"
  RUNNER_TEMP="$non_node_runner_temp" bash "$tmp/non-node-context.sh"
)
[ -d "$non_node_runner_temp/container-node-modules-context" ]
[ -z "$(find "$non_node_runner_temp/container-node-modules-context" -mindepth 1 -print -quit)" ]
if RUNNER_TEMP="$non_node_runner_temp" bash "$tmp/non-node-context.sh" >/dev/null 2>&1; then
  echo "dependency context preparation accepted a pre-existing reserved path" >&2
  exit 1
fi
[ ! -e "$tmp/single/package.json" ]
[ ! -e "$tmp/single/package-lock.json" ]
[ ! -e "$tmp/single/node_modules" ]
if grep -Eqi 'setup-node|node_modules|BASE_SHA|git (show|cat-file|ls-tree)' <<<"$prepare_job"; then
  echo "credential-free preparation imposes Node or reviewed-base requirements" >&2
  exit 1
fi
if grep -Eq 'uses: [^ ]+@(main|master|v[0-9]+)$' "$workflow" "$publish_workflow"; then
  echo "container workflow contains an unpinned action" >&2
  exit 1
fi

before="$(sha256sum "$root/scripts/gen-changelog-caller.sh" "$root/.github/workflows/generated-artifacts.yml")"
(cd "$tmp/single" && "$generator" workflow "$ref") >/dev/null
after="$(sha256sum "$root/scripts/gen-changelog-caller.sh" "$root/.github/workflows/generated-artifacts.yml")"
[ "$before" = "$after" ] || { echo "container generator drifted changelog contract artifacts" >&2; exit 1; }
if "$generator" validator "$(printf 'a%.0s' {1..40})" >/dev/null 2>&1; then
  echo "candidate validator generation resolved a nonexistent pin from local files" >&2
  exit 1
fi

bash "$root/scripts/container-contract-coexistence.test.sh"

echo "container candidate canonical contract passed"

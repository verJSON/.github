#!/usr/bin/env python3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".github/workflows/node-ci.yml"
TARGET = ROOT / ".github/workflows/node-ci-protected.yml"

CREDENTIAL_ENV_KEYS = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "NODE_AUTH_TOKEN",
    "NPM_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AZURE_CREDENTIALS",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_URL",
)

CANDIDATE_CACHE_MAX_FILES = 4096
CANDIDATE_CACHE_MAX_BYTES = 268435456
# The complete set of keys a GitHub Actions step object may open with
# (https://docs.github.com/actions/using-workflows/workflow-syntax-for-github-actions#jobsjob_idsteps).
STEP_START_KEYS = (
    "name",
    "id",
    "if",
    "uses",
    "run",
    "shell",
    "working-directory",
    "env",
    "with",
    "continue-on-error",
    "timeout-minutes",
)

PROTECTED_INPUTS = """      protected-type-surface-declaration-path:
        description: Repository-relative declaration fetched from the authenticated pull-request base SHA.
        required: false
        type: string
        default: ''
      protected-type-surface-expected-package:
        description: Protected package identity the base declaration must select.
        required: false
        type: string
        default: ''
      protected-type-surface-expected-script:
        description: Protected compatibility script the base declaration must select.
        required: false
        type: string
        default: ''
      protected-type-surface-allow-prerelease:
        description: Explicitly authorize a prerelease baseline; stable released versions are the default.
        required: false
        type: boolean
        default: false
"""

PROTECTED_BASELINE_STEP = """      - name: Resolve protected type-surface baseline from the pull-request base
        id: resolve-protected-type-surface
        if: needs.eligibility.outputs.should-run != 'false' && inputs.protected-type-surface-declaration-path != ''
        env:
          ALLOW_PRERELEASE: ${{ inputs.protected-type-surface-allow-prerelease }}
          DECLARATION_PATH: ${{ inputs.protected-type-surface-declaration-path }}
          EXPECTED_PACKAGE: ${{ inputs.protected-type-surface-expected-package }}
          EXPECTED_SCRIPT: ${{ inputs.protected-type-surface-expected-script }}
          GH_TOKEN: ${{ github.token }}
          PULL_REQUEST_NUMBER: ${{ github.event.pull_request.number }}
          REPOSITORY: ${{ github.repository }}
          RUN_ATTEMPT: ${{ github.run_attempt }}
          RUN_ID: ${{ github.run_id }}
          RUNNER_TEMP: ${{ runner.temp }}
        run: |
          set -euo pipefail
          python3 - <<'PY'
          import hashlib
          import json
          import os
          import re
          import stat
          import subprocess
          from pathlib import Path

          repository = os.environ["REPOSITORY"]
          declaration_path = os.environ["DECLARATION_PATH"]
          expected_package = os.environ["EXPECTED_PACKAGE"]
          expected_script = os.environ["EXPECTED_SCRIPT"]
          pull_request_number = os.environ["PULL_REQUEST_NUMBER"]
          if (
              not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
              or not re.fullmatch(r"[1-9][0-9]*", pull_request_number)
              or not declaration_path
              or declaration_path.startswith("/")
              or "//" in declaration_path
              or any(segment in ("", ".", "..") for segment in declaration_path.split("/"))
              or not re.fullmatch(r"[A-Za-z0-9._/-]+", declaration_path)
              or not expected_package
              or not expected_script
          ):
              raise SystemExit("protected type-surface declaration inputs are malformed")

          def github_api(arguments):
              result = subprocess.run(
                  ["gh", "api", *arguments],
                  check=False,
                  stdout=subprocess.PIPE,
                  stderr=subprocess.DEVNULL,
              )
              if result.returncode != 0:
                  raise SystemExit("authenticated GitHub declaration lookup failed")
              return result.stdout

          # This is the sole pull-request lookup. The immutable base SHA it
          # returns is the only ref used for the declaration request below.
          base_sha = github_api(
              [f"repos/{repository}/pulls/{pull_request_number}", "--jq", ".base.sha"]
          ).decode("utf-8", errors="strict").strip()
          if re.fullmatch(r"[0-9a-f]{40}", base_sha) is None:
              raise SystemExit("pull-request base SHA is not an immutable commit")
          declaration_bytes = github_api(
              [
                  "-H",
                  "Accept: application/vnd.github.raw+json",
                  f"repos/{repository}/contents/{declaration_path}?ref={base_sha}",
              ]
          )

          class DuplicateObjectKeyError(ValueError):
              pass

          def reject_duplicate_object_keys(pairs):
              result = {}
              for key, value in pairs:
                  if key in result:
                      raise DuplicateObjectKeyError
                  result[key] = value
              return result

          try:
              declaration = json.loads(
                  declaration_bytes.decode("utf-8"),
                  object_pairs_hook=reject_duplicate_object_keys,
              )
          except (UnicodeDecodeError, json.JSONDecodeError, DuplicateObjectKeyError) as error:
              raise SystemExit(f"protected type-surface declaration is invalid: {error}")
          if not isinstance(declaration, dict) or set(declaration) != {"package", "version", "script"}:
              raise SystemExit("protected type-surface declaration requires exactly package, version, and script")

          package = declaration["package"]
          version = declaration["version"]
          script = declaration["script"]
          package_pattern = r"@[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*"
          version_core = r"(?:0|[1-9][0-9]*)\\.(?:0|[1-9][0-9]*)\\.(?:0|[1-9][0-9]*)"
          stable_version = re.compile(version_core)
          prerelease_version = re.compile(
              version_core + r"-(?:[0-9A-Za-z-]+(?:\\.[0-9A-Za-z-]+)*)"
          )
          if (
              not isinstance(package, str)
              or re.fullmatch(package_pattern, package) is None
              or package != expected_package
              or not isinstance(version, str)
              or (stable_version.fullmatch(version) is None
                  and not (os.environ["ALLOW_PRERELEASE"] == "true"
                           and prerelease_version.fullmatch(version)))
              or version == "latest"
              or not isinstance(script, str)
              or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}", script) is None
              or script != expected_script
          ):
              raise SystemExit("protected type-surface declaration is unauthorized")

          receipt = {
              "schemaVersion": 1,
              "repository": repository,
              "declarationPath": declaration_path,
              "baseSha": base_sha,
              "declarationSha256": hashlib.sha256(declaration_bytes).hexdigest(),
              "package": package,
              "version": version,
              "script": script,
          }
          run_root = Path(os.environ["RUNNER_TEMP"]) / (
              f"protected-type-surface-{os.environ['RUN_ID']}-{os.environ['RUN_ATTEMPT']}"
          )
          run_root.mkdir(mode=0o700, parents=True, exist_ok=False)
          receipt_path = run_root / "receipt.json"
          receipt_path.write_text(
              json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\\n",
              encoding="utf-8",
          )
          request = json.dumps(
              {"package": package, "ranges": [version], "script": script},
              separators=(",", ":"),
          )
          with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
              output.write(f"compatibility-ranges={request}\\n")
              output.write(f"receipt-path={receipt_path}\\n")
              output.write(f"base-sha={base_sha}\\n")
              output.write(f"declaration-sha256={receipt['declarationSha256']}\\n")
              output.write(f"package={package}\\n")
              output.write(f"version={version}\\n")
              output.write(f"script={script}\\n")
          PY
"""

PROTECTED_BASELINE_BIND_STEP = """      - name: Bind protected baseline receipt to verified artifact provenance
        if: inputs.protected-type-surface-declaration-path != ''
        env:
          COMPATIBILITY_PROVENANCE: ${{ runner.temp }}/secretless-compatibility-${{ github.run_id }}-${{ github.run_attempt }}/_compatibility/provenance.json
          PROTECTED_BASELINE_RECEIPT: ${{ steps.resolve-protected-type-surface.outputs.receipt-path }}
        run: |
          set -euo pipefail
          python3 - <<'PY'
          import json
          import os
          from pathlib import Path

          class DuplicateObjectKeyError(ValueError):
              pass

          def reject_duplicate_object_keys(pairs):
              result = {}
              for key, value in pairs:
                  if key in result:
                      raise DuplicateObjectKeyError
                  result[key] = value
              return result

          def read_json(path):
              try:
                  return json.loads(
                      Path(path).read_text(encoding="utf-8"),
                      object_pairs_hook=reject_duplicate_object_keys,
                  )
              except (OSError, UnicodeDecodeError, json.JSONDecodeError, DuplicateObjectKeyError) as error:
                  raise SystemExit(f"protected baseline receipt is invalid: {error}")

          receipt = read_json(os.environ["PROTECTED_BASELINE_RECEIPT"])
          receipt_fields = {
              "schemaVersion", "repository", "declarationPath", "baseSha",
              "declarationSha256", "package", "version", "script",
          }
          if not isinstance(receipt, dict) or set(receipt) != receipt_fields or receipt["schemaVersion"] != 1:
              raise SystemExit("protected baseline receipt has an invalid shape")
          provenance_path = Path(os.environ["COMPATIBILITY_PROVENANCE"])
          provenance = read_json(provenance_path)
          if (
              not isinstance(provenance, dict)
              or set(provenance) != {"schemaVersion", "request", "lanes"}
              or provenance["schemaVersion"] != 1
              or not isinstance(provenance["request"], dict)
              or not isinstance(provenance["lanes"], list)
              or len(provenance["lanes"]) != 1
          ):
              raise SystemExit("protected baseline compatibility provenance has an invalid shape")
          request = provenance["request"]
          if (
              set(request) != {"package", "ranges", "script"}
              or request["package"] != receipt["package"]
              or request["ranges"] != [receipt["version"]]
              or request["script"] != receipt["script"]
          ):
              raise SystemExit("protected baseline receipt does not match the compatibility request")
          lane = provenance["lanes"][0]
          if (
              not isinstance(lane, dict)
              or lane.get("package") != receipt["package"]
              or lane.get("range") != receipt["version"]
              or lane.get("version") != receipt["version"]
              or lane.get("script") != receipt["script"]
              or not all(isinstance(lane.get(key), str) and lane[key] for key in ("integrity", "tarball", "sha512"))
          ):
              raise SystemExit("protected baseline artifact provenance does not match the declaration")
          protected_baseline = dict(receipt)
          protected_baseline["artifact"] = {
              "integrity": lane["integrity"],
              "tarball": lane["tarball"],
              "sha512": lane["sha512"],
          }
          provenance["protectedBaseline"] = protected_baseline
          provenance_path.write_text(
              json.dumps(provenance, sort_keys=True, separators=(",", ":")) + "\\n",
              encoding="utf-8",
          )
          PY
"""

PROTECTED_BASELINE_TRANSFER_VALIDATION = """          protected_baseline = provenance.get("protectedBaseline")
          expected_path = os.environ.get("PROTECTED_BASELINE_DECLARATION_PATH", "")
          if expected_path:
              receipt_fields = {
                  "schemaVersion", "repository", "declarationPath", "baseSha",
                  "declarationSha256", "package", "version", "script", "artifact",
              }
              if not isinstance(protected_baseline, dict) or set(protected_baseline) != receipt_fields:
                  raise SystemExit("protected baseline receipt is missing or malformed")
              if (
                  protected_baseline["schemaVersion"] != 1
                  or protected_baseline["repository"] != os.environ["PROTECTED_BASELINE_REPOSITORY"]
                  or protected_baseline["declarationPath"] != expected_path
                  or protected_baseline["baseSha"] != os.environ["EXPECTED_PROTECTED_BASELINE_BASE_SHA"]
                  or protected_baseline["declarationSha256"] != os.environ["EXPECTED_PROTECTED_BASELINE_DECLARATION_SHA256"]
                  or protected_baseline["package"] != os.environ["PROTECTED_BASELINE_EXPECTED_PACKAGE"]
                  or protected_baseline["script"] != os.environ["PROTECTED_BASELINE_EXPECTED_SCRIPT"]
                  or not isinstance(protected_baseline["version"], str)
                  or request != {
                      "package": protected_baseline["package"],
                      "ranges": [protected_baseline["version"]],
                      "script": protected_baseline["script"],
                  }
                  or not isinstance(protected_baseline["artifact"], dict)
                  or set(protected_baseline["artifact"]) != {"integrity", "tarball", "sha512"}
                  or len(provenance.get("lanes", [])) != 1
                  or not isinstance(provenance["lanes"][0], dict)
                  or any(
                      protected_baseline["artifact"][key] != provenance["lanes"][0].get(key)
                      for key in ("integrity", "tarball", "sha512")
                  )
                  or provenance["lanes"][0].get("package") != protected_baseline["package"]
                  or provenance["lanes"][0].get("version") != protected_baseline["version"]
                  or provenance["lanes"][0].get("script") != protected_baseline["script"]
              ):
                  raise SystemExit("protected baseline receipt does not bind declaration, base, and artifact")
          elif protected_baseline is not None:
              raise SystemExit("unexpected protected baseline receipt")
"""

PROTECTED_BASELINE_REF_STEP = """      - name: Export protected type-surface base SHA
        if: needs.eligibility.outputs.should-run != 'false' && inputs.protected-type-surface-declaration-path != ''
        env:
          BASE_SHA: ${{ needs.acquire-secretless-dependencies.outputs.protected-baseline-base-sha }}
        run: |
          set -euo pipefail
          [[ "$BASE_SHA" =~ ^[0-9a-f]{40}$ ]] || {
            echo "::error::protected type-surface base SHA is unavailable"
            exit 1
          }
          echo "VERJSON_TYPE_SURFACE_BASE_SHA=$BASE_SHA" >> "$GITHUB_ENV"
"""

INPUTS = """      event-name:\n        description: Authenticated pull-request event identity.\n        required: true\n        type: string\n+      head-repository:\n        description: Authenticated pull-request head repository.\n        required: true\n        type: string\n+      head-sha:\n        description: Authenticated immutable pull-request head SHA.\n        required: true\n        type: string\n+"""

VERIFY_STEP = """      - name: Revalidate protected pull-request identity\n        env:\n          ADMITTED_EVENT: ${{ inputs.event-name }}\n          ADMITTED_HEAD_REPOSITORY: ${{ inputs.head-repository }}\n          ADMITTED_HEAD_SHA: ${{ inputs.head-sha }}\n          GH_TOKEN: ${{ github.token }}\n          REPOSITORY: ${{ github.repository }}\n          RUN_ID: ${{ github.run_id }}\n        run: |\n          set -euo pipefail\n          [ "$ADMITTED_EVENT" = pull_request ]\n          [ -n "$ADMITTED_HEAD_REPOSITORY" ]\n          [[ "$ADMITTED_HEAD_SHA" =~ ^[0-9a-f]{40}$ ]]\n          [[ "$RUN_ID" =~ ^[1-9][0-9]*$ ]]\n          run_record="$(gh api "repos/$REPOSITORY/actions/runs/$RUN_ID" --jq '[.event,.head_sha,(.pull_requests|length),(.pull_requests[0].number//"")]|@tsv')"\n          IFS=$'\\t' read -r run_event run_head binding_count pr_number <<<"$run_record"\n          [ "$run_event" = "$ADMITTED_EVENT" ]\n          [ "$run_head" = "$ADMITTED_HEAD_SHA" ]\n          [ "$binding_count" = 1 ]\n          [[ "$pr_number" =~ ^[1-9][0-9]*$ ]]\n          pr_record="$(gh api "repos/$REPOSITORY/pulls/$pr_number" --jq '[.state,.head.repo.full_name,.head.sha]|@tsv')"\n          IFS=$'\\t' read -r pr_state pr_head_repository pr_head_sha <<<"$pr_record"\n          [ "$pr_state" = open ]\n          [ "$pr_head_repository" = "$ADMITTED_HEAD_REPOSITORY" ]\n          [ "$pr_head_sha" = "$ADMITTED_HEAD_SHA" ]\n+"""


INPUTS = INPUTS.replace("\n+", "\n")
VERIFY_STEP = VERIFY_STEP.replace("\n+", "\n")


def verifier_step(condition: str | None = None) -> str:
    if condition is None:
        return VERIFY_STEP
    marker = "        env:\n"
    if VERIFY_STEP.count(marker) != 1:
        raise SystemExit("protected verifier env marker drifted")
    return VERIFY_STEP.replace(marker, f"        if: {condition}\n{marker}")


def replace_once(document: str, old: str, new: str) -> str:
    if document.count(old) != 1:
        raise SystemExit(
            f"protected node-ci generator expected one {old.splitlines()[0]!r} boundary, "
            f"found {document.count(old)}"
        )
    return document.replace(old, new)


def remove_candidate_credentials(document: str, step_name: str) -> str:
    step_marker = f"      - name: {step_name}\n"
    if document.count(step_marker) != 1:
        raise SystemExit(
            f"protected node-ci expected one candidate step {step_name!r}, "
            f"found {document.count(step_marker)}"
        )
    step_start = document.index(step_marker)
    step_end = document.find("\n      - ", step_start + len(step_marker))
    if step_end == -1:
        step_end = len(document)
    step = document[step_start:step_end]
    run_marker = "        run: |\n"
    if step.count(run_marker) != 1:
        raise SystemExit(f"protected candidate step {step_name!r} has no unique run block")
    unset = "          unset -v " + " ".join(CREDENTIAL_ENV_KEYS) + "\n"
    protected_step = step.replace(run_marker, run_marker + unset, 1)
    return document[:step_start] + protected_step + document[step_end:]


def move_step_before_guard(
    document: str,
    moving_name: str,
    before_name: str,
    guard_name: str,
) -> str:
    lines = document.splitlines(keepends=True)
    moving_marker = f"      - name: {moving_name}\n"
    before_marker = f"      - name: {before_name}\n"
    guard_marker = f"      - name: {guard_name}\n"
    moving_indexes = [
        index for index, line in enumerate(lines) if line == moving_marker
    ]
    before_indexes = [index for index, line in enumerate(lines) if line == before_marker]
    guard_indexes = [index for index, line in enumerate(lines) if line == guard_marker]
    target_guard_indexes = [
        index for index in guard_indexes if index < before_indexes[0]
    ] if len(before_indexes) == 1 else []
    if len(moving_indexes) != 1 or len(before_indexes) != 1 or not target_guard_indexes:
        raise SystemExit(
            f"protected node-ci step ordering boundary drifted: {moving_name!r}, "
            f"{guard_name!r}, {before_name!r}"
        )
    moving_start = moving_indexes[0]
    before_index = before_indexes[0]
    guard_index = max(target_guard_indexes)
    def is_step_start(line: str) -> bool:
        if len(line) - len(line.lstrip()) != 6:
            return False
        rest = line.lstrip()
        if not rest.startswith("- "):
            return False
        key = rest[2:]
        return any(key.startswith(f"{start_key}:") for start_key in STEP_START_KEYS)

    moving_end = next(
        (
            index
            for index in range(moving_start + 1, len(lines))
            if is_step_start(lines[index])
        ),
        None,
    )
    if moving_end is None:
        raise SystemExit(
            f"protected node-ci step ordering boundary drifted: no step marker "
            f"found after {moving_name!r} (before {before_name!r}, guard "
            f"{guard_name!r})"
        )
    moving_step = lines[moving_start:moving_end]
    del lines[moving_start:moving_end]
    if guard_index > moving_start:
        guard_index -= moving_end - moving_start
    lines[guard_index:guard_index] = moving_step
    return "".join(lines)


def configure_changelog_tool_cache(document: str) -> str:
    step_name = "Prepare job-scoped changelog tool cache"
    step_marker = f"      - name: {step_name}\n"
    step_start = document.index(step_marker)
    step_end = document.find("\n      - ", step_start + len(step_marker))
    if step_end == -1:
        raise SystemExit(f"protected changelog cache step {step_name!r} must not be last")
    step = document[step_start:step_end]
    workspace_cache_root = '$GITHUB_WORKSPACE/.verjson-changelog-tools.XXXXXX'
    runner_cache_root = '$RUNNER_TEMP/verjson-changelog-tools.XXXXXX'
    if step.count(workspace_cache_root) != 1:
        raise SystemExit("protected changelog cache root source drifted")
    step = step.replace(workspace_cache_root, runner_cache_root, 1)

    plan_if = (
        "needs.eligibility.outputs.should-run != 'false' && "
        "(inputs.secretless-pr || inputs.secretless-trusted-ref) && "
        "(inputs.secretless-ci-script-plan != '' || "
        "inputs.secretless-nested-manifests != '')"
    )
    warm_step = f"""      - name: Warm verified changelog contract cache
        id: warm-changelog-contract
        if: {plan_if}
        run: |
          set -euo pipefail
          python3 - <<'PY'
          import hashlib
          import os
          import re
          import stat
          import subprocess
          import sys
          import tempfile
          from pathlib import Path

          workspace = Path(os.environ.get("GITHUB_WORKSPACE", ""))
          workspace_fd = None
          scripts_fd = None
          renderer_fd = None
          try:
              if (
                  not workspace.is_absolute()
                  or workspace.resolve(strict=True) != workspace
              ):
                  raise ValueError("checkout path is not canonical")
              directory_flags = (
                  os.O_RDONLY
                  | os.O_DIRECTORY
                  | os.O_NOFOLLOW
                  | getattr(os, "O_CLOEXEC", 0)
              )
              workspace_metadata = workspace.stat(follow_symlinks=False)
              workspace_fd = os.open(workspace, directory_flags)
              opened_workspace_metadata = os.fstat(workspace_fd)
              if (
                  not stat.S_ISDIR(opened_workspace_metadata.st_mode)
                  or opened_workspace_metadata.st_dev != workspace_metadata.st_dev
                  or opened_workspace_metadata.st_ino != workspace_metadata.st_ino
                  or opened_workspace_metadata.st_uid != os.getuid()
              ):
                  raise ValueError("checkout directory changed during validation")
              scripts_fd = os.open("scripts", directory_flags, dir_fd=workspace_fd)
              scripts_metadata = os.fstat(scripts_fd)
              if (
                  not stat.S_ISDIR(scripts_metadata.st_mode)
                  or scripts_metadata.st_uid != os.getuid()
              ):
                  raise ValueError("checkout scripts directory has an unsafe shape")
              renderer_fd = os.open(
                  "render-next.sh",
                  os.O_RDONLY
                  | os.O_NOFOLLOW
                  | os.O_NONBLOCK
                  | getattr(os, "O_CLOEXEC", 0),
                  dir_fd=scripts_fd,
              )
              renderer_metadata = os.fstat(renderer_fd)
              max_renderer_bytes = 1024 * 1024
              if (
                  not stat.S_ISREG(renderer_metadata.st_mode)
                  or renderer_metadata.st_uid != os.getuid()
                  or renderer_metadata.st_nlink != 1
                  or renderer_metadata.st_size > max_renderer_bytes
              ):
                  raise ValueError("checkout renderer has an unsafe file shape")
              with os.fdopen(renderer_fd, "rb") as renderer_stream:
                  renderer_fd = None
                  renderer_bytes = renderer_stream.read(max_renderer_bytes + 1)
                  final_renderer_metadata = os.fstat(renderer_stream.fileno())
              if (
                  len(renderer_bytes) != renderer_metadata.st_size
                  or len(renderer_bytes) > max_renderer_bytes
                  or (
                      renderer_metadata.st_dev,
                      renderer_metadata.st_ino,
                      renderer_metadata.st_size,
                      renderer_metadata.st_mtime_ns,
                      renderer_metadata.st_ctime_ns,
                  )
                  != (
                      final_renderer_metadata.st_dev,
                      final_renderer_metadata.st_ino,
                      final_renderer_metadata.st_size,
                      final_renderer_metadata.st_mtime_ns,
                      final_renderer_metadata.st_ctime_ns,
                  )
              ):
                  raise ValueError("checkout renderer changed during validation")
              renderer = renderer_bytes.decode("utf-8")
          except (OSError, UnicodeDecodeError, ValueError):
              sys.exit("pinned changelog renderer is unavailable or unsafe")
          finally:
              for descriptor in (renderer_fd, scripts_fd, workspace_fd):
                  if descriptor is not None:
                      os.close(descriptor)

          def declaration(name, pattern):
              matches = re.findall(rf'(?m)^{{name}}="([^"\\n]*)"$', renderer)
              if len(matches) != 1 or re.fullmatch(pattern, matches[0]) is None:
                  sys.exit(f"pinned changelog renderer has an invalid {{name}} declaration")
              return matches[0]

          contract_ref = declaration("CONTRACT_REF", r"[0-9a-f]{{40}}")
          contract_sha256 = declaration("CONTRACT_SHA256", r"[0-9a-f]{{64}}")
          runner_temp = Path(os.environ.get("RUNNER_TEMP", ""))
          cache_root = Path(os.environ.get("VERJSON_CHANGELOG_TOOL_CACHE", ""))
          if (
              not runner_temp.is_absolute()
              or runner_temp.is_symlink()
              or not runner_temp.is_dir()
              or runner_temp.resolve() != runner_temp
              or not cache_root.is_absolute()
              or cache_root.is_symlink()
              or not cache_root.is_dir()
              or cache_root.resolve() != cache_root
              or cache_root.parent != runner_temp
          ):
              sys.exit("job-scoped changelog cache is not a canonical RUNNER_TEMP child")
          if any(cache_root.iterdir()):
              sys.exit("job-scoped changelog cache was not empty before warm-up")

          url = (
              "https://raw.githubusercontent.com/Verjson/.github/"
              f"{{contract_ref}}/scripts/changelog.py"
          )
          descriptor, temporary_name = tempfile.mkstemp(prefix=".changelog.", dir=cache_root)
          os.close(descriptor)
          temporary_path = Path(temporary_name)
          try:
              subprocess.run(
                  [
                      "curl",
                      "-fsSL",
                      "--proto",
                      "=https",
                      "--proto-redir",
                      "=https",
                      "--max-filesize",
                      "16777216",
                      "-o",
                      str(temporary_path),
                      url,
                  ],
                  check=True,
                  capture_output=True,
              )
          except (OSError, subprocess.CalledProcessError):
              temporary_path.unlink(missing_ok=True)
              sys.exit("cannot fetch the pinned changelog contract")
          try:
              temporary_metadata = temporary_path.stat(follow_symlinks=False)
              if (
                  not stat.S_ISREG(temporary_metadata.st_mode)
                  or temporary_metadata.st_uid != os.getuid()
                  or temporary_metadata.st_nlink != 1
                  or temporary_metadata.st_mode & 0o022
                  or temporary_metadata.st_size > 16777216
              ):
                  raise ValueError("fetched changelog contract has an unsafe file shape")
              contract_fd = os.open(
                  temporary_path,
                  os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
              )
              opened_metadata = os.fstat(contract_fd)
              if (
                  opened_metadata.st_dev != temporary_metadata.st_dev
                  or opened_metadata.st_ino != temporary_metadata.st_ino
                  or not stat.S_ISREG(opened_metadata.st_mode)
              ):
                  os.close(contract_fd)
                  raise ValueError("fetched changelog contract changed during validation")
              digest = hashlib.sha256()
              with os.fdopen(contract_fd, "rb") as contract_stream:
                  for chunk in iter(lambda: contract_stream.read(1024 * 1024), b""):
                      digest.update(chunk)
          except (OSError, ValueError):
              temporary_path.unlink(missing_ok=True)
              sys.exit("fetched changelog contract has an unsafe file shape")
          if digest.hexdigest() != contract_sha256:
              temporary_path.unlink(missing_ok=True)
              sys.exit("fetched changelog contract does not match its pinned SHA-256")

          contract_dir = cache_root / contract_ref
          try:
              contract_dir.mkdir(mode=0o700)
              temporary_path.chmod(0o400)
              os.rename(temporary_path, contract_dir / "changelog.py")
          except OSError:
              temporary_path.unlink(missing_ok=True)
              sys.exit("cannot publish the verified changelog contract")

          with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
              output.write(f"contract_ref={{contract_ref}}\\n")
              output.write(f"contract_sha256={{contract_sha256}}\\n")
          PY
"""
    return document[:step_start] + step + "\n" + warm_step.rstrip("\n") + document[step_end:]


def isolate_candidate_runtime_cache(document: str) -> str:
    step_name = "Run exact credentialless consumer script plan"
    plan_if = (
        "needs.eligibility.outputs.should-run != 'false' && "
        "(inputs.secretless-pr || inputs.secretless-trusted-ref) && "
        "(inputs.secretless-ci-script-plan != '' || "
        "inputs.secretless-nested-manifests != '')"
    )
    step_marker = f"      - name: {step_name}\n"
    step_start = document.index(step_marker)
    step_end = document.find("\n      - ", step_start + len(step_marker))
    if step_end == -1:
        raise SystemExit(f"protected candidate step {step_name!r} must not be last")
    step = document[step_start:step_end]
    plan_env_lines = [
        line
        for line in step.splitlines(keepends=True)
        if line.startswith("          CI_SCRIPT_PLAN:")
    ]
    if len(plan_env_lines) != 1:
        raise SystemExit(
            f"protected candidate script plan environment changed: {len(plan_env_lines)}"
        )
    plan_env = plan_env_lines[0]
    isolated_plan_env = plan_env + (
        "          CANDIDATE_CACHE_ROOT: "
        "${{ runner.temp }}/verjson-candidate-caches-${{ github.run_id }}-"
        "${{ github.run_attempt }}-${{ github.job }}\n"
        "          VERJSON_CHANGELOG_CONTRACT_REF: "
        "${{ steps.warm-changelog-contract.outputs.contract_ref }}\n"
        "          VERJSON_CHANGELOG_CONTRACT_SHA256: "
        "${{ steps.warm-changelog-contract.outputs.contract_sha256 }}\n"
    )
    step = step.replace(plan_env, isolated_plan_env, 1)
    imports = """          import json
          import os
          import re
          import shutil
          import subprocess
          import sys
          from pathlib import Path
"""
    protected_imports = """          import hashlib
          import json
          import os
          import re
          import shutil
          import signal
          import stat
          import subprocess
          import sys
          import time
          from pathlib import Path
"""
    if step.count(imports) != 1:
        raise SystemExit("protected candidate script plan imports changed")
    step = step.replace(imports, protected_imports, 1)
    execution = """          for directory, name, unset_env in normalized:
              script_env = os.environ.copy()
              for env_name in unset_env:
                  script_env.pop(env_name, None)
              npm_command = ["npm"]
              npm_path = shutil.which("npm")
              node_path = shutil.which("node")
              if npm_path is not None and node_path is not None:
                  npm_executable = Path(npm_path)
                  resolved_npm_executable = npm_executable.resolve()
                  npm_cli_candidates = list(dict.fromkeys(
                      candidate.resolve()
                      for candidate in (
                          npm_executable.parent.parent / "lib/node_modules/npm/bin/npm-cli.js",
                          npm_executable.parent / "node_modules/npm/bin/npm-cli.js",
                          resolved_npm_executable.parent.parent / "bin/npm-cli.js",
                      )
                      if candidate.is_file()
                  ))
                  if len(npm_cli_candidates) > 1:
                      raise SystemExit("trusted npm CLI is ambiguous")
                  if npm_cli_candidates:
                      npm_command = [node_path, str(npm_cli_candidates[0])]
              subprocess.run([*npm_command, "run", name], check=True, env=script_env, cwd=directory)
"""
    isolated_execution = f"""          max_cache_files = {CANDIDATE_CACHE_MAX_FILES}
          max_cache_bytes = {CANDIDATE_CACHE_MAX_BYTES}
          runner_temp_input = Path(os.environ["RUNNER_TEMP"])
          if (
              not runner_temp_input.is_absolute()
              or runner_temp_input.is_symlink()
              or not runner_temp_input.is_dir()
              or runner_temp_input.resolve() != runner_temp_input
          ):
              sys.exit("RUNNER_TEMP is not a canonical directory")
          runner_temp = runner_temp_input
          changelog_ref = os.environ.get("VERJSON_CHANGELOG_CONTRACT_REF", "")
          changelog_sha256 = os.environ.get("VERJSON_CHANGELOG_CONTRACT_SHA256", "")
          if (
              re.fullmatch(r"[0-9a-f]{{40}}", changelog_ref) is None
              or re.fullmatch(r"[0-9a-f]{{64}}", changelog_sha256) is None
          ):
              sys.exit("pinned changelog contract identity is malformed")
          changelog_cache_root = Path(os.environ.get("VERJSON_CHANGELOG_TOOL_CACHE", ""))
          if (
              not changelog_cache_root.is_absolute()
              or changelog_cache_root.is_symlink()
              or not changelog_cache_root.is_dir()
              or changelog_cache_root.resolve() != changelog_cache_root
              or changelog_cache_root.parent != runner_temp
          ):
              sys.exit("verified changelog cache is not a canonical RUNNER_TEMP child")
          changelog_cache_root_metadata = changelog_cache_root.stat(follow_symlinks=False)
          if (
              not stat.S_ISDIR(changelog_cache_root_metadata.st_mode)
              or changelog_cache_root_metadata.st_uid != os.getuid()
              or changelog_cache_root_metadata.st_mode & 0o022
          ):
              sys.exit("verified changelog cache root has unsafe ownership or mode")
          changelog_cache_entries = list(os.scandir(changelog_cache_root))
          if len(changelog_cache_entries) != 1 or changelog_cache_entries[0].name != changelog_ref:
              sys.exit("verified changelog cache contains unexpected entries")
          changelog_cache_dir = changelog_cache_root / changelog_ref
          changelog_cache_dir_metadata = changelog_cache_dir.stat(follow_symlinks=False)
          if (
              not stat.S_ISDIR(changelog_cache_dir_metadata.st_mode)
              or changelog_cache_dir_metadata.st_uid != os.getuid()
              or changelog_cache_dir_metadata.st_mode & 0o022
          ):
              sys.exit("verified changelog cache entry has unsafe ownership or mode")
          changelog_cache_files = list(os.scandir(changelog_cache_dir))
          if len(changelog_cache_files) != 1 or changelog_cache_files[0].name != "changelog.py":
              sys.exit("verified changelog cache entry is incomplete")
          changelog_contract_path = changelog_cache_dir / "changelog.py"
          changelog_contract_metadata = changelog_contract_path.stat(follow_symlinks=False)
          if (
              not stat.S_ISREG(changelog_contract_metadata.st_mode)
              or changelog_contract_metadata.st_uid != os.getuid()
              or changelog_contract_metadata.st_nlink != 1
              or changelog_contract_metadata.st_mode & 0o222
              or changelog_contract_metadata.st_size > 16777216
          ):
              sys.exit("verified changelog contract file has unsafe ownership or mode")
          try:
              changelog_contract_fd = os.open(
                  changelog_contract_path,
                  os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
              )
          except OSError:
              sys.exit("verified changelog contract changed during validation")
          opened_contract_metadata = os.fstat(changelog_contract_fd)
          if (
              opened_contract_metadata.st_dev != changelog_contract_metadata.st_dev
              or opened_contract_metadata.st_ino != changelog_contract_metadata.st_ino
              or opened_contract_metadata.st_uid != changelog_contract_metadata.st_uid
              or opened_contract_metadata.st_size != changelog_contract_metadata.st_size
          ):
              os.close(changelog_contract_fd)
              sys.exit("verified changelog contract changed during validation")
          changelog_digest = hashlib.sha256()
          with os.fdopen(changelog_contract_fd, "rb") as changelog_stream:
              for chunk in iter(lambda: changelog_stream.read(1024 * 1024), b""):
                  changelog_digest.update(chunk)
          if changelog_digest.hexdigest() != changelog_sha256:
              sys.exit("verified changelog contract does not match its pinned SHA-256")
          changelog_cache_root_identity = (
              changelog_cache_root_metadata.st_dev,
              changelog_cache_root_metadata.st_ino,
              changelog_cache_root_metadata.st_uid,
              stat.S_IMODE(changelog_cache_root_metadata.st_mode),
          )
          baseline_value = os.environ.get("npm_config_cache", "").strip()
          baseline = None
          if baseline_value:
              candidate_baseline = Path(os.path.abspath(baseline_value))
              if candidate_baseline.exists() or candidate_baseline.is_symlink():
                baseline = candidate_baseline
                if not baseline.is_absolute() or baseline.is_symlink() or not baseline.is_dir():
                  sys.exit("verified runtime cache is not an absolute regular directory")
                if baseline.resolve() != baseline:
                  sys.exit("verified runtime cache path contains a symlink")
                try:
                  baseline.resolve().relative_to(runner_temp)
                except ValueError:
                  sys.exit("verified runtime cache escapes RUNNER_TEMP")

          bubblewrap = Path("/usr/bin/bwrap")
          try:
              bubblewrap_metadata = bubblewrap.stat(follow_symlinks=False)
          except OSError:
              sys.exit("verified bubblewrap namespace boundary is unavailable")
          if (
              not stat.S_ISREG(bubblewrap_metadata.st_mode)
              or bubblewrap_metadata.st_uid != 0
              or bubblewrap_metadata.st_mode & 0o022
          ):
              sys.exit("bubblewrap namespace boundary has unsafe ownership or mode")
          bubblewrap_version = subprocess.run(
              [str(bubblewrap), "--version"],
              check=True,
              capture_output=True,
              text=True,
          ).stdout
          version_match = re.fullmatch(r"bubblewrap (\\d+)\\.(\\d+)\\.(\\d+)\\n?", bubblewrap_version)
          if version_match is None or tuple(map(int, version_match.groups())) < (0, 9, 0):
              sys.exit("bubblewrap namespace boundary is below version 0.9.0")

          def inventory(root):
              files = []
              total_bytes = 0
              pending = [root]
              while pending:
                  directory = pending.pop()
                  for entry in os.scandir(directory):
                      entry_path = Path(entry.path)
                      mode = entry.stat(follow_symlinks=False).st_mode
                      if stat.S_ISLNK(mode):
                          sys.exit("verified runtime cache contains a symlink")
                      if stat.S_ISDIR(mode):
                          pending.append(entry_path)
                          continue
                      if not stat.S_ISREG(mode):
                          sys.exit("verified runtime cache contains a special file")
                      try:
                          relative = entry_path.relative_to(root).as_posix()
                      except ValueError:
                          sys.exit("verified runtime cache entry escapes its root")
                      try:
                          descriptor = os.open(entry_path, os.O_RDONLY | os.O_NOFOLLOW)
                      except OSError:
                          sys.exit("verified runtime cache file changed during validation")
                      descriptor_stat = os.fstat(descriptor)
                      if not stat.S_ISREG(descriptor_stat.st_mode):
                          os.close(descriptor)
                          sys.exit("verified runtime cache file changed type during validation")
                      size = descriptor_stat.st_size
                      total_bytes += size
                      if len(files) + 1 > max_cache_files or total_bytes > max_cache_bytes:
                          sys.exit("verified runtime cache exceeds its file or byte bound")
                      digest = hashlib.sha256()
                      with os.fdopen(descriptor, "rb") as stream:
                          for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                              digest.update(chunk)
                      files.append((relative, size, digest.hexdigest()))
              return tuple(sorted(files))

          baseline_inventory = inventory(baseline) if baseline is not None else ()
          cache_root = Path(os.environ["CANDIDATE_CACHE_ROOT"])
          if (
              not cache_root.is_absolute()
              or Path(os.path.abspath(cache_root)) != cache_root
              or cache_root.parent.resolve() != runner_temp
          ):
              sys.exit("candidate cache root escapes RUNNER_TEMP")
          if cache_root.exists() or cache_root.is_symlink():
              sys.exit("candidate cache root exists before script execution")
          workspace_lexical = Path(os.environ.get("PWD", ""))
          workspace = Path.cwd().resolve()
          if (
            not workspace_lexical.is_absolute()
            or workspace_lexical != workspace
            or workspace_lexical.resolve() != workspace_lexical
            or workspace_lexical.is_symlink()
            or not workspace.is_dir()
          ):
            sys.exit("candidate workspace is not a canonical directory")
          if workspace == runner_temp or workspace in runner_temp.parents or runner_temp in workspace.parents:
              sys.exit("candidate workspace and RUNNER_TEMP overlap")
          if (baseline is not None and baseline.parent != runner_temp) or cache_root.parent != runner_temp:
              sys.exit("candidate cache roots are not exact RUNNER_TEMP children")
          if baseline is not None and (baseline == cache_root or baseline in cache_root.parents or cache_root in baseline.parents):
              sys.exit("candidate cache baseline and isolation root overlap")
          if any(os.environ.get(name) for name in ("DB_HOST", "DB_PORT", "CACHE_PORT")):
              sys.exit("protected candidate scripts do not permit shared service networking")

          trusted_tool_root_input = Path(os.environ.get("RUNNER_TOOL_CACHE", ""))
          if (
              not trusted_tool_root_input.is_absolute()
              or trusted_tool_root_input.is_symlink()
              or not trusted_tool_root_input.is_dir()
              or trusted_tool_root_input.resolve() != trusted_tool_root_input
          ):
              sys.exit("trusted setup-node tool root unavailable or noncanonical")
          trusted_tool_root = trusted_tool_root_input
          hosted_tool_cache_root = Path("/opt/hostedtoolcache")
          # uid 0 is root; uid 1001 is the "runner" account GitHub-hosted
          # images provision and run Actions steps as.
          hosted_tool_cache_uids = (0, 1001)

          def paths_overlap(left, right):
            return left == right or left in right.parents or right in left.parents

          if paths_overlap(changelog_cache_root, cache_root) or (
              baseline is not None and paths_overlap(changelog_cache_root, baseline)
          ):
              sys.exit("verified changelog cache overlaps a candidate cache")

          def validate_trusted_ancestry(
            root,
            target,
            allowed_uids,
            label,
            include_target=True,
            require_unwritable=True,
          ):
            try:
              relative = target.relative_to(root)
            except ValueError:
              sys.exit(f"{{label}} escapes its trusted root")
            components = (root,)
            current = root
            for part in relative.parts:
              current = current / part
              components += (current,)
            if not include_target:
              components = components[:-1]
            for component in components:
              try:
                metadata = component.stat(follow_symlinks=False)
              except OSError:
                sys.exit(f"{{label}} ancestry is unreadable")
              if component != target or not include_target:
                if not stat.S_ISDIR(metadata.st_mode):
                  sys.exit(f"{{label}} ancestry is not canonical directories")
              if metadata.st_uid not in allowed_uids or (
                require_unwritable and metadata.st_mode & 0o022
              ):
                sys.exit(f"{{label}} ancestry has unsafe ownership mode")

          candidate_controlled_roots = tuple(
              root for root in (workspace, runner_temp, baseline, cache_root, changelog_cache_root)
              if root is not None
          )
          if any(paths_overlap(trusted_tool_root, path) for path in candidate_controlled_roots):
              sys.exit("trusted setup-node tool root overlaps candidate-controlled paths")
          trusted_root_metadata = trusted_tool_root.stat(follow_symlinks=False)
          allow_hosted_tool_cache_root = (
            trusted_tool_root == hosted_tool_cache_root
            and stat.S_IMODE(trusted_root_metadata.st_mode) == 0o777
            and trusted_root_metadata.st_uid in hosted_tool_cache_uids
            and trusted_root_metadata.st_gid == 0
          )
          if (
            not stat.S_ISDIR(trusted_root_metadata.st_mode)
            or trusted_root_metadata.st_uid not in (
              hosted_tool_cache_uids if allow_hosted_tool_cache_root else (0,)
            )
            or (
              trusted_root_metadata.st_mode & 0o022
              and not allow_hosted_tool_cache_root
            )
          ):
            sys.exit("trusted setup-node tool root has unsafe ownership mode")
          trusted_tool_uids = hosted_tool_cache_uids if allow_hosted_tool_cache_root else (0,)
          # GitHub-hosted images provision every entry under /opt/hostedtoolcache
          # (directories, files, and symlinks alike) as uid 1001 mode 0777 -- the
          # same convention as the root, measured on ubuntu-latest for #1599 -- so
          # an unwritable requirement on any descendant can never pass there. The
          # trust anchor for that tree is the owner allowlist plus the fact that
          # nothing PR-controlled runs before this check (npm ci uses
          # --ignore-scripts): the write bit is unexploited capability, not
          # evidence of tampering. Every other tool root stays strict.
          trusted_tool_tree_requires_unwritable = not allow_hosted_tool_cache_root

          trusted_search_directories = []
          for entry in os.environ.get("PATH", "").split(os.pathsep):
            if not entry:
              continue
            lexical_directory = Path(entry)
            if not lexical_directory.is_absolute():
              continue
            try:
              lexical_directory.relative_to(trusted_tool_root)
            except ValueError:
              continue
            resolved_directory = lexical_directory.resolve()
            try:
              resolved_directory.relative_to(trusted_tool_root)
            except ValueError:
              sys.exit("setup-node PATH entry escapes trusted tool root")
            validate_trusted_ancestry(
              trusted_tool_root,
              lexical_directory,
              trusted_tool_uids,
              "setup-node lexical PATH",
              require_unwritable=trusted_tool_tree_requires_unwritable,
            )
            validate_trusted_ancestry(
              trusted_tool_root,
              resolved_directory,
              trusted_tool_uids,
              "setup-node resolved PATH",
              require_unwritable=trusted_tool_tree_requires_unwritable,
            )
            if resolved_directory not in trusted_search_directories:
              trusted_search_directories.append(resolved_directory)

          tool_executables = {{}}
          for tool_name in ("npm", "node", "pwsh"):
            candidates = []
            if tool_name == "pwsh":
              system_candidate = Path("/usr/bin/pwsh")
              if system_candidate.exists():
                resolved_system_candidate = system_candidate.resolve()
                microsoft_root = Path("/opt/microsoft/powershell")
                if (
                  not resolved_system_candidate.is_relative_to(microsoft_root)
                  or resolved_system_candidate.name != "pwsh"
                  or not re.fullmatch(r"[0-9]+(?:[.][0-9]+)*", resolved_system_candidate.parent.name)
                ):
                  sys.exit("trusted pwsh executable escapes Microsoft runtime contract")
                validate_trusted_ancestry(
                  Path("/usr"), system_candidate, (0,), "pwsh lexical path", include_target=False
                )
                validate_trusted_ancestry(
                  # GitHub-hosted runner images ship /opt root-owned but world-writable
                  # (mode 777, matching /opt/hostedtoolcache's convention), so an
                  # unwritable-ancestry requirement can never pass there. Root ownership
                  # alone still holds: nothing PR-controlled runs outside this sandbox
                  # before this check (npm ci uses --ignore-scripts), so the write bit is
                  # unexploited capability, not evidence of tampering.
                  Path("/opt"),
                  resolved_system_candidate.parent,
                  (0,),
                  "pwsh resolved runtime",
                  require_unwritable=False,
                )
                system_metadata = resolved_system_candidate.stat(follow_symlinks=False)
                if not stat.S_ISREG(system_metadata.st_mode) or system_metadata.st_uid != 0:
                  sys.exit("trusted pwsh executable has unsafe ownership mode")
                candidates.append(resolved_system_candidate)
            for directory in trusted_search_directories:
              lexical_candidate = directory / tool_name
              if not lexical_candidate.exists():
                continue
              resolved_candidate = lexical_candidate.resolve()
              validate_trusted_ancestry(
                trusted_tool_root,
                lexical_candidate,
                trusted_tool_uids,
                f"trusted {{tool_name}} lexical path",
                include_target=False,
                require_unwritable=trusted_tool_tree_requires_unwritable,
              )
              try:
                resolved_candidate.relative_to(trusted_tool_root)
              except ValueError:
                sys.exit(f"trusted {{tool_name}} executable escapes setup-node tool root")
              validate_trusted_ancestry(
                trusted_tool_root,
                resolved_candidate,
                trusted_tool_uids,
                f"trusted {{tool_name}} resolved path",
                require_unwritable=trusted_tool_tree_requires_unwritable,
              )
              if resolved_candidate.is_file() and os.access(resolved_candidate, os.X_OK):
                candidates.append(resolved_candidate)
            candidates = list(dict.fromkeys(candidates))
            if not candidates:
              if tool_name in ("npm", "node"):
                sys.exit(f"trusted {{tool_name}} executable is unavailable")
              continue
            if len(candidates) != 1:
              sys.exit(f"trusted {{tool_name}} executable is ambiguous")
            tool_executable = candidates[0]
            executable_metadata = tool_executable.stat(follow_symlinks=False)
            # The Microsoft-shipped pwsh binary under GitHub-hosted /opt is root-owned
            # but world-writable (mode 777, matching /opt/hostedtoolcache's convention),
            # so the write-bit requirement below can never pass against a real,
            # untampered system pwsh there. Root ownership alone still holds for that
            # one location: nothing PR-controlled runs outside this sandbox before this
            # check (npm ci uses --ignore-scripts), so the write bit is unexploited
            # capability, not evidence of tampering. npm/node under an admitted hosted
            # tool cache carry the same image convention (see
            # trusted_tool_tree_requires_unwritable); every other root stays strict.
            executable_in_hosted_tool_cache = tool_executable.is_relative_to(hosted_tool_cache_root)
            executable_requires_unwritable = not (
              (tool_name == "pwsh" and tool_executable.is_relative_to("/opt/microsoft/powershell"))
              or (executable_in_hosted_tool_cache and not trusted_tool_tree_requires_unwritable)
            )
            executable_uids = trusted_tool_uids if executable_in_hosted_tool_cache else (0,)
            if executable_metadata.st_uid not in executable_uids or (
              executable_requires_unwritable and executable_metadata.st_mode & 0o022
            ):
              sys.exit(f"trusted {{tool_name}} executable has unsafe ownership mode")
            tool_executables[tool_name] = tool_executable
          npm_executable = tool_executables["npm"]
          tool_prefix_candidates = {{
            (executable.parent if executable.is_relative_to("/opt/microsoft/powershell") else executable.parent.parent)
            for executable in tool_executables.values()
            if not executable.is_relative_to("/usr")
          }}
          tool_prefixes = []
          tool_prefix_identities = {{}}

          def validate_trusted_tool_tree(root, allowed_uids=(0,), require_unwritable=True):
            for directory, directory_names, file_names in os.walk(root, followlinks=False):
              directory_path = Path(directory)
              directory_metadata = directory_path.stat(follow_symlinks=False)
              if (
                not stat.S_ISDIR(directory_metadata.st_mode)
                or directory_metadata.st_uid not in allowed_uids
                or (require_unwritable and directory_metadata.st_mode & 0o022)
              ):
                sys.exit("trusted tool tree directory has unsafe ownership mode")
              for name in (*directory_names, *file_names):
                entry = directory_path / name
                entry_metadata = entry.stat(follow_symlinks=False)
                if entry_metadata.st_uid not in allowed_uids:
                  sys.exit("trusted tool tree entry has unapproved ownership")
                if stat.S_ISLNK(entry_metadata.st_mode):
                  resolved_entry = entry.resolve()
                  if resolved_entry.is_relative_to(root):
                    continue
                  if not resolved_entry.exists():
                    # The Microsoft-shipped pwsh tree ships legacy OpenSSL 1.0
                    # compatibility shims that name host library paths distros
                    # dropped long ago (e.g. Ubuntu 24.04 carries no OpenSSL 1.0);
                    # a dangling symlink cannot be read or executed by anything
                    # that mounts this tree, so it carries no trust to verify.
                    continue
                  # The Microsoft-shipped pwsh tree symlinks its bundled OpenSSL 1.0
                  # compatibility shims to the real system libraries under /usr on
                  # distros that still carry them; re-validate the escape target
                  # against /usr's own strict, unconditional trust instead of
                  # blanket-rejecting every symlink that leaves the tool prefix.
                  if resolved_entry.is_relative_to("/usr"):
                    validate_trusted_ancestry(
                      Path("/usr"),
                      resolved_entry,
                      (0,),
                      "trusted tool tree external symlink target",
                    )
                    continue
                  sys.exit("trusted tool tree symlink escapes mounted prefix")
                if require_unwritable and entry_metadata.st_mode & 0o022:
                  sys.exit("trusted tool tree entry has unsafe writable mode")
                if not (stat.S_ISDIR(entry_metadata.st_mode) or stat.S_ISREG(entry_metadata.st_mode)):
                  sys.exit("trusted tool tree contains special file")

          for tool_prefix in sorted(tool_prefix_candidates, key=lambda path: (len(path.parts), str(path))):
            if (
                tool_prefix == Path("/")
                or any(paths_overlap(tool_prefix, path) for path in candidate_controlled_roots)
            ):
              sys.exit("trusted tool prefix overlaps candidate-controlled paths")
            if tool_prefix.is_relative_to(trusted_tool_root):
              validate_trusted_ancestry(
                trusted_tool_root,
                tool_prefix,
                trusted_tool_uids,
                "trusted tool prefix",
                require_unwritable=trusted_tool_tree_requires_unwritable,
              )
            prefix_metadata = tool_prefix.stat(follow_symlinks=False)
            if not stat.S_ISDIR(prefix_metadata.st_mode):
              sys.exit("trusted tool prefix is not a directory")
            # See the pwsh discovery comment above: the Microsoft-shipped runtime tree
            # under GitHub-hosted /opt is root-owned but world-writable throughout, and
            # the admitted hosted tool cache is world-writable throughout by the same
            # image convention; only those prefixes relax the write-bit requirement.
            prefix_in_hosted_tool_cache = tool_prefix.is_relative_to(hosted_tool_cache_root)
            validate_trusted_tool_tree(
              tool_prefix,
              allowed_uids=trusted_tool_uids if prefix_in_hosted_tool_cache else (0,),
              require_unwritable=not (
                tool_prefix.is_relative_to("/opt/microsoft/powershell")
                or (prefix_in_hosted_tool_cache and not trusted_tool_tree_requires_unwritable)
              ),
            )
            tool_prefix_identities[tool_prefix] = (
              prefix_metadata.st_dev,
              prefix_metadata.st_ino,
              prefix_metadata.st_uid,
              stat.S_IMODE(prefix_metadata.st_mode),
            )
            if not any(existing == tool_prefix or existing in tool_prefix.parents for existing in tool_prefixes):
              tool_prefixes.append(tool_prefix)
          npm_cli_candidates = set()
          for npm_cli_candidate in (
            npm_executable.parent.parent / "lib/node_modules/npm/bin/npm-cli.js",
            npm_executable.parent / "node_modules/npm/bin/npm-cli.js",
            npm_executable.resolve().parent.parent / "bin/npm-cli.js",
          ):
            if not npm_cli_candidate.is_file():
              continue
            if not any(npm_cli_candidate.is_relative_to(prefix) for prefix in tool_prefixes):
              sys.exit("trusted npm CLI is outside validated tool prefixes")
            resolved_npm_cli_candidate = npm_cli_candidate.resolve(strict=True)
            if not any(resolved_npm_cli_candidate.is_relative_to(prefix) for prefix in tool_prefixes):
              sys.exit("trusted npm CLI escapes validated tool prefixes")
            npm_cli_candidates.add(resolved_npm_cli_candidate)
          if len(npm_cli_candidates) > 1:
              sys.exit("trusted npm CLI is ambiguous")
          npm_cli_executable = next(iter(npm_cli_candidates), None)
          npm_command = (
            [str(tool_executables["node"]), str(npm_cli_executable)]
            if npm_cli_executable is not None
            else [str(npm_executable)]
          )
          cache_root.mkdir(mode=0o700)
          active_process = None
          received_signal = None

          def cleanup_cache_root(*_args):
              global active_process
              if active_process is not None and active_process.poll() is None:
                  try:
                      os.killpg(active_process.pid, signal.SIGTERM)
                  except ProcessLookupError:
                      pass
              shutil.rmtree(cache_root, ignore_errors=True)

          def extinguish_process_group(process):
              graceful_deadline = time.monotonic() + 1
              while time.monotonic() < graceful_deadline:
                  try:
                      os.killpg(process.pid, 0)
                  except ProcessLookupError:
                      return
                  time.sleep(0.05)
              try:
                  os.killpg(process.pid, signal.SIGKILL)
              except ProcessLookupError:
                  return
              deadline = time.monotonic() + 5
              while True:
                  try:
                      os.killpg(process.pid, 0)
                  except ProcessLookupError:
                      return
                  if time.monotonic() >= deadline:
                      sys.exit("candidate process group survived bounded SIGKILL cleanup")
                  time.sleep(0.05)

          previous_handlers = {{}}
          def handle_signal(signum, _frame):
              global received_signal
              signal.signal(signal.SIGINT, signal.SIG_IGN)
              signal.signal(signal.SIGTERM, signal.SIG_IGN)
              received_signal = signum
              if active_process is not None and active_process.poll() is None:
                  try:
                      os.killpg(active_process.pid, signal.SIGTERM)
                  except ProcessLookupError:
                      pass

          for caught_signal in (signal.SIGINT, signal.SIGTERM):
              previous_handlers[caught_signal] = signal.getsignal(caught_signal)
              signal.signal(caught_signal, handle_signal)
          try:
              for index, (script_directory, name, unset_env) in enumerate(normalized):
                  if baseline is not None and inventory(baseline) != baseline_inventory:
                      sys.exit("verified runtime cache changed before candidate script")
                  script_cache = cache_root / str(index)
                  script_cache.mkdir(mode=0o700)
                  script_tmp = cache_root / f"tmp-{{index}}"
                  script_home = cache_root / f"home-{{index}}"
                  script_tmp.mkdir(mode=0o700)
                  script_home.mkdir(mode=0o700)
                  if baseline is not None:
                      for relative, _size, _digest in baseline_inventory:
                          source = baseline / relative
                          target = script_cache / relative
                          target.parent.mkdir(parents=True, exist_ok=True)
                          try:
                              source_descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
                          except OSError:
                              sys.exit("verified runtime cache changed during isolated copy")
                          if not stat.S_ISREG(os.fstat(source_descriptor).st_mode):
                              os.close(source_descriptor)
                              sys.exit("verified runtime cache copy source is not a regular file")
                          with (
                              os.fdopen(source_descriptor, "rb") as source_stream,
                              target.open("xb") as target_stream,
                          ):
                              shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
                  if (baseline is not None and inventory(baseline) != baseline_inventory) or (
                      inventory(script_cache) != baseline_inventory
                  ):
                      sys.exit("isolated candidate cache copy failed integrity verification")
                  script_env = os.environ.copy()
                  for env_name in unset_env:
                      script_env.pop(env_name, None)
                  script_env["NPM_CONFIG_CACHE"] = str(script_cache)
                  script_env["npm_config_cache"] = str(script_cache)
                  script_env["HOME"] = str(script_home)
                  script_env["TMPDIR"] = str(script_tmp)
                  tool_path_entries = [
                      *(str(executable.parent) for executable in tool_executables.values()),
                      "/usr/local/bin",
                      "/usr/bin",
                      "/bin",
                  ]
                  script_env["PATH"] = ":".join(dict.fromkeys(tool_path_entries))
                  isolated_paths = (script_cache, script_tmp, script_home)
                  if len(set(isolated_paths)) != len(isolated_paths):
                      sys.exit("candidate writable mount paths are not distinct")
                  if any(
                      left == right or left in right.parents or right in left.parents
                      for position, left in enumerate(isolated_paths)
                      for right in isolated_paths[position + 1:]
                  ):
                      sys.exit("candidate writable mount paths overlap")
                  mount_targets = (
                      workspace,
                      cache_root,
                      changelog_cache_root,
                      *isolated_paths,
                      *tool_prefixes,
                  )
                  namespace_directories = set()
                  for target in mount_targets:
                      namespace_directories.add(target)
                      namespace_directories.update(target.parents)
                  namespace_directories.discard(Path("/"))
                  directory_args = []
                  for directory in sorted(namespace_directories, key=lambda path: (len(path.parts), str(path))):
                      if directory == Path("/usr") or directory.is_relative_to("/usr"):
                          continue
                      directory_args.extend(("--dir", str(directory)))
                  protected_parents = {{workspace.parent, runner_temp, cache_root}}
                  chmod_args = []
                  for directory in sorted(protected_parents, key=str):
                      chmod_args.extend(("--chmod", "0555", str(directory)))
              tool_mount_args = []
              tool_prefix_fds = []
              readonly_mount_identities = dict(tool_prefix_identities)
              readonly_mount_identities[changelog_cache_root] = changelog_cache_root_identity
              try:
                for tool_prefix in (*tool_prefixes, changelog_cache_root):
                  descriptor = os.open(
                    tool_prefix,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                  )
                  descriptor_metadata = os.fstat(descriptor)
                  descriptor_identity = (
                    descriptor_metadata.st_dev,
                    descriptor_metadata.st_ino,
                    descriptor_metadata.st_uid,
                    stat.S_IMODE(descriptor_metadata.st_mode),
                  )
                  if descriptor_identity != readonly_mount_identities[tool_prefix]:
                    os.close(descriptor)
                    sys.exit("verified read-only mount changed before namespace bind")
                  tool_prefix_fds.append(descriptor)
                  tool_mount_args.extend(
                    ("--ro-bind", f"/proc/self/fd/{{descriptor}}", str(tool_prefix))
                  )
              except BaseException:
                for descriptor in tool_prefix_fds:
                  os.close(descriptor)
                raise
              try:
                active_process = subprocess.Popen(
                          [
                              str(bubblewrap),
                              "--unshare-user",
                              "--unshare-pid",
                              "--unshare-ipc",
                              "--unshare-uts",
                              "--unshare-cgroup-try",
                              "--unshare-net",
                              "--disable-userns",
                              "--die-with-parent",
                              "--new-session",
                              "--cap-drop", "ALL",
                              "--tmpfs", "/",
                              "--tmpfs", "/tmp",
                              *directory_args,
                              *chmod_args,
                              "--ro-bind", "/usr", "/usr",
                              "--ro-bind", "/bin", "/bin",
                              "--ro-bind", "/lib", "/lib",
                              "--ro-bind", "/lib64", "/lib64",
                              "--ro-bind", "/etc/ssl", "/etc/ssl",
                              "--ro-bind", "/etc/hosts", "/etc/hosts",
                              "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
                              "--ro-bind", "/etc/nsswitch.conf", "/etc/nsswitch.conf",
                              "--ro-bind", "/etc/passwd", "/etc/passwd",
                              "--ro-bind", "/etc/group", "/etc/group",
                              *tool_mount_args,
                              "--bind", str(workspace), str(workspace),
                              "--bind", str(script_cache), str(script_cache),
                              "--bind", str(script_home), str(script_home),
                              "--bind", str(script_tmp), str(script_tmp),
                              "--proc", "/proc",
                              "--dev", "/dev",
                              "--chdir", str(script_directory),
                              "--",
                              *npm_command, "run", name,
                          ],
                          env=script_env,
                          start_new_session=True,
                          pass_fds=tuple(tool_prefix_fds),
                        )
                for descriptor in tool_prefix_fds:
                  os.close(descriptor)
                tool_prefix_fds = []
                while active_process.poll() is None:
                  time.sleep(0.05)
                candidate_status = active_process.returncode
                extinguish_process_group(active_process)
                if received_signal is not None:
                  raise SystemExit(128 + received_signal)
                if candidate_status != 0:
                  raise subprocess.CalledProcessError(candidate_status, active_process.args)
              finally:
                for descriptor in tool_prefix_fds:
                  os.close(descriptor)
                if active_process is not None:
                  extinguish_process_group(active_process)
                active_process = None
                shutil.rmtree(script_cache, ignore_errors=True)
                shutil.rmtree(script_tmp, ignore_errors=True)
                shutil.rmtree(script_home, ignore_errors=True)
                if (
                  script_cache.exists()
                  or script_tmp.exists()
                  or script_home.exists()
                  or (baseline is not None and inventory(baseline) != baseline_inventory)
                ):
                  sys.exit("candidate script cache cleanup or baseline integrity check failed")
          finally:
              cleanup_cache_root()
              for caught_signal, previous_handler in previous_handlers.items():
                  signal.signal(caught_signal, previous_handler)
          if cache_root.exists() or (baseline is not None and inventory(baseline) != baseline_inventory):
              sys.exit("candidate cache root cleanup or final baseline integrity check failed")
"""
    if step.count(execution) != 1:
        raise SystemExit("protected candidate script execution block changed")
    step = step.replace(execution, isolated_execution, 1)
    python_start = "          python3 - <<'PY'\n"
    python_end = "          PY"
    if step.count(python_start) != 1 or step.count(python_end) != 1:
        raise SystemExit("protected candidate script Python boundary changed")
    supervised_start = """          candidate_cache_supervisor_pid=''
          # shellcheck disable=SC2329 # invoked indirectly by the INT/TERM traps below.
          forward_candidate_signal() {
            signal="$1"
            trap - INT TERM
            [ -z "$candidate_cache_supervisor_pid" ] || kill -s "$signal" "$candidate_cache_supervisor_pid" 2>/dev/null || true
            [ -z "$candidate_cache_supervisor_pid" ] || wait "$candidate_cache_supervisor_pid" 2>/dev/null || true
            exit "$2"
          }
          trap 'forward_candidate_signal INT 130' INT
          trap 'forward_candidate_signal TERM 143' TERM
          python3 - <<'PY' &
"""
    supervised_end = '''          PY
          candidate_cache_supervisor_pid=$!
          candidate_cache_status=0
          wait "$candidate_cache_supervisor_pid" || candidate_cache_status=$?
          trap - INT TERM
          exit "$candidate_cache_status"
'''
    step = step.replace(python_start, supervised_start, 1)
    step = step.replace(python_end, supervised_end, 1)
    cleanup_step = f"""
      - name: Remove isolated candidate runtime caches
        if: always() && {plan_if}
        env:
          CANDIDATE_CACHE_ROOT: ${{{{ runner.temp }}}}/verjson-candidate-caches-${{{{ github.run_id }}}}-${{{{ github.run_attempt }}}}-${{{{ github.job }}}}
        run: |
          set -euo pipefail
          expected="$RUNNER_TEMP/verjson-candidate-caches-${{{{ github.run_id }}}}-${{{{ github.run_attempt }}}}-${{{{ github.job }}}}"
          [ "$CANDIDATE_CACHE_ROOT" = "$expected" ]
          case "$CANDIDATE_CACHE_ROOT" in
            "$RUNNER_TEMP"/verjson-candidate-caches-*) ;;
            *) exit 1 ;;
          esac
          rm -rf -- "$CANDIDATE_CACHE_ROOT"
          [ ! -e "$CANDIDATE_CACHE_ROOT" ] && [ ! -L "$CANDIDATE_CACHE_ROOT" ]
"""
    return document[:step_start] + step + cleanup_step + document[step_end:]


def remove_step(document: str, step_name: str) -> str:
    step_marker = f"      - name: {step_name}\n"
    if document.count(step_marker) != 1:
        raise SystemExit(
            f"protected node-ci expected one unsupported step {step_name!r}, "
            f"found {document.count(step_marker)}"
        )
    step_start = document.index(step_marker)
    step_end = document.find("\n      - ", step_start + len(step_marker))
    if step_end == -1:
        raise SystemExit(f"protected unsupported step {step_name!r} must not be last")
    return document[:step_start] + document[step_end + 1:]


def insert_protected_baseline_step(document: str) -> str:
    acquisition_start = document.index("  acquire-secretless-dependencies:\n")
    checkout_marker = "      - uses: actions/checkout@"
    checkout_start = document.index(checkout_marker, acquisition_start)
    return document[:checkout_start] + PROTECTED_BASELINE_STEP + document[checkout_start:]


def configure_protected_baseline(document: str) -> str:
    def insert_after_signature(source: str, signature: str, body: str) -> str:
        lines = source.splitlines(keepends=True)
        matches = [index for index, line in enumerate(lines) if line.strip() == signature]
        if len(matches) != 1:
            raise SystemExit(f"protected node-ci expected one {signature!r} boundary")
        index = matches[0]
        indentation = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
        body_lines = [
            f"{indentation}    {line}\n" for line in body.splitlines()
        ]
        lines[index + 1 : index + 1] = body_lines
        return "".join(lines)

    document = insert_after_signature(
        document,
        "def is_bounded_range(value):",
        'if os.environ.get("ALLOW_PRERELEASE") == "true" and re.fullmatch('
        'rf"{core_pattern}-(?:[0-9A-Za-z-]+(?:\\.[0-9A-Za-z-]+)*)", value):\n'
        "  return True",
    )
    document = insert_after_signature(
        document,
        "def satisfies_bounded_range(version, range_value):",
        'if (\n'
        'os.environ.get("ALLOW_PRERELEASE") == "true"\n'
        "and \"-\" in range_value\n"
        "and version == range_value\n"
        "and version_pattern.fullmatch(version)\n"
        "):\n"
        "  return True",
    )
    acquisition_end = document.index("  build-test:\n")
    acquisition = document[:acquisition_end]
    remainder = document[acquisition_end:]
    dynamic_request = (
        "COMPATIBILITY_RANGES: ${{ steps.resolve-protected-type-surface.outputs.compatibility-ranges "
        "|| inputs.secretless-compatibility-ranges }}"
    )
    if "COMPATIBILITY_RANGES: ${{ inputs.secretless-compatibility-ranges }}" not in acquisition:
        raise SystemExit("protected node-ci compatibility input boundary drifted")
    acquisition = acquisition.replace(
        "COMPATIBILITY_RANGES: ${{ inputs.secretless-compatibility-ranges }}",
        dynamic_request,
    )

    acquisition_gate = "if: inputs.secretless-compatibility-ranges != ''"
    if acquisition.count(acquisition_gate) != 1:
        raise SystemExit("protected node-ci compatibility acquisition gate drifted")
    acquisition = acquisition.replace(
        acquisition_gate,
        "if: inputs.protected-type-surface-declaration-path != '' || "
        "inputs.secretless-compatibility-ranges != ''",
        1,
    )

    compatibility_marker = dynamic_request
    if acquisition.count(compatibility_marker) < 1:
        raise SystemExit("protected node-ci compatibility environment drifted")
    environment_lines = []
    for line in acquisition.splitlines(keepends=True):
        environment_lines.append(line)
        if line.lstrip().startswith(compatibility_marker):
            indentation = line[: len(line) - len(line.lstrip())]
            environment_lines.append(
                indentation
                + "ALLOW_PRERELEASE: ${{ inputs.protected-type-surface-allow-prerelease }}\n"
            )
    acquisition = "".join(environment_lines)
    provenance_env = (
        "          COMPATIBILITY_PROVENANCE: ${{ runner.temp }}/secretless-compatibility-"
        "${{ github.run_id }}-${{ github.run_attempt }}/_compatibility/provenance.json\n"
    )
    if acquisition.count(provenance_env) != 1:
        raise SystemExit("protected node-ci compatibility provenance environment drifted")
    acquisition = acquisition.replace(
        provenance_env,
        provenance_env
        + "          PROTECTED_BASELINE_RECEIPT_PATH: ${{ steps.resolve-protected-type-surface.outputs.receipt-path }}\n",
        1,
    )
    auxiliary_marker = "      - name: Resolve immutable auxiliary source\n"
    if acquisition.count(auxiliary_marker) != 1:
        raise SystemExit("protected node-ci auxiliary boundary drifted")
    acquisition = acquisition.replace(
        auxiliary_marker,
        PROTECTED_BASELINE_BIND_STEP + auxiliary_marker,
        1,
    )
    outputs_marker = (
        "      compatibility-provenance-sha256: ${{ steps.package-secretless-transfer.outputs.compatibility-provenance-sha256 }}\n"
    )
    if acquisition.count(outputs_marker) != 1:
        raise SystemExit("protected node-ci acquisition outputs drifted")
    acquisition = acquisition.replace(
        outputs_marker,
        outputs_marker
        + "      protected-baseline-request: ${{ steps.resolve-protected-type-surface.outputs.compatibility-ranges }}\n"
        + "      protected-baseline-base-sha: ${{ steps.resolve-protected-type-surface.outputs.base-sha }}\n"
        + "      protected-baseline-declaration-sha256: ${{ steps.resolve-protected-type-surface.outputs.declaration-sha256 }}\n",
        1,
    )

    build = remainder
    build_request = (
        "COMPATIBILITY_RANGES: ${{ needs.acquire-secretless-dependencies.outputs.protected-baseline-request "
        "|| inputs.secretless-compatibility-ranges }}"
    )
    if "COMPATIBILITY_RANGES: ${{ inputs.secretless-compatibility-ranges }}" not in build:
        raise SystemExit("protected node-ci build compatibility input boundary drifted")
    build = build.replace(
        "COMPATIBILITY_RANGES: ${{ inputs.secretless-compatibility-ranges }}",
        build_request,
    )
    expected_provenance = (
        "          EXPECTED_COMPATIBILITY_PROVENANCE_SHA256: ${{ needs.acquire-secretless-dependencies.outputs.compatibility-provenance-sha256 }}\n"
    )
    if build.count(expected_provenance) < 1:
        raise SystemExit("protected node-ci expected provenance environment drifted")
    build = build.replace(
        expected_provenance,
        expected_provenance
        + "          EXPECTED_PROTECTED_BASELINE_BASE_SHA: ${{ needs.acquire-secretless-dependencies.outputs.protected-baseline-base-sha }}\n"
        + "          EXPECTED_PROTECTED_BASELINE_DECLARATION_SHA256: ${{ needs.acquire-secretless-dependencies.outputs.protected-baseline-declaration-sha256 }}\n"
        + "          PROTECTED_BASELINE_DECLARATION_PATH: ${{ inputs.protected-type-surface-declaration-path }}\n"
        + "          PROTECTED_BASELINE_EXPECTED_PACKAGE: ${{ inputs.protected-type-surface-expected-package }}\n"
        + "          PROTECTED_BASELINE_EXPECTED_SCRIPT: ${{ inputs.protected-type-surface-expected-script }}\n"
        + "          PROTECTED_BASELINE_REPOSITORY: ${{ github.repository }}\n",
        1,
    )
    baseline_shape = (
        '                      or set(provenance) != {"schemaVersion", "request", "lanes"}\n'
    )
    if build.count(baseline_shape) < 1:
        raise SystemExit("protected node-ci provenance shape guard drifted")
    build = build.replace(
        baseline_shape,
        '                      or set(provenance) not in ({"schemaVersion", "request", "lanes"}, {"schemaVersion", "request", "lanes", "protectedBaseline"})\n',
        1,
    )
    baseline_parse = "              provenance = json.loads(provenance_bytes)\n"
    if build.count(baseline_parse) < 1:
        raise SystemExit("protected node-ci provenance parser drifted")
    build = build.replace(
        baseline_parse,
        baseline_parse
        + "".join(
            f"    {line}"
            for line in PROTECTED_BASELINE_TRANSFER_VALIDATION.splitlines(keepends=True)
        ),
        1,
    )
    return acquisition + build


def render() -> str:
    document = SOURCE.read_text(encoding="utf-8")
    document = replace_once(
        document,
        "# Reusable CI for the verJSON Node libraries:",
        "# Generated by scripts/gen-node-ci-protected.py; do not edit.\n"
        "# Protected required-workflow Node.js CI variant:",
    )
    document = replace_once(
        document,
        "      db-image:\n",
        INPUTS + PROTECTED_INPUTS + "      db-image:\n",
    )
    document = insert_protected_baseline_step(document)
    document = configure_protected_baseline(document)
    document = replace_once(document, "          HEAD_SHA: ${{ github.event.pull_request.head.sha || github.sha }}", "          HEAD_SHA: ${{ inputs.head-sha }}")
    document = replace_once(document, "    permissions:\n      contents: read\n      packages: read\n", "    permissions:\n      actions: read\n      contents: read\n      packages: read\n      pull-requests: read\n")
    document = replace_once(document, "          EVENT_NAME: ${{ github.event_name }}\n          HEAD_REPOSITORY: ${{ github.event.pull_request.head.repo.full_name }}", "          EVENT_NAME: ${{ inputs.event-name }}\n          HEAD_REPOSITORY: ${{ inputs.head-repository }}")
    document = replace_once(document, "    if: (inputs.secretless-pr || inputs.secretless-trusted-ref) && needs.eligibility.outputs.should-run != 'false'\n", "    if: needs.eligibility.outputs.should-run != 'false'\n")
    document = replace_once(document, "          [ \"$SECRETLESS_PR\" != \"$SECRETLESS_TRUSTED_REF\" ] || {\n", "          [ \"$SECRETLESS_PR\" = true ] && [ \"$SECRETLESS_TRUSTED_REF\" = false ] || {\n            echo \"::error::protected node-ci requires secretless-pr=true and secretless-trusted-ref=false\"\n            exit 1\n          }\n          [ \"$SECRETLESS_PR\" != \"$SECRETLESS_TRUSTED_REF\" ] || {\n")
    document = replace_once(
        document,
        "          [ -z \"$SCHEMA_DIR\" ] || {\n            echo \"::error::secretless modes do not permit credentialed submodule acquisition\"\n            exit 1\n          }\n",
        "          [ -z \"$SCHEMA_DIR\" ] || {\n            echo \"::error::protected node-ci does not support schema-dir\"\n            exit 1\n          }\n",
    )
    document = replace_once(document, "        with:\n          persist-credentials: false\n", "        with:\n          ref: ${{ inputs.head-sha }}\n          persist-credentials: false\n")
    acquisition_end = document.index("  build-test:\n")
    acquisition = document[:acquisition_end]
    build = document[acquisition_end:]
    transfer_download = "      - name: Download pinned secretless dependency transfer implementation\n"
    if acquisition.count(transfer_download) != 1:
        raise SystemExit("protected node-ci acquisition transfer download boundary drifted")
    acquisition = acquisition.replace(
        transfer_download,
        verifier_step() + transfer_download,
        1,
    )
    document = acquisition + build
    auxiliary_if = "inputs.secretless-auxiliary-source != ''"
    document = replace_once(document, "      - name: Acquire immutable auxiliary source\n", verifier_step(auxiliary_if) + "      - name: Acquire immutable auxiliary source\n")
    document = replace_once(document, "      - name: Populate verified private dependency cache\n", verifier_step() + "      - name: Populate verified private dependency cache\n")
    document = replace_once(document, "    permissions:\n      contents: read\n    steps:\n", "    permissions:\n      actions: read\n      contents: read\n      pull-requests: read\n    steps:\n")
    checkout = "        with:\n          submodules: ${{ (inputs.secretless-pr || inputs.secretless-trusted-ref) && 'false' || 'recursive' }}\n"
    document = replace_once(document, checkout, "        with:\n          ref: ${{ inputs.head-sha }}\n          submodules: ${{ (inputs.secretless-pr || inputs.secretless-trusted-ref) && 'false' || 'recursive' }}\n")
    rebuild_if = "needs.eligibility.outputs.should-run != 'false' && (inputs.secretless-pr || inputs.secretless-trusted-ref) && inputs.secretless-rebuild-packages != ''"
    plan_if = "needs.eligibility.outputs.should-run != 'false' && (inputs.secretless-pr || inputs.secretless-trusted-ref) && (inputs.secretless-ci-script-plan != '' || inputs.secretless-nested-manifests != '')"
    default_if = "needs.eligibility.outputs.should-run != 'false' && (!(inputs.secretless-pr || inputs.secretless-trusted-ref) || inputs.secretless-ci-script-plan == '')"
    document = replace_once(document, "      - name: Rebuild exact approved lifecycle packages without credentials\n", verifier_step(rebuild_if) + "      - name: Rebuild exact approved lifecycle packages without credentials\n")
    document = replace_once(
        document,
        "      - name: Run exact credentialless consumer script plan\n",
        PROTECTED_BASELINE_REF_STEP
        + verifier_step(plan_if)
        + "      - name: Run exact credentialless consumer script plan\n",
    )
    default_commands = """      - run: npm run build
        if: needs.eligibility.outputs.should-run != 'false' && (!(inputs.secretless-pr || inputs.secretless-trusted-ref) || inputs.secretless-ci-script-plan == '')
      - run: npm run typecheck --if-present
        if: needs.eligibility.outputs.should-run != 'false' && (!(inputs.secretless-pr || inputs.secretless-trusted-ref) || inputs.secretless-ci-script-plan == '')
      - run: npm test
        if: needs.eligibility.outputs.should-run != 'false' && (!(inputs.secretless-pr || inputs.secretless-trusted-ref) || inputs.secretless-ci-script-plan == '')
      - run: npm run lint --if-present
        if: needs.eligibility.outputs.should-run != 'false' && (!(inputs.secretless-pr || inputs.secretless-trusted-ref) || inputs.secretless-ci-script-plan == '')
"""
    grouped_default = """      - name: Run default build, typecheck, test, and lint plan
        if: needs.eligibility.outputs.should-run != 'false' && (!(inputs.secretless-pr || inputs.secretless-trusted-ref) || inputs.secretless-ci-script-plan == '')
        run: |
          npm run build
          npm run typecheck --if-present
          npm test
          npm run lint --if-present
"""
    document = replace_once(document, default_commands, verifier_step(default_if) + grouped_default)
    compatibility_if = (
        "needs.eligibility.outputs.should-run != 'false' && "
        "(inputs.secretless-pr || inputs.secretless-trusted-ref) && "
        "(inputs.protected-type-surface-declaration-path != '' || "
        "inputs.secretless-compatibility-ranges != '')"
    )
    legacy_compatibility_if = (
        "needs.eligibility.outputs.should-run != 'false' && "
        "(inputs.secretless-pr || inputs.secretless-trusted-ref) && "
        "inputs.secretless-compatibility-ranges != ''"
    )
    if document.count(legacy_compatibility_if) != 2:
        raise SystemExit("protected node-ci compatibility runtime gate drifted")
    document = document.replace(legacy_compatibility_if, compatibility_if)
    # The protected script plan requires the bubblewrap namespace boundary
    # whenever it runs (a script plan or nested manifests are set), so on
    # GitHub-hosted runners the sandbox must be provisioned for every lane that
    # will execute it, not only for lanes that declare a type surface or
    # compatibility ranges. verjson-cli-projects' lanes pass a script plan and
    # neither of those, and failed closed with "verified bubblewrap namespace
    # boundary is unavailable" the moment their required workflow was
    # activated (#1423).
    hosted_provisioning_if = compatibility_if + " && runner.environment == 'github-hosted'"
    if document.count(hosted_provisioning_if) != 1:
        raise SystemExit("protected node-ci hosted sandbox provisioning gate drifted")
    document = document.replace(
        hosted_provisioning_if,
        "needs.eligibility.outputs.should-run != 'false' && "
        "(inputs.secretless-pr || inputs.secretless-trusted-ref) && "
        "(inputs.protected-type-surface-declaration-path != '' || "
        "inputs.secretless-compatibility-ranges != '' || "
        "inputs.secretless-ci-script-plan != '' || "
        "inputs.secretless-nested-manifests != '') && "
        "runner.environment == 'github-hosted'",
    )
    document = replace_once(document, "      - name: Run runtime-resolved compatibility lanes without credentials\n", verifier_step(compatibility_if) + "      - name: Run runtime-resolved compatibility lanes without credentials\n")
    document = remove_step(document, "Install schema submodule deps")
    document = document.replace(
        "          ref: ${{ inputs.head-sha }}\n          persist-credentials: false\n",
        "          ref: ${{ inputs.head-sha }}\n          fetch-depth: 0\n          persist-credentials: false\n",
        1,
    )
    document = document.replace(
        "          ref: ${{ inputs.head-sha }}\n          submodules: ${{ (inputs.secretless-pr || inputs.secretless-trusted-ref) && 'false' || 'recursive' }}\n",
        "          ref: ${{ inputs.head-sha }}\n          fetch-depth: 0\n          submodules: ${{ (inputs.secretless-pr || inputs.secretless-trusted-ref) && 'false' || 'recursive' }}\n",
        1,
    )
    for step_name in (
        "Rebuild exact approved lifecycle packages without credentials",
        "Run exact credentialless consumer script plan",
        "Run default build, typecheck, test, and lint plan",
        "Run runtime-resolved compatibility lanes without credentials",
    ):
        document = remove_candidate_credentials(document, step_name)
    document = configure_changelog_tool_cache(document)
    document = isolate_candidate_runtime_cache(document)
    document = move_step_before_guard(
        document,
        "Provision trusted compatibility sandbox",
        "Run exact credentialless consumer script plan",
        "Revalidate protected pull-request identity",
    )
    return document


if __name__ == "__main__":
    TARGET.write_text(render(), encoding="utf-8")

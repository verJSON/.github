#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("app_key_audit", ROOT / "scripts/app-key-environment-audit.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def workflow_bindings(directory):
    """Every workflow job that reads an App private key, with its confinement evidence."""
    bindings = []
    for path in sorted(p for p in Path(directory).glob("*") if p.suffix in (".yml", ".yaml")):
        text = path.read_text(encoding="utf-8")
        document = yaml.safe_load(text)
        workflow_scope = {key: value for key, value in document.items() if key != "jobs"}
        mentioned = audit.app_keys(yaml.safe_dump(workflow_scope))
        if mentioned:
            raise ValueError(
                f"{path.name} reads {sorted(mentioned)} at workflow scope, "
                "where no environment confines it"
            )
        captured = set()
        for name, job in (document.get("jobs") or {}).items():
            rendered = yaml.safe_dump(job)
            needs = job.get("needs") or []
            try:
                job_keys = audit.app_keys(rendered)
            except ValueError as error:
                raise ValueError(f"{path.name} job {name} cannot be confined: {error}") from None
            for secret in sorted(job_keys):
                captured.add(secret)
                bindings.append({
                    "workflow": path.stem,
                    "job": name,
                    "secret": secret,
                    "environment": job.get("environment"),
                    "needs": [needs] if isinstance(needs, str) else list(needs),
                })
        # A workflow-level `env:` block reads the key outside every job, where no
        # `environment:` can confine it and where a job-by-job scan sees nothing.
        if mentioned - captured:
            raise ValueError(
                f"{path.name} reads {sorted(mentioned - captured)} outside any job, "
                "where no environment confines it")
    if not bindings:
        raise ValueError("no App key binding was found; the workflow scan is unreliable")
    return bindings


def confinement_pattern(entry):
    """The only expressions that bind a canonical entry's own role environment.

    A substring test admits `${{ inputs.release_environment || 'unprotected' }}`,
    which resolves to an unprotected environment whenever the caller omits the
    input -- the exact absence of confinement the contract exists to reject.
    """
    return (r"\$\{\{ inputs\." + re.escape(entry["input"])
            + r"( \|\| '" + re.escape(entry["environment"]) + r"')? \}\}")


def workflow(name):
    return yaml.safe_load((ROOT / f".github/workflows/{name}.yml").read_text())


def metadata():
    result = {"repos/Verjson/example": [{"default_branch": "main", "owner": {"type": "Organization"}}],
              "repos/Verjson/example/actions/secrets?per_page=100": [{"total_count": 0, "secrets": []}],
              "orgs/Verjson/actions/secrets?per_page=100": [{"total_count": 0, "secrets": []}]}
    for role, key in audit.ROLES.items():
        path = f"repos/Verjson/example/environments/{role}-app"
        result[path] = [{"id": 42, "name": f"{role}-app", "deployment_branch_policy": {
            "protected_branches": False, "custom_branch_policies": True}}]
        result[path + "/deployment-branch-policies?per_page=100"] = [{"total_count": 1, "branch_policies": [{"name": "main", "type": "branch"}]}]
        result[path + "/secrets?per_page=100"] = [{"total_count": 1, "secrets": [{"name": key}]}]
    return result


class StorageAuditTests(unittest.TestCase):
    def test_environment_only_keys_with_exact_main_policies_are_compliant(self):
        self.assertTrue(audit.audit("Verjson/example", metadata().__getitem__)["compliant"])

    def test_broad_copies_fail_even_when_environment_copies_exist(self):
        for scope, path in (("broadRepositoryKeys", "repos/Verjson/example/actions/secrets?per_page=100"),
                            ("broadOrganizationKeys", "orgs/Verjson/actions/secrets?per_page=100")):
            with self.subTest(scope=scope):
                data = metadata()
                data[path] = [{"total_count": 1, "secrets": [{"name": "RELEASE_APP_PRIVATE_KEY"}]}]
                result = audit.audit("Verjson/example", data.__getitem__)
                self.assertFalse(result["compliant"])
                self.assertEqual(result[scope], ["RELEASE_APP_PRIVATE_KEY"])

    def test_missing_environment_key_fails_without_accepting_a_broad_fallback(self):
        data = metadata()
        data["repos/Verjson/example/environments/merge-app/secrets?per_page=100"] = [{"total_count": 0, "secrets": []}]
        self.assertFalse(audit.audit("Verjson/example", data.__getitem__)["compliant"])

    def test_branch_wildcards_tags_additional_refs_and_missing_types_are_rejected(self):
        for policies in ([{"name": "*", "type": "branch"}], [{"name": "main", "type": "tag"}],
                         [{"name": "main"}], [{"name": "main", "type": "branch"}, {"name": "release/*", "type": "branch"}]):
            with self.subTest(policies=policies):
                data = metadata()
                data["repos/Verjson/example/environments/release-app/deployment-branch-policies?per_page=100"] = [{"total_count": len(policies), "branch_policies": policies}]
                with self.assertRaises(ValueError):
                    audit.audit("Verjson/example", data.__getitem__)

    def test_protected_branches_only_is_not_main_only(self):
        data = metadata()
        data["repos/Verjson/example/environments/release-app"][0]["deployment_branch_policy"] = {"protected_branches": True, "custom_branch_policies": False}
        with self.assertRaises(ValueError):
            audit.audit("Verjson/example", data.__getitem__)

    def test_unreadable_metadata_and_incomplete_pagination_never_prove_absence(self):
        with self.assertRaises(subprocess.CalledProcessError):
            audit.audit("Verjson/example", mock.Mock(side_effect=subprocess.CalledProcessError(1, "gh")))
        for pages in ([{"total_count": 1, "secrets": []}], [{"total_count": 2, "secrets": [{"name": "X"}, {"name": "X"}]}]):
            with self.subTest(pages=pages), self.assertRaises(ValueError):
                audit.names(pages, "secrets")

    def test_malformed_environment_identity_and_boolean_metadata_are_rejected(self):
        path = "repos/Verjson/example/environments/release-app"
        for identity in (None, 0, -1, True, "42"):
            with self.subTest(identity=identity):
                data = metadata()
                data[path][0]["id"] = identity
                with self.assertRaises(ValueError):
                    audit.audit("Verjson/example", data.__getitem__)
        for field, value in (("protected_branches", 0), ("custom_branch_policies", 1)):
            with self.subTest(field=field):
                data = metadata()
                data[path][0]["deployment_branch_policy"][field] = value
                with self.assertRaises(ValueError):
                    audit.audit("Verjson/example", data.__getitem__)
        for count in (None, False, "0"):
            with self.subTest(count=count), self.assertRaises(ValueError):
                audit.names([{"total_count": count, "secrets": []}], "secrets")

    def test_inventory_collects_every_page_and_never_fetches_secret_values(self):
        pages = [{"total_count": 2, "secrets": [{"name": "X"}]}, {"total_count": 2, "secrets": [{"name": "Y"}]}]
        self.assertEqual(audit.names(pages, "secrets"), {"X", "Y"})
        with mock.patch.object(audit.subprocess, "run", return_value=mock.Mock(stdout=json.dumps(pages))) as run:
            self.assertEqual(audit.github("repos/Verjson/example/actions/secrets?per_page=100"), pages)
        self.assertEqual(run.call_args.args[0][:4], ["gh", "api", "--paginate", "--slurp"])


class WorkflowBoundaryTests(unittest.TestCase):
    def test_key_consumers_declare_required_environment_and_depend_on_keyless_policy(self):
        consumers = {
            "changelog-release": ("release_environment", ["release"]),
            "container-release": ("release_environment", ["promote"]),
            "renovate-changelog": ("release_environment", ["attribute"]),
            "ai-privileged-merge": ("merge_environment", ["privileged_merge"]),
            "ai-review-merge": ("ai_review_environment", ["gate", "complete-authorization"]),
            "gate-rearm": ("ai_review_environment", ["arm"]),
        }
        for name, (field, jobs) in consumers.items():
            with self.subTest(name=name):
                doc = workflow(name)
                trigger = doc.get("on", doc.get(True))
                self.assertTrue(trigger["workflow_call"]["inputs"][field]["required"])
                self.assertFalse(set(trigger["workflow_call"].get("secrets", {})) & (set(audit.ROLES.values()) | {"release_app_private_key"}))
                policy = doc["jobs"]["app-key-policy"]
                if name == "gate-rearm":
                    # Required-workflow execution cannot start a relative reusable
                    # workflow (ADR 0171), so these two policy jobs remain inline.
                    self.assertNotIn("uses", policy)
                    self.assertEqual(policy["runs-on"], "ubuntu-24.04")
                    self.assertEqual(
                        policy.get("permissions"),
                        {"actions": "read", "contents": "read"},
                    )
                    self.assertNotIn("secrets", policy)
                    key_validation = doc["jobs"]["validate-ai-review-key"]
                    self.assertEqual(key_validation["needs"], "app-key-policy")
                    self.assertEqual(
                        key_validation["environment"],
                        "${{ inputs.ai_review_environment || 'ai-review-app' }}",
                    )
                    self.assertEqual(key_validation.get("permissions"), {"contents": "read"})
                    self.assertNotIn("uses", key_validation)
                else:
                    self.assertEqual(policy["uses"], "./.github/workflows/app-key-environment.yml")
                    # Keep the caller's read-only token boundary explicit while
                    # passing the reduced permissions into the nested policy.
                    self.assertEqual(
                        policy.get("permissions"),
                        {"actions": "read", "contents": "read"},
                        f"{name}: app-key policy must retain explicit read-only permissions",
                    )
                    if name == "ai-review-merge":
                        # The reusable edge carries only the one named secret
                        # consumed by its environment-bound validator.
                        self.assertEqual(
                            policy.get("secrets"),
                            {"AI_REVIEW_APP_PRIVATE_KEY": "${{ secrets.AI_REVIEW_APP_PRIVATE_KEY }}"},
                        )
                    else:
                        self.assertNotIn("secrets", policy)
                for job in jobs:
                    value = doc["jobs"][job]
                    self.assertIn(field, value["environment"])
                    self.assertIn("app-key-policy", value["needs"])
                    if job == "arm":
                        self.assertIn("always()", value["if"])
                        self.assertIn("needs.event-policy.outputs.run_control_plane == 'true'", value["if"])
                        self.assertNotIn("needs.app-key-policy.result == 'success'", value["if"])
                    elif job == "complete-authorization":
                        self.assertIn("always()", value["if"])
                        self.assertIn("inputs.authorization_check_id != ''", value["if"])
                        app_token = next(
                            step for step in value["steps"] if step.get("name") == "Mint dedicated authorization App token"
                        )
                        self.assertIn("needs.app-key-policy.result == 'success'", app_token["if"])
                    elif "always()" in value.get("if", ""):
                        self.assertIn("needs.app-key-policy.result == 'success'", value["if"])

    def test_ai_review_key_policy_validates_the_resolved_environment_secret(self):
        policy = workflow("app-key-environment")
        call = policy.get("on", policy.get(True))["workflow_call"]
        self.assertFalse(call["secrets"]["AI_REVIEW_APP_PRIVATE_KEY"]["required"])
        job = policy["jobs"]["validate-ai-review-key"]
        self.assertEqual(job["if"], "${{ inputs.role == 'ai-review' }}")
        self.assertEqual(job["needs"], "validate")
        self.assertEqual(job["environment"], "${{ inputs.environment }}")
        validator = next(step for step in job["steps"] if step.get("name") == "Validate resolved AI review App private key")
        self.assertEqual(validator["env"]["AI_REVIEW_APP_PRIVATE_KEY"], "${{ secrets.AI_REVIEW_APP_PRIVATE_KEY }}")
        self.assertFalse(any(step.get("uses", "").startswith("actions/checkout@") for step in job["steps"]))
        self.assertIn('key="${AI_REVIEW_APP_PRIVATE_KEY:-}"', validator["run"])
        self.assertIn("umask 077", validator["run"])
        self.assertIn("mktemp", validator["run"])
        self.assertIn("trap 'rm -f \"$key_file\"' EXIT", validator["run"])
        self.assertIn('openssl rsa -in "$key_file" -check -noout', validator["run"])

    def test_gate_rearm_inline_validators_match_the_reusable_policy_source(self):
        source = workflow("app-key-environment")
        rearm = workflow("gate-rearm")
        source_validate = next(
            step for step in source["jobs"]["validate"]["steps"]
            if step.get("name") == "Verify the caller's main-only role environment before key-job admission"
        )
        inline_validate = next(
            step for step in rearm["jobs"]["app-key-policy"]["steps"]
            if step.get("name") == source_validate["name"]
        )
        self.assertEqual(inline_validate["run"], source_validate["run"])
        self.assertEqual(inline_validate["env"]["ROLE"], "ai-review")
        self.assertEqual(
            inline_validate["env"]["ENVIRONMENT"],
            "${{ inputs.ai_review_environment || 'ai-review-app' }}",
        )
        source_key = next(
            step for step in source["jobs"]["validate-ai-review-key"]["steps"]
            if step.get("name") == "Validate resolved AI review App private key"
        )
        inline_key = next(
            step for step in rearm["jobs"]["validate-ai-review-key"]["steps"]
            if step.get("name") == source_key["name"]
        )
        self.assertEqual(inline_key["run"], source_key["run"])
        self.assertEqual(inline_key["env"], source_key["env"])
        self.assertFalse(any(step.get("uses", "").startswith("actions/checkout@") for step in rearm["jobs"]["validate-ai-review-key"]["steps"]))
        selector = rearm["jobs"]["validate-ai-review-key"]["runs-on"]
        self.assertIn("github.event.repository.private != true && 'ubuntu-24.04'", selector)
        self.assertIn("github.event.repository.private == true && fromJSON(vars.CI_LANE_TRUSTED", selector)
        self.assertIn("|| 'ubuntu-24.04'", selector)
        arm = rearm["jobs"]["arm"]
        self.assertIn("app-key-policy", arm["needs"])
        self.assertIn("validate-ai-review-key", arm["needs"])
        arm_step = next(step for step in arm["steps"] if step.get("id") == "arm")
        self.assertIn("AI_REVIEW_KEY_VALIDATION_RESULT", arm_step["env"])
        self.assertIn("AI_REVIEW_KEY_VALIDATION_RESULT", arm_step["run"])

    def test_retry_inherits_context_and_retains_fixed_environment(self):
        retry = workflow("ai-promotion-retry")
        self.assertTrue(retry[True]["workflow_call"]["inputs"]["merge_environment"]["required"])
        self.assertEqual(retry["jobs"]["promote"]["secrets"], "inherit")
        self.assertIn("merge_environment", retry["jobs"]["promote"]["with"])
        self.assertNotIn("release_app_private_key", workflow("node-release")[True]["workflow_call"].get("secrets", {}))

    def test_retry_chain_and_native_callers_preserve_inheritance_at_every_edge(self):
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        generated = subprocess.check_output(["bash", str(ROOT / "scripts/gen-privileged-merge-caller.sh"), sha,
            "--retry", '["CI"]', '[{"name":"CI","app_id":1,"workflow_id":2,"workflow_path":".github/workflows/ci.yml"}]'], text=True)
        retry = yaml.safe_load(generated)["jobs"]["retry"]
        self.assertEqual(retry["uses"], f"Verjson/.github/.github/workflows/ai-promotion-retry.yml@{sha}")
        self.assertEqual(retry["secrets"], "inherit")
        self.assertEqual(retry["with"]["merge_environment"], "merge-app")
        for name, job, target, field in [
                ("ai-promotion-retry", "promote", "ai-privileged-merge", "merge_environment"),
                ("ai-review-label-rearm", "rearm", "gate-rearm", "ai_review_environment"),
                ("container-release-workflow-ref-canary", "probe", "container-release", "release_environment")]:
            with self.subTest(name=name):
                edge = workflow(name)["jobs"][job]
                self.assertEqual(edge["uses"], f"./.github/workflows/{target}.yml")
                self.assertEqual(edge["secrets"], "inherit")
                self.assertIn(field, edge["with"])

    def test_inheritance_preserves_actual_model_names_and_narrow_node_publication(self):
        review = workflow("ai-review-merge")
        names = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}
        self.assertEqual(set(review[True]["workflow_call"]["secrets"]), names)
        source = (ROOT / ".github/workflows/ai-review-merge.yml").read_text()
        for name in names:
            self.assertIn("${{ secrets." + name + " }}", source)
            self.assertNotIn("${{ secrets." + name.lower() + " }}", source)
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        generated = subprocess.check_output(["bash", str(ROOT / "scripts/gen-changelog-caller.sh"), "release-node", sha], text=True)
        jobs = yaml.safe_load(generated)["jobs"]
        publishing = next(job for job in jobs.values() if "node-release.yml@" in job.get("uses", ""))
        self.assertEqual(publishing["secrets"], {"NODE_AUTH_TOKEN": "${{ secrets.NODE_AUTH_TOKEN }}"})
        snapshot = next(job for job in jobs.values() if "changelog-release.yml@" in job.get("uses", ""))
        self.assertEqual(snapshot["secrets"], "inherit")

    def test_body_only_edits_skip_jobs_but_title_hold_transitions_run(self):
        jobs = workflow("gate-rearm")["jobs"]
        policy_guard = "${{ needs.event-policy.outputs.run_control_plane == 'true' }}"
        arm_guard = "${{ always() && needs.event-policy.outputs.run_control_plane == 'true' }}"
        self.assertEqual(jobs["app-key-policy"]["if"], policy_guard)
        self.assertEqual(jobs["arm"]["if"], arm_guard)
        self.assertEqual(jobs["event-policy"]["outputs"]["old_title_held"], "${{ steps.classify.outputs.old_title_held }}")
        self.assertEqual(jobs["event-policy"]["outputs"]["new_title_held"], "${{ steps.classify.outputs.new_title_held }}")
        self.assertEqual(jobs["arm"]["env"]["EVENT_NEW_TITLE_HELD"], "${{ needs.event-policy.outputs.new_title_held }}")
        self.assertIn("EVENT_TITLE_CHANGED", jobs["event-policy"]["steps"][0]["env"])
        marker = r'(^|[^ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_])DO\ NOT\ MERGE([^ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_]|$)'
        self.assertIn("ascii_upcase", jobs["event-policy"]["steps"][0]["run"])
        self.assertIn(marker, jobs["event-policy"]["steps"][0]["run"])

        review_jobs = workflow("ai-review-merge")["jobs"]
        self.assertEqual(review_jobs["title-policy"]["permissions"], {})
        self.assertEqual(review_jobs["preflight"]["needs"], "title-policy")
        self.assertIn("needs.title-policy.outputs.title_held != 'true'", review_jobs["preflight"]["if"])
        self.assertIn("ascii_upcase", review_jobs["title-policy"]["steps"][0]["run"])
        self.assertIn(marker, review_jobs["title-policy"]["steps"][0]["run"])

    def test_gate_rearm_uses_both_inline_policy_results_without_caller_sha_validator_checkout(self):
        jobs = workflow("gate-rearm")["jobs"]
        arm = jobs["arm"]
        self.assertIn("app-key-policy", arm["needs"])
        self.assertIn("validate-ai-review-key", arm["needs"])
        self.assertFalse(
            any(step.get("with", {}).get("path") == ".gate-trust" for step in arm["steps"])
        )
        self.assertFalse(
            any(
                step.get("with", {}).get("repository") == "Verjson/.github"
                and "github.workflow_sha" in step.get("with", {}).get("ref", "")
                for step in arm["steps"]
            )
        )
        receipt = next(step for step in arm["steps"] if step.get("id") == "arm")
        self.assertEqual(
            receipt["env"]["APP_KEY_POLICY_RESULT"], "${{ needs.app-key-policy.result }}"
        )
        self.assertEqual(
            receipt["env"]["AI_REVIEW_KEY_VALIDATION_RESULT"],
            "${{ needs.validate-ai-review-key.result }}",
        )
        self.assertNotIn("AI_REVIEW_APP_PRIVATE_KEY", receipt["env"])
        self.assertNotIn(".gate-trust/scripts/ci-gate/validate-ai-review-app-key.sh", receipt["run"])
        self.assertIn(
            'if [ "$APP_KEY_POLICY_RESULT" != success ] || [ "$AI_REVIEW_KEY_VALIDATION_RESULT" != success ]; then',
            receipt["run"],
        )

    def test_native_rearm_and_retry_defaults_are_only_canonical_role_environments(self):
        rearm = workflow("gate-rearm")["jobs"]
        expression = "${{ inputs.ai_review_environment || 'ai-review-app' }}"
        self.assertEqual(rearm["validate-ai-review-key"]["environment"], expression)
        self.assertEqual(rearm["arm"]["environment"], expression)
        self.assertEqual(workflow("ai-promotion-retry")["jobs"]["promote"]["with"]["merge_environment"],
                         "${{ inputs.merge_environment || 'merge-app' }}")

    def test_generators_emit_environment_names_with_inherited_context_without_key_values(self):
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        cases = [(["gen-ai-review-caller.sh", sha], "ai_review_environment"),
                 (["gen-gate-rearm-caller.sh", sha], "ai_review_environment"),
                 (["gen-ai-review-label-rearm-caller.sh", sha], "ai_review_environment"),
                 (["gen-privileged-merge-caller.sh", sha, '[{"name":"CI","app_id":1,"workflow_id":2,"workflow_path":".github/workflows/ci.yml"}]'], "merge_environment"),
                 (["gen-container-release.sh", "workflow", sha], "release_environment"),
                 (["gen-changelog-caller.sh", "release-node", sha], "release_environment"),
                 (["gen-changelog-caller.sh", "release-snapshot", sha], "release_environment"),
                 (["gen-changelog-caller.sh", "renovate-attribution", sha], "release_environment")]
        for command, field in cases:
            with self.subTest(command=command[0:2]):
                text = subprocess.check_output(["bash", str(ROOT / "scripts" / command[0]), *command[1:]], cwd=ROOT, text=True)
                doc = yaml.safe_load(text)
                self.assertNotIn("PRIVATE_KEY", text)
                self.assertIn("secrets: inherit", text)
                consumers = [j for j in doc["jobs"].values() if field in j.get("with", {})]
                self.assertTrue(consumers)
                self.assertTrue(all("environment" not in j for j in consumers))
                self.assertTrue(all(j.get("secrets") == "inherit" for j in consumers))

    def test_lint_exception_is_exact_to_the_environment_secret_schema_gap(self):
        config = yaml.safe_load((ROOT / ".github/actionlint.yaml").read_text())
        patterns = config["paths"][".github/workflows/ai-review-merge.yml"]["ignore"]
        selected = [p for p in patterns if "ai_review_app_private_key" in p]
        self.assertEqual(len(selected), 1)
        message = 'property "ai_review_app_private_key" is not defined in object type {actions_runner_debug: string; actions_step_debug: string; anthropic_api_key: string; claude_code_oauth_token: string; deepseek_api_key: string; github_token: string; openai_api_key: string}'
        self.assertIsNotNone(re.fullmatch(selected[0], message))
        self.assertIsNone(re.fullmatch(selected[0], message.replace("ai_review_app_private_key", "unexpected_private_key")))

    def test_policy_validation_has_no_key_environment_or_checkout(self):
        doc = workflow("app-key-environment")
        self.assertEqual(doc["permissions"], {"actions": "read", "contents": "read"})
        job = doc["jobs"]["validate"]
        self.assertNotIn("environment", job)
        self.assertNotIn("secrets", str(job))
        self.assertNotIn("actions/checkout", str(job))

    def run_policy(self, *, ref="refs/heads/main", name="release-app", role="release", policy=None, unavailable=False):
        step = workflow("app-key-environment")["jobs"]["validate"]["steps"][0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gh = root / "gh"
            gh.write_text('#!/usr/bin/env python3\nimport json,os,sys\nif os.environ.get("UNAVAILABLE")=="1":sys.exit(1)\nif "deployment-branch-policies" in sys.argv[-1]:print(os.environ["POLICY"])\nelse:print(json.dumps({"id":42,"name":os.environ["ENVIRONMENT"],"deployment_branch_policy":{"protected_branches":False,"custom_branch_policies":True}}))\n')
            gh.chmod(0o755)
            result = subprocess.run(["bash", "-c", step["run"]], text=True, capture_output=True,
                env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}", "REF": ref, "DEFAULT_BRANCH": "main",
                     "ROLE": role, "ENVIRONMENT": name, "REPOSITORY": "Verjson/example", "UNAVAILABLE": "1" if unavailable else "0",
                     "POLICY": json.dumps(policy or {"total_count": 1, "branch_policies": [{"name": "main", "type": "branch"}]})})
            return result.returncode

    def test_native_policy_preflight_accepts_only_main_and_exact_role(self):
        self.assertEqual(self.run_policy(), 0)
        for kwargs in ({"ref": "refs/heads/topic"}, {"ref": "refs/tags/main"}, {"name": "unguarded"}, {"role": "unknown"}, {"unavailable": True},
                       {"policy": {"total_count": 1, "branch_policies": [{"name": "*", "type": "branch"}]}},
                       {"policy": {"total_count": 1, "branch_policies": [{"name": "main", "type": "tag"}]}}):
            with self.subTest(kwargs=kwargs):
                self.assertNotEqual(self.run_policy(**kwargs), 0)



WORKFLOW = """\
on: workflow_dispatch
jobs:
  mint:
    runs-on: ubuntu-latest
    environment: release-app
    steps:
      - run: echo "${{ %s }}"
"""


class WorkflowScanTests(unittest.TestCase):
    """The directory scan sees every shape in which a workflow can read an App key.

    Each case below is a real GitHub Actions spelling that the first scan missed, so
    a binding written that way contributed nothing and read as full coverage (#1285).
    """

    def scan(self, files):
        with tempfile.TemporaryDirectory() as directory:
            for name, body in files.items():
                (Path(directory) / name).write_text(body, encoding="utf-8")
            return workflow_bindings(directory)

    def test_a_yaml_extension_workflow_is_scanned_like_a_yml_one(self):
        bindings = self.scan({"mint.yaml": WORKFLOW % "secrets.RELEASE_APP_PRIVATE_KEY"})
        self.assertEqual([binding["secret"] for binding in bindings], ["RELEASE_APP_PRIVATE_KEY"])

    def test_index_expression_syntax_is_the_same_binding_as_dotted_syntax(self):
        for expression in ("secrets['RELEASE_APP_PRIVATE_KEY']", 'secrets["RELEASE_APP_PRIVATE_KEY"]'):
            with self.subTest(expression=expression):
                bindings = self.scan({"mint.yml": WORKFLOW % expression})
                self.assertEqual([binding["secret"] for binding in bindings], ["RELEASE_APP_PRIVATE_KEY"])

    def test_an_app_key_named_outside_the_canonical_suffix_is_still_an_app_key(self):
        for name in ("RELEASE_APP_KEY_PEM", "RUNNER_DEPLOY_APP_KEY", "x_app_private_key"):
            with self.subTest(name=name):
                bindings = self.scan({"mint.yml": WORKFLOW % f"secrets.{name}"})
                self.assertEqual([binding["secret"] for binding in bindings], [name])

    def test_a_secret_that_is_not_an_app_key_contributes_no_binding(self):
        for name in ("NODE_AUTH_TOKEN", "RELEASE_KEY", "MERGE_APP_ID"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.scan({"mint.yml": WORKFLOW % f"secrets.{name}"})

    def test_a_key_read_outside_every_job_is_an_error_not_a_silent_zero(self):
        preamble = ("on: workflow_dispatch\n"
                    "env:\n"
                    "  APP_KEY: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}\n")
        job = ("jobs:\n"
               "  mint:\n"
               "    runs-on: ubuntu-latest\n"
               "    environment: release-app\n"
               "    steps:\n"
               "      - run: echo \"$APP_KEY\"\n")
        other = WORKFLOW % "secrets.MERGE_APP_PRIVATE_KEY"
        for body in (preamble, preamble + job):
            # The companion file binds a key inside a job, so the directory-wide
            # "no binding at all" guard cannot be what rejects these.
            with self.subTest(jobs=body is not preamble), self.assertRaises(ValueError):
                self.scan({"leak.yml": body, "mint.yml": other})

    def test_a_key_read_outside_a_job_is_seen_even_when_a_job_reads_the_same_key(self):
        # Reconciling file-level mentions against the union of every job's captures
        # lets a workflow-level read hide behind any job that reads the same key.
        body = ("on: workflow_dispatch\n"
                "env:\n"
                "  APP_KEY: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}\n"
                "jobs:\n"
                "  mint:\n"
                "    runs-on: ubuntu-latest\n"
                "    environment: release-app\n"
                "    steps:\n"
                "      - run: echo \"${{ secrets.RELEASE_APP_PRIVATE_KEY }}\"\n")
        with self.assertRaises(ValueError):
            self.scan({"leak.yml": body})

    def test_case_folded_and_space_separated_secrets_references_are_the_same_binding(self):
        for expression in ("SECRETS.RELEASE_APP_PRIVATE_KEY",
                           "Secrets.RELEASE_APP_PRIVATE_KEY",
                           "secrets . RELEASE_APP_PRIVATE_KEY",
                           "SECRETS[ 'RELEASE_APP_PRIVATE_KEY' ]"):
            with self.subTest(expression=expression):
                bindings = self.scan({"mint.yml": WORKFLOW % expression})
                self.assertEqual([binding["secret"] for binding in bindings],
                                 ["RELEASE_APP_PRIVATE_KEY"])

    def test_a_wholesale_or_dynamically_indexed_secrets_read_is_rejected_not_ignored(self):
        """A read the scan cannot resolve to a name is rejected, never passed over.

        Each of these reads a real App private key while naming none, so ignoring
        it leaves the gate green on a binding nothing confines (#1285).
        """
        for expression in ("toJSON(secrets)",
                           "toJson(secrets)",
                           "fromJSON(toJSON(secrets))",
                           "secrets[format('{0}_APP_PRIVATE_KEY', inputs.role)]",
                           "secrets[env.KEYNAME]",
                           "secrets[matrix.key]"):
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError) as raised:
                    self.scan({"mint.yml": WORKFLOW % expression})
                message = str(raised.exception)
                self.assertIn("mint.yml", message)
                self.assertIn("job mint", message)
                self.assertIn(expression, message)
                self.assertIn("cannot be confined", message)


class AppKeyRoleManifestTests(unittest.TestCase):
    """Every App private key a canonical workflow binds is accounted for.

    The consumer list in WorkflowBoundaryTests is hand-written, so it proved the
    three ADR 0171 roles and silently ignored every other App key in the same
    workflow directory. This enumerates the directory instead, so a new binding
    cannot be added without declaring its confinement (#1285).
    """

    def setUp(self):
        self.manifest = audit.load_roles()
        self.bindings = workflow_bindings(ROOT / ".github/workflows")

    def test_manifest_and_workflows_declare_exactly_the_same_app_keys(self):
        self.assertEqual({entry["secret"] for entry in self.manifest},
                         {binding["secret"] for binding in self.bindings})

    def test_every_confined_binding_declares_its_declared_environment(self):
        declared = {entry["secret"]: entry for entry in self.manifest}
        for binding in self.bindings:
            entry = declared[binding["secret"]]
            with self.subTest(workflow=binding["workflow"], job=binding["job"]):
                if binding["workflow"] == "app-key-environment" and binding["job"] == "validate-ai-review-key":
                    self.assertEqual(binding["environment"], "${{ inputs.environment }}")
                    self.assertIn("validate", binding["needs"])
                elif binding["job"] == "app-key-policy":
                    # This narrow forwarder passes the key only to the reusable
                    # policy workflow, which validates it inside its environment.
                    caller = workflow(binding["workflow"])
                    policy = caller["jobs"]["app-key-policy"]
                    if binding["workflow"] == "gate-rearm":
                        # The injected required workflow pins the validator to a
                        # full immutable, ref-reachable SHA (ADR 0171).
                        self.assertRegex(
                            policy["uses"],
                            r"^Verjson/\.github/\.github/workflows/app-key-environment\.yml@[0-9a-f]{40}$",
                        )
                    else:
                        self.assertEqual(policy["uses"], "./.github/workflows/app-key-environment.yml")
                    self.assertIn("environment", policy["with"])
                    self.assertEqual(
                        policy.get("secrets"),
                        {"AI_REVIEW_APP_PRIVATE_KEY": "${{ secrets.AI_REVIEW_APP_PRIVATE_KEY }}"},
                    )
                elif entry["confinement"] == "unconfined":
                    self.assertIsNone(binding["environment"])
                elif entry["confinement"] == "caller-owned":
                    self.assertEqual(binding["environment"], entry["environment"])
                else:
                    self.assertIsNotNone(
                        re.fullmatch(confinement_pattern(entry), binding["environment"] or ""),
                        f"{binding['environment']!r} does not bind {entry['environment']}")

    def test_a_canonical_expression_defaulting_elsewhere_is_not_confinement(self):
        pattern = confinement_pattern({"input": "release_environment", "environment": "release-app"})
        for accepted in ("${{ inputs.release_environment }}",
                         "${{ inputs.release_environment || 'release-app' }}"):
            with self.subTest(accepted=accepted):
                self.assertIsNotNone(re.fullmatch(pattern, accepted))
        for rejected in ("${{ inputs.release_environment || 'unprotected' }}",
                         "${{ inputs.release_environment || '' }}",
                         "${{ inputs.release_environment_override }}",
                         "prefix-${{ inputs.release_environment }}",
                         "${{ inputs.merge_environment }}",
                         "release-app"):
            with self.subTest(rejected=rejected):
                self.assertIsNone(re.fullmatch(pattern, rejected))

    def test_canonical_bindings_depend_on_the_keyless_policy_preflight(self):
        canonical = {e["secret"] for e in self.manifest if e["confinement"] == "canonical"}
        for binding in self.bindings:
            if binding["secret"] not in canonical:
                continue
            with self.subTest(workflow=binding["workflow"], job=binding["job"]):
                if binding["workflow"] == "app-key-environment":
                    self.assertIn("validate", binding["needs"])
                elif binding["job"] == "app-key-policy":
                    self.assertEqual(
                        workflow(binding["workflow"])["jobs"]["app-key-policy"]["secrets"],
                        {"AI_REVIEW_APP_PRIVATE_KEY": "${{ secrets.AI_REVIEW_APP_PRIVATE_KEY }}"},
                    )
                else:
                    self.assertIn("app-key-policy", binding["needs"])

    def test_policy_workflow_accepts_exactly_the_canonical_roles(self):
        roles = {e["role"] for e in self.manifest if e["confinement"] == "canonical"}
        step = workflow("app-key-environment")["jobs"]["validate"]["steps"][0]
        accepted = re.search(r'case "\$ROLE" in ([a-z|-]+)\)', step["run"]).group(1)
        self.assertEqual(set(accepted.split("|")), roles)

    def test_unconfined_keys_name_their_exposure_and_tracking_issue(self):
        pending = [entry for entry in self.manifest if entry["confinement"] == "unconfined"]
        self.assertTrue(pending, "remove this test once every App key is confined")
        for entry in pending:
            with self.subTest(secret=entry["secret"]):
                self.assertEqual(entry["tracking"], 1285)
                self.assertTrue(entry["reason"].strip())

    def test_audit_reports_every_declared_app_key_as_a_broad_copy(self):
        data = metadata()
        data["orgs/Verjson/actions/secrets?per_page=100"] = [{
            "total_count": len(self.manifest),
            "secrets": [{"name": entry["secret"]} for entry in self.manifest]}]
        result = audit.audit("Verjson/example", data.__getitem__)
        self.assertFalse(result["compliant"])
        self.assertEqual(result["broadOrganizationKeys"],
                         sorted(entry["secret"] for entry in self.manifest))



class AppKeyRoleManifestValidationTests(unittest.TestCase):
    def manifest(self, **overrides):
        entry = {"role": "release", "secret": "RELEASE_APP_PRIVATE_KEY", "environment": "release-app",
                 "confinement": "canonical", "organization_copy": "withdraw"} | overrides
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roles.json"
            path.write_text(json.dumps({"roles": [entry]}), encoding="utf-8")
            return audit.load_roles(path)

    def test_a_well_formed_entry_loads(self):
        self.assertEqual(self.manifest()[0]["role"], "release")

    def test_malformed_entries_are_rejected_rather_than_silently_skipped(self):
        for overrides in ({"confinement": "none"}, {"confinement": None}, {"secret": "RELEASE_KEY"},
                          {"secret": ""}, {"secret": None}, {"role": ""}, {"role": None},
                          {"environment": ""}, {"environment": None},
                          {"environment": "release-cache"},
                          {"organization_copy": "keep"}, {"organization_copy": None}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.manifest(**overrides)

    def test_a_caller_owned_entry_may_name_an_environment_outside_the_role_convention(self):
        entry = self.manifest(confinement="caller-owned", environment="production")[0]
        self.assertEqual(entry["environment"], "production")

    def test_the_audit_reads_the_declared_environment_rather_than_deriving_a_second_one(self):
        requested = []

        def api(path):
            requested.append(path)
            return metadata()[path]

        audit.audit("Verjson/example", api)
        for entry in audit.load_roles():
            if entry["confinement"] == "canonical":
                self.assertIn(f"repos/Verjson/example/environments/{entry['environment']}", requested)

    def test_an_unverifiable_audit_names_the_cause_beside_the_guidance(self):
        cause = "release-app permits a ref other than the main branch"
        stdout = io.StringIO()
        with mock.patch.object(audit, "audit", side_effect=ValueError(cause)), \
                mock.patch.object(sys, "argv", ["audit", "--repo", "Verjson/example"]), \
                contextlib.redirect_stdout(stdout):
            self.assertEqual(audit.main(), 1)
        self.assertIn(cause, stdout.getvalue())
        self.assertIn("check metadata access", stdout.getvalue())

    def test_an_empty_or_duplicated_manifest_never_reads_as_full_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roles.json"
            entry = {"role": "release", "secret": "RELEASE_APP_PRIVATE_KEY", "environment": "release-app",
                     "confinement": "canonical", "organization_copy": "withdraw"}
            for roles in ([], {}, [entry, dict(entry, role="other")]):
                with self.subTest(roles=roles):
                    path.write_text(json.dumps({"roles": roles}), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        audit.load_roles(path)

    def test_a_directory_with_no_binding_is_an_error_not_an_empty_result(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                workflow_bindings(directory)


if __name__ == "__main__":
    unittest.main()

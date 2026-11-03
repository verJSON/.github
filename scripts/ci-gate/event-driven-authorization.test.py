#!/usr/bin/env python3
"""Contract tests for the head-bound, event-driven AI authorization gate."""

from dataclasses import dataclass
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

import yaml


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from app_client_id_contract import CLIENT_ID_GUARD_RUN
REVIEW = ROOT / ".github/workflows/ai-review-merge.yml"
DISPATCH = ROOT / ".github/workflows/ai-review-merge-dispatch.yml"
REARM = ROOT / ".github/workflows/gate-rearm.yml"
PROMOTE = ROOT / ".github/workflows/ai-privileged-merge.yml"
TERMINAL_MERGE = ROOT / "scripts/ci-gate/terminal-merge.sh"
RETRY = ROOT / ".github/workflows/ai-promotion-retry.yml"
APP_TOKEN_ACTION = "actions/create-github-app-token"
IMMUTABLE_ACTION = re.compile(rf"^{re.escape(APP_TOKEN_ACTION)}@[0123456789abcdef]{{40}}$")
MODEL_ACTION_NAME = "anthropics/claude-code-action"
MODEL_ACTION_SHA = "c81e3bc69d1b18badbb63ba39581218f02421678"
MODEL_ACTION = f"{MODEL_ACTION_NAME}@{MODEL_ACTION_SHA}"
IMMUTABLE_MODEL_ACTION = re.compile(
    rf"^{re.escape(MODEL_ACTION_NAME)}@[0123456789abcdef]{{40}}$")
TRUSTED_BOTS = {"renovate[bot]", "mend[bot]", "github-actions[bot]"}
PUBLIC_PR_EVENTS = (
    "pull_request",
    "pull_request_review",
    "pull_request_review_comment",
    "merge_group",
    "pull_request_target",
)


def load(path: Path):
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def compact_expression(expression: str) -> str:
    expression = expression.strip()
    if expression.startswith("${{") and expression.endswith("}}"):
        expression = expression[3:-2]
    characters = []
    quote = None
    escaped = False
    for character in expression:
        if quote is not None:
            characters.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif character in "\"'":
            quote = character
            characters.append(character)
        elif not character.isspace():
            characters.append(character)
    if quote is not None:
        raise AssertionError("unterminated quoted workflow expression")
    return "".join(characters)


def split_top_level(expression: str, operator: str) -> list[str]:
    terms = []
    depth = 0
    quote = None
    start = 0
    index = 0
    while index < len(expression):
        character = expression[index]
        if quote is not None:
            if character == quote:
                quote = None
            index += 1
            continue
        if character in "\"'":
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                raise AssertionError("unbalanced workflow expression parentheses")
        elif depth == 0 and expression.startswith(operator, index):
            terms.append(expression[start:index])
            index += len(operator)
            start = index
            continue
        index += 1
    if depth != 0 or quote is not None:
        raise AssertionError("unbalanced workflow expression parentheses or quotes")
    terms.append(expression[start:])
    return terms


HEREDOC = re.compile(
    r"<<(?!<)(?P<strip>-?)\s*(?P<word>(?:"
    r"\\[^\r\n]|'[^'\r\n]*'|\"(?:\\.|[^\"\\\r\n])*\"|"
    r"[^\s;'\"|&()<>\\])+)"
)


@dataclass(frozen=True)
class ShellLexicalState:
    quote: str | None = None
    parameter_quotes: tuple[bool, ...] = ()


def shell_lexical_mask(
    line: str, initial_state: ShellLexicalState
) -> tuple[list[bool], ShellLexicalState, set[int]]:
    """Mark characters outside shell quotes and record continued newlines."""
    mask = [False] * len(line)
    quote = initial_state.quote
    parameter_quotes = list(initial_state.parameter_quotes)
    continuations: set[int] = set()
    index = 0

    while index < len(line):
        character = line[index]
        if quote == "single":
            if character == "'":
                quote = None
            index += 1
            continue
        if quote in {"ansi", "backtick"}:
            closing = "'" if quote == "ansi" else "`"
            if character == "\\" and index + 1 < len(line):
                if line[index + 1] == "\n":
                    continuations.add(index)
                index += 2
                continue
            if character == closing:
                quote = None
            index += 1
            continue
        if quote == "double":
            if character == "\\" and index + 1 < len(line):
                if line[index + 1] in '$`"\\\n':
                    if line[index + 1] == "\n":
                        continuations.add(index)
                    index += 2
                    continue
            if parameter_quotes:
                if parameter_quotes[-1]:
                    if character == '"':
                        parameter_quotes[-1] = False
                    index += 1
                    continue
                if line.startswith("${", index):
                    parameter_quotes.append(False)
                    index += 2
                    continue
                if character == '"':
                    parameter_quotes[-1] = True
                    index += 1
                    continue
                if character == "}":
                    parameter_quotes.pop()
                    index += 1
                    continue
                index += 1
                continue
            if line.startswith("${", index):
                parameter_quotes.append(False)
                index += 2
                continue
            if character == '"':
                quote = None
            index += 1
            continue

        mask[index] = True
        if character == "\\" and index + 1 < len(line):
            mask[index] = False
            if line[index + 1] == "\n":
                continuations.add(index)
            index += 2
            continue
        if line.startswith("$'", index):
            mask[index] = False
            quote = "ansi"
            index += 2
            continue
        if character == "'":
            mask[index] = False
            quote = "single"
        elif character == '"':
            mask[index] = False
            quote = "double"
        elif character == "`":
            mask[index] = False
            quote = "backtick"
        index += 1

    return mask, ShellLexicalState(quote, tuple(parameter_quotes)), continuations


def decode_heredoc_word(word: str) -> tuple[str, bool]:
    delimiter: list[str] = []
    quoted = False
    quote: str | None = None
    index = 0
    while index < len(word):
        character = word[index]
        if quote is not None:
            if character == quote:
                quote = None
                quoted = True
            elif character == "\\" and quote == '"' and index + 1 < len(word):
                escaped = word[index + 1]
                if escaped in '$`"\\':
                    index += 1
                    delimiter.append(escaped)
                    quoted = True
                else:
                    delimiter.append(character)
            else:
                delimiter.append(character)
            index += 1
            continue
        if character in {"'", '"'}:
            quote = character
            quoted = True
            index += 1
            continue
        if character == "\\" and index + 1 < len(word):
            index += 1
            delimiter.append(word[index])
            quoted = True
            index += 1
            continue
        delimiter.append(character)
        index += 1
    return "".join(delimiter), quoted


def heredoc_specs(
    line: str, initial_state: ShellLexicalState
) -> tuple[list[tuple[str, bool, bool]], ShellLexicalState]:
    specs: list[tuple[str, bool, bool]] = []
    mask, final_state, _continuations = shell_lexical_mask(line, initial_state)
    index = 0

    while index < len(line):
        if not mask[index]:
            index += 1
            continue
        if line.startswith("<<<", index):
            index += 3
            continue
        if line.startswith("<<", index):
            arithmetic_start = line.rfind("((", 0, index)
            arithmetic_end = line.rfind("))", 0, index)
            if arithmetic_start > arithmetic_end and "))" in line[index + 2 :]:
                index += 2
                continue
            match = HEREDOC.match(line, index)
            if match is None:
                raise AssertionError(
                    f"workflow run block contains unsupported heredoc syntax: {line[index:].rstrip()}"
                )
            delimiter, quoted = decode_heredoc_word(match.group("word"))
            specs.append(
                (
                    delimiter,
                    match.group("strip") == "-",
                    quoted,
                )
            )
            index = match.end()
            continue
        index += 1

    return specs, final_state


def strip_shell_line_comment(
    line: str,
    initial_state: ShellLexicalState,
    initial_word_start: bool,
    inside_conditional: bool,
    conditional_command_depth: int,
) -> tuple[str, ShellLexicalState, bool]:
    if (
        not inside_conditional
        and initial_state == ShellLexicalState()
        and initial_word_start
    ):
        comment_index = 0
        while comment_index < len(line) and line[comment_index] in " \t\r":
            comment_index += 1
        if comment_index < len(line) and line[comment_index] == "#":
            result = line[:comment_index]
            if line.endswith("\n"):
                result += "\n"
            return result, initial_state, True

    mask, final_state, continuations = shell_lexical_mask(line, initial_state)
    conditional_contexts = shell_conditional_contexts(
        line,
        initial_state,
        inside_conditional,
        conditional_command_depth,
    )
    result: list[str] = []
    word_start = initial_word_start
    index = 0
    while index < len(line):
        if index in continuations:
            index += 2
            continue
        character = line[index]
        if (
            character == "#"
            and mask[index]
            and not conditional_contexts[index]
            and word_start
        ):
            break
        result.append(character)
        if mask[index] and (character.isspace() or character in ";|&()<>"):
            word_start = True
        else:
            word_start = False
        index += 1

    if index < len(line):
        _mask, final_state, _continuations = shell_lexical_mask(
            "".join(result), initial_state
        )
    if index < len(line) and line.endswith("\n"):
        result.append("\n")
        word_start = True
    return "".join(result), final_state, word_start


def shell_control_token_at(line: str, index: int, token: str) -> bool:
    if not line.startswith(token, index):
        return False
    before = line[index - 1] if index else "\n"
    after_index = index + len(token)
    after = line[after_index] if after_index < len(line) else "\n"
    boundaries = " \t\r\n;|&(){}<>"
    return before in boundaries and after in boundaries


def shell_conditional_state(
    line: str,
    initial_state: ShellLexicalState,
    inside_conditional: bool,
    command_depth: int,
) -> tuple[bool, int]:
    inside_conditional, command_depth, _contexts = shell_conditional_scan(
        line, initial_state, inside_conditional, command_depth
    )
    return inside_conditional, command_depth


def shell_conditional_contexts(
    line: str,
    initial_state: ShellLexicalState,
    inside_conditional: bool,
    command_depth: int,
) -> list[bool]:
    _inside_conditional, _command_depth, contexts = shell_conditional_scan(
        line, initial_state, inside_conditional, command_depth
    )
    return contexts


def shell_conditional_scan(
    line: str,
    initial_state: ShellLexicalState,
    inside_conditional: bool,
    command_depth: int,
) -> tuple[bool, int, list[bool]]:
    mask, _final_state, _continuations = shell_lexical_mask(line, initial_state)
    contexts = [inside_conditional] * len(line)
    index = 0
    while index < len(line):
        contexts[index] = inside_conditional
        if not mask[index]:
            index += 1
            continue
        if line.startswith("$((", index):
            command_depth += 2
            index += 3
            continue
        if line.startswith("$(", index):
            command_depth += 1
            index += 2
            continue
        if command_depth:
            if line[index] == "(":
                command_depth += 1
            elif line[index] == ")":
                command_depth -= 1
            index += 1
            continue
        if shell_control_token_at(line, index, "[["):
            inside_conditional = True
            index += 2
            continue
        if shell_control_token_at(line, index, "]]"):
            inside_conditional = False
            index += 2
            continue
        index += 1
    return inside_conditional, command_depth, contexts


def strip_shell_comments(script: str) -> str:
    result: list[str] = []
    heredocs: list[tuple[str, bool, bool]] = []
    lexical_state = ShellLexicalState()
    word_start = True
    inside_conditional = False
    conditional_command_depth = 0

    for line in script.splitlines(keepends=True):
        if heredocs:
            delimiter, strip_tabs, _quoted = heredocs[0]
            candidate = line.rstrip("\r\n")
            if strip_tabs:
                candidate = candidate.lstrip("\t")

            if candidate == delimiter:
                heredocs.pop(0)
                result.append("\n" if line.endswith("\n") else "")
            else:
                result.append(line)
            continue

        initial_state = lexical_state
        executable_line, lexical_state, word_start = strip_shell_line_comment(
            line,
            initial_state,
            word_start,
            inside_conditional,
            conditional_command_depth,
        )
        inside_conditional, conditional_command_depth = shell_conditional_state(
            executable_line,
            initial_state,
            inside_conditional,
            conditional_command_depth,
        )
        line_heredocs, parsed_state = heredoc_specs(executable_line, initial_state)
        require(
            lexical_state == parsed_state,
            "shell comment and heredoc parsers must agree on quote state",
        )
        heredocs.extend(line_heredocs)
        result.append(executable_line)

    if heredocs:
        raise AssertionError(
            f"workflow run block contains an unterminated heredoc: {heredocs}"
        )

    return "".join(result)


def executable_run_text(workflow: dict) -> str:
    # This contract separates executable workflow text from documentation. It
    # intentionally detects literal policy markers in trusted workflow source;
    # it is not a shell interpreter or an untrusted-code sandbox.
    scripts: list[str] = []
    for job in workflow.get("jobs", {}).values():
        for step in job.get("steps", []):
            run = step.get("run")
            if not isinstance(run, str):
                continue
            scripts.append(strip_shell_comments(run))
    return "\n".join(scripts)


def validate_runner_free_external_ci_wait(workflow: dict, job_name: str) -> None:
    job = workflow["jobs"][job_name]
    scripts = executable_run_text({"jobs": {job_name: job}})
    endpoint = re.compile(
        r"commits/[^\s\"']+/(?:check-runs|status)(?:\?per_page=100)?")
    expected_queries = 1 if job_name == "privileged_merge" else 0
    require(len(endpoint.findall(scripts)) == expected_queries,
            f"{job_name} must contain exactly {expected_queries} external-CI endpoint snapshot(s) job-wide")

    # Endpoint spelling is not the trust boundary. Reject syntactically
    # infinite shell loops even when they poll through `gh pr checks`, a
    # variable, or another API spelling. A loop over an infinite condition is
    # allowed only when its own body carries an explicit terminating command;
    # this preserves the bounded fetch retry below while rejecting pure polls.
    infinite_loop = re.compile(
        r"(?:for\s*\(\(\s*;\s*;\s*\)\)|while\s+(?:true|:)|until\s+false)"
        r"\s*;?\s*do\b(?P<body>.*?)\bdone\b", re.I | re.S)
    for loop in infinite_loop.finditer(scripts):
        require(re.search(r"(?m)^\s*(?:break|return|exit)(?:\s|$)", loop.group("body")),
                f"{job_name} contains an unbounded loop with no terminating path")

    # Dynamically composing an external-CI endpoint is outside this contract:
    # the reserved `commits/<head>/(check-runs|status)` text is counted wherever
    # it appears, including variable assignments, and privileged_merge must also
    # retain this exact audited snapshot statement. Splitting the endpoint into
    # runtime fragments would remove that required statement and fail closed.
    allowed_snapshot = ('all_checks="$(gh api --paginate '
                        '"repos/$TARGET_REPO/commits/$EXPECTED_HEAD_SHA/check-runs?per_page=100"')
    if job_name == "privileged_merge":
        require(scripts.count(allowed_snapshot) == 1,
                "privileged_merge must retain its one audited literal CI snapshot")

    for step in job.get("steps", []):
        script = strip_shell_comments(step.get("run", ""))
        require(not re.search(r"\bsleep\b|ci-wait|MERGE_PROBE", script, re.I),
                f"{job_name} must not sleep or retain a polling-era CI wait")
        queries = list(endpoint.finditer(script))
        if not queries:
            continue
        prefix = script[:queries[0].start()]
        require(not re.search(r"(?m)^\s*(?:for\b|while\b|until\b)", prefix),
                f"{job_name} must not place its external-CI snapshot after a loop opener")


def authorization_app_token_uses(workflow: dict, job_name: str) -> str:
    steps = workflow["jobs"][job_name]["steps"]
    matches = [step.get("uses", "") for step in steps
               if step.get("name") == "Mint dedicated authorization App token"]
    require(len(matches) == 1,
            f"{job_name} must have exactly one dedicated authorization App token step")
    return matches[0]


def validate_authorization_app_token_inputs(workflow: dict, job_name: str) -> None:
    steps = workflow["jobs"][job_name]["steps"]
    token = next(step for step in steps
                 if step.get("name") == "Mint dedicated authorization App token")
    token_index = steps.index(token)
    inputs = token.get("with", {})
    require(inputs.get("client-id") == "${{ vars.AI_REVIEW_CLIENT_ID }}",
            f"{job_name} must mint with the dedicated App client ID")
    require("app-id" not in inputs,
            f"{job_name} token minting must reject the deprecated app-id input")
    guard = steps[token_index - 1] if token_index else {}
    guard_env = guard.get("env", {})
    require(guard.get("name") == "Validate GitHub App client ID" and
            str(guard.get("id", "")).startswith("generated_app_client_id_") and
            guard.get("shell") == "bash" and
            guard.get("run") == CLIENT_ID_GUARD_RUN and
            guard_env.get("APP_CLIENT_ID") == inputs.get("client-id") and
            guard_env.get("LC_ALL") == "C" and
            guard.get("if") == token.get("if"),
            f"{job_name} must bind the generated fail-closed guard to its token mint")


def validate_authorization_app_token_pins(uses_values: list[str]) -> None:
    require(all(IMMUTABLE_ACTION.fullmatch(uses) for uses in uses_values),
            "trusted authorization sites must use the official App-token action at a full lowercase 40-hex SHA")
    require(len(set(uses_values)) == 1,
            "trusted authorization sites must use the same immutable App-token action pin")


def validate_model_admission(steps: list[dict]) -> None:
    model_uses = [step.get("uses", "") for step in steps
                  if step.get("uses", "").startswith(f"{MODEL_ACTION_NAME}@")]
    require(model_uses and all(IMMUTABLE_MODEL_ACTION.fullmatch(uses)
                               for uses in model_uses),
            "every Claude review action must use a full lowercase 40-hex SHA")
    require(len(set(model_uses)) == 1 and model_uses[0] == MODEL_ACTION,
            "every Claude review pass must share the audited immutable action pin")
    model_indexes = [index for index, step in enumerate(steps)
                     if step.get("uses") == MODEL_ACTION]
    require(len(model_indexes) == 1, "review must retain exactly one automatic paid model pass")

    validation_indexes = [index for index, step in enumerate(steps)
                          if step.get("name") == "Validate trusted head authorization"]
    require(len(validation_indexes) == 1 and validation_indexes[0] < model_indexes[0],
            "receipt and pending-check validation must precede the model pass")
    validation = steps[validation_indexes[0]].get("run", "")
    verifier = "bash .gate-trust/scripts/ci-gate/verify-arm-receipt.sh"
    pending_check = 'check-runs/$AUTHORIZATION_CHECK_ID'
    require(verifier in validation and pending_check in validation and
            validation.index(verifier) < validation.index(pending_check) and
            "in_progress:" in validation and "exit 1" in validation,
            "trusted admission must verify the receipt, then require its exact pending App check")

    allowlists = []
    for index in model_indexes:
        allowed = steps[index].get("with", {}).get("allowed_bots", "")
        require("*" not in allowed, "model bot admission must never contain a wildcard")
        allowlists.append({login.strip() for login in allowed.split(",") if login.strip()})
    require(all(allowed == TRUSTED_BOTS for allowed in allowlists),
            "the model pass must retain exact source bot logins without normalized aliases")


def main() -> int:
    review_text = REVIEW.read_text(encoding="utf-8")
    rearm_text = REARM.read_text(encoding="utf-8")
    promote_text = PROMOTE.read_text(encoding="utf-8")
    retry_text = RETRY.read_text(encoding="utf-8")
    dispatch = load(DISPATCH)
    rearm_generator = (ROOT / "scripts/gen-gate-rearm-caller.sh").read_text(encoding="utf-8")
    promote_generator = (ROOT / "scripts/gen-privileged-merge-caller.sh").read_text(encoding="utf-8")
    verifier_text = (ROOT / "scripts/ci-gate/verify-arm-receipt.sh").read_text(encoding="utf-8")
    review = load(REVIEW)
    rearm = load(REARM)
    promote = load(PROMOTE)
    retry = load(RETRY)

    dispatch_inputs = dispatch[True]["workflow_dispatch"]["inputs"]
    environment_input = dispatch_inputs.get("ai_review_environment", {})
    require(
        environment_input.get("type") == "choice"
        and environment_input.get("options") == ["ai-review-app"],
        "direct dispatch must select only the main-only ai-review-app environment",
    )
    require(
        "contract_ref" not in dispatch_inputs,
        "direct dispatch must not override the reusable caller's canonical contract ref",
    )
    receipt_inputs = {"pr_number", "expected_head_sha", "authorization_check_id",
                      "arm_run_id", "arm_run_attempt"}
    require(receipt_inputs <= dispatch_inputs.keys() and
            all(dispatch_inputs[name].get("required") is True for name in receipt_inputs),
            "workflow_dispatch must require the exact head, App check, and arm-receipt identity")
    preflight = review["jobs"]["preflight"]
    direct_environment_guard = (
        "github.event_name != 'workflow_dispatch' || "
        "inputs.ai_review_environment == 'ai-review-app'"
    )
    compact_environment_guard = "".join(direct_environment_guard.split())
    public_call_guard = (
        "(" + "&&".join(f"github.event_name!='{event}'" for event in PUBLIC_PR_EVENTS)
        + "||github.event.repository.private==true)"
    )
    app_key_condition = "".join(str(review["jobs"]["app-key-policy"].get("if", "")).split())
    completion_condition = "".join(str(review["jobs"]["complete-authorization"].get("if", "")).split())
    expected_completion_guard = (
        "${{" + public_call_guard + "&&(always()&&inputs.authorization_check_id!=''&&("
        + compact_environment_guard
        + "))}}"
    )
    expected_app_key_guard = (
        "${{" + public_call_guard + "&&("
        + compact_environment_guard
        + ")}}"
    )
    require(
        app_key_condition == expected_app_key_guard
        and completion_condition == expected_completion_guard,
        "App-key jobs must reject public callers and direct dispatch to unapproved environments",
    )
    dispatch_guard = compact_expression(str(preflight.get("if", "")))
    dispatch_conjuncts = split_top_level(dispatch_guard, "&&")
    dispatch_disjunctions = split_top_level(dispatch_guard, "||")
    required_dispatch_guard = (
        "(github.event_name!='workflow_dispatch'||("
        "github.ref=='refs/heads/main'&&"
        "github.event.repository.default_branch=='main'&&"
        "github.ref_protected&&"
        "inputs.ai_review_environment=='ai-review-app'))"
    )
    require(
        len(dispatch_conjuncts) == 2
        and dispatch_conjuncts[0] == public_call_guard
        and dispatch_conjuncts[1].startswith("(")
        and dispatch_conjuncts[1].endswith(")")
        and len(dispatch_disjunctions) == 1,
        "direct dispatch must be a protected-main conjunct with no trailing OR bypass",
    )
    dispatch_context_terms = split_top_level(dispatch_conjuncts[1][1:-1], "&&")
    require(
        len(dispatch_context_terms) == 3
        and dispatch_context_terms[0] == "github.event_name!='pull_request_target'"
        and dispatch_context_terms[1] == required_dispatch_guard,
        "direct dispatch must remain bound to protected main",
    )
    pull_request_terms = split_top_level(dispatch_context_terms[2][1:-1], "||")
    required_pull_request_guard = (
        "(((github.event.action!='labeled'&&github.event.action!='unlabeled')||"
        "(github.event.action=='labeled'&&github.event.label.name=='re-review')||"
        "(github.event.action=='unlabeled'&&(github.event.label.name=='hold'||"
        "github.event.label.name=='DO NOT MERGE')))&&"
        "!github.event.pull_request.draft&&"
        "!contains(github.event.pull_request.labels.*.name,'hold')&&"
        "!contains(github.event.pull_request.labels.*.name,'DO NOT MERGE')&&"
        "needs.title-policy.outputs.title_held!='true')"
    )
    require(
        len(pull_request_terms) == 2
        and pull_request_terms[0] == "github.event_name=='workflow_dispatch'"
        and pull_request_terms[1] == required_pull_request_guard,
        "pull-request eligibility must not be weakened by a disjunctive bypass",
    )
    require(
        'DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}' in rearm_text
        and "AI_REVIEW_DISPATCH_WORKFLOW: ${{ github.repository == 'Verjson/.github' && 'ai-review-merge-dispatch.yml' || 'ai-review-merge.yml' }}" in rearm_text
        and 'gh workflow run "$AI_REVIEW_DISPATCH_WORKFLOW" --repo "$TARGET_REPO" --ref "$DEFAULT_BRANCH"' in rearm_text,
        "trusted dispatcher must explicitly target the repository default branch",
    )
    expected_run_name = (
        "AI review authorization ${{ inputs.authorization_check_id }} "
        "from arm ${{ inputs.arm_run_id }}.${{ inputs.arm_run_attempt }}"
    )

    def validate_run_name(document: dict) -> None:
        require(
            document.get("run-name") == expected_run_name,
            "review run name must bind authorization check and exact arm receipt",
        )

    validate_run_name(review)
    for invalid in (
        None,
        "AI review",
        "AI review authorization ${{ inputs.pr_number }} from arm ${{ inputs.arm_run_id }}.${{ inputs.arm_run_attempt }}",
        "AI review authorization ${{ inputs.authorization_check_id }} from arm ${{ inputs.pr_number }}.${{ inputs.arm_run_attempt }}",
        expected_run_name + " " + expected_run_name,
    ):
        mutation = dict(review)
        if invalid is None:
            mutation.pop("run-name")
        else:
            mutation["run-name"] = invalid
        try:
            validate_run_name(mutation)
        except AssertionError:
            pass
        else:
            raise AssertionError(f"invalid review run name was accepted: {invalid!r}")
    gate_steps = review["jobs"]["gate"]["steps"]
    validate_runner_free_external_ci_wait(review, "gate")
    validate_runner_free_external_ci_wait(promote, "privileged_merge")
    validate_model_admission(gate_steps)

    mutated_steps = [dict(step) for step in gate_steps]
    first_model = next(index for index, step in enumerate(mutated_steps)
                       if step.get("uses") == MODEL_ACTION)
    mutated_steps[first_model] = {
        **mutated_steps[first_model],
        "with": {**mutated_steps[first_model]["with"], "allowed_bots": "*"},
    }
    try:
        validate_model_admission(mutated_steps)
    except AssertionError:
        pass
    else:
        raise AssertionError("wildcard bot mutation escaped model admission contract")

    for invalid_uses in (f"{MODEL_ACTION_NAME}@v1",
                         f"{MODEL_ACTION_NAME}@{'a' * 40}"):
        mutated_pin_steps = [dict(step) for step in gate_steps]
        mutated_pin_steps[first_model] = {
            **mutated_pin_steps[first_model], "uses": invalid_uses,
        }
        try:
            validate_model_admission(mutated_pin_steps)
        except AssertionError:
            continue
        raise AssertionError(f"mutable or divergent Claude action pin escaped: {invalid_uses}")

    normalized_alias_steps = [dict(step) for step in gate_steps]
    normalized_alias_steps[first_model] = {
        **normalized_alias_steps[first_model],
        "with": {
            **normalized_alias_steps[first_model]["with"],
            "allowed_bots": "renovate,mend,github-actions",
        },
    }
    try:
        validate_model_admission(normalized_alias_steps)
    except AssertionError:
        pass
    else:
        raise AssertionError("normalized aliases escaped the source-login bot contract")

    validation_index = next(index for index, step in enumerate(gate_steps)
                            if step.get("name") == "Validate trusted head authorization")
    missing_verifier_steps = [dict(step) for step in gate_steps]
    missing_verifier_steps[validation_index] = {
        **missing_verifier_steps[validation_index],
        "run": missing_verifier_steps[validation_index]["run"].replace(
            "bash .gate-trust/scripts/ci-gate/verify-arm-receipt.sh", "true"),
    }
    reordered_steps = [dict(step) for step in gate_steps]
    validation_step = reordered_steps.pop(validation_index)
    reordered_steps.insert(first_model + 1, validation_step)
    for mutation, message in (
            (missing_verifier_steps, "missing receipt verifier"),
            (reordered_steps, "post-model receipt validation")):
        try:
            validate_model_admission(mutation)
        except AssertionError:
            continue
        raise AssertionError(f"{message} mutation escaped model admission contract")

    caller_sha = "1" * 40
    canonical_sha = "2" * 40
    workflow_call = review[True]["workflow_call"]
    require(workflow_call["inputs"]["contract_ref"] == {
        "description": "Immutable Verjson/.github revision; must equal the reusable workflow uses pin",
        "required": True,
        "type": "string",
    }, "consumer call must supply its canonical reusable-workflow revision")

    preflight = review["jobs"]["preflight"]
    revision = next(step for step in preflight["steps"]
                    if step.get("name") == "Resolve immutable canonical workflow revision")
    require(revision["env"] == {
        "CONTRACT_REF": "${{ inputs.contract_ref }}",
        "EXECUTING_WORKFLOW_SHA": "${{ github.workflow_sha }}",
    }, "trusted revision resolver must separate the caller head from the canonical input")
    require(preflight["outputs"]["trusted_review_sha"] ==
            "${{ steps.trusted-revision.outputs.sha }}",
            "validated canonical revision must be shared with downstream verifier jobs")

    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "output"
        environment = {
            **os.environ,
            "CONTRACT_REF": canonical_sha,
            "EXECUTING_WORKFLOW_SHA": caller_sha,
            "GITHUB_OUTPUT": str(output),
        }
        result = subprocess.run(
            ["bash", "-c", revision["run"]],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        require(result.returncode == 0 and output.read_text().strip() == f"sha={canonical_sha}",
                "consumer-call fixture substituted its consumer head for the canonical revision")

        for invalid in ("main", "A" * 40, "3" * 39):
            environment["CONTRACT_REF"] = invalid
            result = subprocess.run(
                ["bash", "-c", revision["run"]],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            require(result.returncode != 0,
                    f"malformed canonical revision escaped: {invalid}")

    policy_checkout = next(step for step in preflight["steps"]
                           if step.get("name") == "Check out immutable policy decoder")
    require(policy_checkout["with"]["repository"] == "Verjson/.github" and
            policy_checkout["with"]["ref"] == "${{ steps.trusted-revision.outputs.sha }}",
            "consumer policy checkout must pair Verjson/.github with the validated canonical revision")
    for job_name in ("gate", "complete-authorization"):
        job = review["jobs"][job_name]
        resolver = next(step for step in job["steps"]
                        if step.get("name") == "Resolve executing trusted workflow revision")
        require(resolver["env"] == {
            "TRUSTED_REVIEW_SHA": "${{ needs.preflight.outputs.trusted_review_sha }}"
        }, f"{job_name} must consume the preflight-validated canonical revision")
        checkout = next(step for step in job["steps"]
                        if step.get("name") in {
                            "Check out immutable arm verifier",
                            "Check out immutable policy decoder",
                        })
        require(checkout["with"]["repository"] == "Verjson/.github" and
                checkout["with"]["ref"] == "${{ steps.trusted-revision.outputs.sha }}",
                f"{job_name} verifier checkout is not bound to Verjson/.github at the canonical revision")
        if job_name == "complete-authorization":
            require("always()" in job.get("if", ""),
                    "completion must retain its terminal always-running behavior")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            for trusted_ref, expected_success in (
                (canonical_sha, True),
                ("", False),
                ("main", False),
                ("A" * 40, False),
                ("3" * 39, False),
            ):
                output.write_text("", encoding="utf-8")
                result = subprocess.run(
                    ["bash", "-c", resolver["run"]],
                    env={
                        **os.environ,
                        "TRUSTED_REVIEW_SHA": trusted_ref,
                        "GITHUB_OUTPUT": str(output),
                    },
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if expected_success:
                    require(result.returncode == 0 and
                            output.read_text(encoding="utf-8").strip() ==
                            f"sha={canonical_sha}",
                            f"{job_name} rejected the validated canonical revision")
                else:
                    require(result.returncode != 0 and
                            not output.read_text(encoding="utf-8").strip(),
                            f"{job_name} exposed an empty or malformed canonical revision")

    require("pull_request_target" not in review.get(True, {}),
            "model workflow must not run in pull_request_target context")
    require("pull_request" not in review.get(True, {}),
            "model workflow must run only after the trusted arm deduplicates a head")
    label_rearm = load(ROOT / ".github/workflows/ai-review-label-rearm.yml")
    require(set(rearm[True]["pull_request_target"]["types"]) ==
            {"opened", "reopened", "synchronize"} and
            "labeled" not in rearm[True]["pull_request_target"]["types"] and
            label_rearm[True] == {"pull_request_target": {"types": ["labeled"]}},
            "trusted rearm must separate explicit label delivery from head transitions")
    arm = rearm["jobs"]["arm"]
    arm_step = next(step for step in arm["steps"] if step.get("name") == "Create exact-head authorization receipt")
    arm_script = arm_step["run"]
    require(arm["permissions"].get("checks") == "write", "trusted arm must own its Actions check")
    require(arm_step["env"].get("GH_TOKEN") == "${{ github.token }}", "arm check must be created by GitHub Actions")
    require(not any(step.get("name") == "Mint dedicated authorization App token" for step in arm["steps"]),
            "arm must validate credentials before any App token is minted")
    require("--argjson app_id 15368" in arm_script and "--arg slug github-actions" in arm_script and ".app.id == $app_id" in arm_script and ".app.slug == $slug" in arm_script,
            "arm must bind the authorization check to the GitHub Actions App")
    dispatch_step = next(step for step in arm["steps"] if step.get("name") == "Dispatch trusted review after receipt publication")
    receipt_step = next(step for step in arm["steps"] if step.get("name") == "Upload immutable arm receipt")
    require(arm_script.index('gh api --method POST "repos/$TARGET_REPO/check-runs"') <
            arm_script.index('if [ "$APP_KEY_POLICY_RESULT" != success ] || [ "$AI_REVIEW_KEY_VALIDATION_RESULT" != success ]; then') and
            arm["steps"].index(receipt_step) < arm["steps"].index(dispatch_step),
            "arm must create a failure-visible check, enforce pinned key policy, publish the receipt, then dispatch")

    app_token_uses = [authorization_app_token_uses(review, "complete-authorization")]
    validate_authorization_app_token_pins(app_token_uses)
    validate_authorization_app_token_inputs(review, "complete-authorization")

    workflow, job_name = review, "complete-authorization"
    mutated = yaml.safe_load(yaml.safe_dump(workflow))
    token = next(step for step in mutated["jobs"][job_name]["steps"]
                 if step.get("name") == "Mint dedicated authorization App token")
    token["with"]["app-id"] = token["with"].pop("client-id")
    try:
        validate_authorization_app_token_inputs(mutated, job_name)
    except AssertionError:
        pass
    else:
        raise AssertionError(f"{job_name} legacy app-id mutation escaped token-input contract")

    valid_pin = f"{APP_TOKEN_ACTION}@{'a' * 40}"
    invalid_pin_sets = (
        [valid_pin.replace(APP_TOKEN_ACTION, "attacker/create-github-app-token"), valid_pin],
        [f"{APP_TOKEN_ACTION}@v3", f"{APP_TOKEN_ACTION}@v3"],
        [f"{APP_TOKEN_ACTION}@{'a' * 12}", f"{APP_TOKEN_ACTION}@{'a' * 12}"],
        [valid_pin, f"{APP_TOKEN_ACTION}@{'b' * 40}"],
    )
    for invalid_pins in invalid_pin_sets:
        try:
            validate_authorization_app_token_pins(invalid_pins)
        except AssertionError:
            continue
        raise AssertionError(f"mutation escaped App-token pin contract: {invalid_pins}")
    require(review["jobs"]["complete-authorization"]["permissions"].get("checks") == "write",
            "completion must retain Checks write permission for the owner-selected terminalizer")
    require(all(job.get("permissions", {}).get("checks") != "write"
                for name, job in rearm["jobs"].items() if name != "arm"),
            "only the trusted arm may create and terminalize authorization checks")
    require(all(job.get("permissions", {}).get("checks") != "write"
                for name, job in review["jobs"].items() if name != "complete-authorization"),
            "only completion may write the authorization check")
    require(all(job.get("permissions", {}).get("checks") != "write"
                for job in promote["jobs"].values()),
            "promotion jobs must not receive Checks write permission")
    completion_steps = review["jobs"]["complete-authorization"]["steps"]
    app_token = next(step for step in completion_steps
                     if step.get("name") == "Mint dedicated authorization App token")
    require("permission-checks" not in app_token["with"] and
            app_token["with"].get("permission-contents") == "read" and
            app_token["with"].get("permission-pull-requests") == "write",
            "dedicated App token must have only the required content and pull-request permissions")
    require('check-runs/$AUTHORIZATION_CHECK_ID' in review_text,
            "review must complete the exact check-run supplied by the trusted arm")
    require('head_sha:$sha' in rearm_text and '--arg sha "$head_sha"' in rearm_text,
            "authorization check must be bound to the exact current PR head")
    require("APP_ID: ${{ vars.AI_REVIEW_APP_ID }}" in rearm_text and
            "EXPECTED_APP_ID: ${{ vars.AI_REVIEW_APP_ID }}" in review_text and
            "check_app_id" in rearm_text and "check_app_id" in verifier_text and
            ".app.id == 15368" in rearm_text and ".app.id == $check_app_id" in verifier_text and
            ".check_app_id == 15368" in verifier_text and
            ".check_app_id // .app_id" not in verifier_text,
            "the receipt must bind the Actions-owned check and AI App approval identities separately")
    require("AI review authorization" in rearm_text and "AI review authorization" in promote_text,
            "arm and promotion must agree on the unambiguous required-check name")
    terminal_merge_text = TERMINAL_MERGE.read_text(encoding="utf-8")
    require("Hold autonomous terminal merge" in promote_text and "gh pr merge" not in terminal_merge_text and
            "exit 1" in terminal_merge_text,
            "trusted continuation must hold autonomous terminal merges")
    require("retention-days: 90" in rearm_text,
            "arm receipts must survive the supported long-lived hold window")
    require("REQUIRED_CHECK_POLICY" in promote_text and "vars.AI_REVIEW_REQUIRED_CHECKS" not in promote_text and
            "check-runs?per_page=100" in promote_text and "sort_by(.id) | last" in promote_text and
            ".workflow_id == $workflow_id" in promote_text and
            "actions/runs/$ci_run_id/jobs?per_page=100" in promote_text and
            ".check_suite.id" in promote_text and ".check_suite_id" in promote_text and
            ".check_run_url == $url" in promote_text and
            "head_workflow_blob" in promote_text and "trusted_workflow_blob" in promote_text,
            "terminal promotion must enforce latest-run App/workflow/revision-bound required CI")
    require("(.conclusion | ascii_upcase) == \"SUCCESS\"" in promote_text,
            "only successful authorization may permit terminal promotion")
    require(all(marker in verifier_text for marker in
                ('[ "$(<"$tmp/arm-workflow-id")" = "$arm_workflow_id" ]',
                 'actions/required_workflows/$arm_workflow_id',
                 'workflow_api arm-rules', '--paginate',
                 '.source_type == "Organization"', '(.source | ascii_downcase) == "verjson"',
                 '.repository_id == 1269388380', '.ref == "refs/heads/main"',
                 '.event == "pull_request_target"',
                 '.path == ".github/workflows/gate-rearm.yml"', ".external_id == $external_id",
                 "artifact_digest", "actual_zip_sha")),
            "authorization must bind local or organization-required arm provenance, receipt digest, run, and dedicated App")
    verifier_invocation = re.compile(
        r"(?m)^(?P<indent>\s*)(?:GH_TOKEN=\"\$ACTIONS_TOKEN\" )?"
        r"(?P<shell>bash )?\.gate-trust/scripts/ci-gate/verify-arm-receipt\.sh"
        r"(?: \|\| receipt_ok=false)?$"
    )
    invocations = [
        match for text in (review_text, promote_text)
        for match in verifier_invocation.finditer(text)
    ]
    require(len(invocations) == 3 and all(match.group("shell") == "bash " for match in invocations),
            "every sparse-checked-out arm verifier must be invoked explicitly with bash")
    require("headRefOid" in promote_text and "EXPECTED_HEAD_SHA" in promote_text,
            "promotion must reject a stale head")
    require("headRepositoryOwner" in rearm_text,
            "trusted arm must make an explicit fork policy decision")
    require("authorization_check_id" in review_text and "expected_head_sha" in review_text,
            "review dispatch must carry trusted head/check identities")
    require('--tools "Read,Grep,Glob"' in review_text and "--allowedTools" not in review_text,
            "secret-backed model review must not execute pull-request code")
    require("ai_review_environment: ai-review-app" in rearm_generator and "PRIVATE_KEY" not in rearm_generator and "checks: write" in rearm_generator and
            all(event in rearm_generator for event in ("opened", "synchronize", "reopened")),
            "generated arm callers must preserve head events and Checks permission without exposing App credentials")
    require("authorization_check_id" in promote_generator and
            "pull_request_target:" not in promote_generator,
            "generated promotion callers must accept only trusted explicit dispatches")

    forbidden = re.compile(r"\bsleep\b|MERGE_PROBE|ci-wait", re.I)
    for path, workflow in ((REVIEW, review), (PROMOTE, promote), (RETRY, retry)):
        require(
            not forbidden.search(executable_run_text(workflow)),
            f"{path.name} still contains runner-held waiting",
        )
    require(promote_text.count("check-runs?per_page=100") == 1,
            "promotion must read required checks once rather than poll")

    mutated_review = yaml.safe_load(yaml.safe_dump(review))
    mutated_review["jobs"]["gate"]["steps"].append(
        {"run": "for attempt in $(seq 1 20); do gh api commits/head/check-runs?per_page=100; sleep 30; done"})
    require(
        forbidden.search(executable_run_text(mutated_review)) is not None,
        "runner-held waiting detector must reject executable sleep",
    )

    mutated_comments = yaml.safe_load(yaml.safe_dump(review))
    mutated_comments["jobs"]["gate"]["steps"].append(
        {
            "run": "# sleep is forbidden here\n"
                   "# MERGE_PROBE and ci-wait were removed\n"
                   "printf '%s\\n' ready",
        }
    )
    require(
        forbidden.search(executable_run_text(mutated_comments)) is None,
        "runner-held waiting detector must ignore shell comments",
    )
    validate_runner_free_external_ci_wait(mutated_comments, "gate")
    for inline_comment in (
        "printf ok # sleep\n",
        "printf ok;# MERGE_PROBE\n",
        "printf ok;\\\n# ci-wait\n",
    ):
        documented_inline = yaml.safe_load(yaml.safe_dump(review))
        documented_inline["jobs"]["gate"]["steps"].append(
            {"run": inline_comment}
        )
        require(
            forbidden.search(executable_run_text(documented_inline)) is None,
            "inline comments at shell word boundaries must be ignored",
        )
        validate_runner_free_external_ci_wait(documented_inline, "gate")
    for literal_hash in (
        "printf foo#sleep\n",
        "printf '%s' '# sleep'\n",
        "printf '%s' \"${x#sleep}\"\n",
        "[[ x =~ (\n# sleep\n) ]]\n",
    ):
        require(
            forbidden.search(strip_shell_comments(literal_hash)) is not None,
            "non-comment hash markers must remain executable text",
        )
    for documentation_comment in (
        "# Don't use sleep here (runner-held waiting).\n",
        "# `sleep` and MERGE_PROBE were removed; ci-wait is obsolete.\n",
        "# " + ("documentation " * 4000) + "sleep\n",
    ):
        documented = yaml.safe_load(yaml.safe_dump(review))
        documented["jobs"]["gate"]["steps"].append(
            {"run": documentation_comment + "printf '%s\\n' ready"}
        )
        require(
            forbidden.search(executable_run_text(documented)) is None,
            "top-level documentation comments must allow natural prose",
        )
    require(
        "# sleep" in strip_shell_comments("printf '%s\\n' '# sleep'"),
        "shell comment stripping must preserve hash characters inside quotes",
    )
    require(
        "sleep 30" in strip_shell_comments('printf %s "text\n#"; sleep 30\n'),
        "multiline double quotes must not hide executable waiting",
    )
    require(
        "sleep 30" in strip_shell_comments("printf %s 'text\n#'; sleep 30\n"),
        "multiline single quotes must not hide executable waiting",
    )
    require(
        "# sleep" in strip_shell_comments("printf %s $'foo\\\'\n# sleep\nbar'\n"),
        "ANSI-C escaped quotes must preserve multiline quoted markers",
    )
    require(
        "# sleep" in strip_shell_comments(
            'printf %s "${x:-"foo\n# sleep\nbar"}"\n'
        ),
        "nested parameter quotes must preserve multiline quoted markers",
    )
    require(
        "sleep 30" in strip_shell_comments("printf %s foo\\\n#bar; sleep 30\n"),
        "line continuations must preserve the shell word boundary before hash",
    )
    mutated_continuation = yaml.safe_load(yaml.safe_dump(review))
    mutated_continuation["jobs"]["gate"]["steps"].append(
        {"run": "printf %s foo\\\n#bar; sleep 30\n"}
    )
    require(
        forbidden.search(executable_run_text(mutated_continuation)) is not None,
        "runner-held waiting detector must reject waits after a continued word",
    )
    for script, description in (
        ("printf %s $'foo\\' # still quoted'; sleep 30\n", "ANSI-C quotes"),
        ('printf %s "${x:-"foo # still nested"}"; sleep 30\n', "nested expansions"),
        ("[[ x =~ (#$(sleep 30)) ]]\n", "conditional regular expressions"),
        (
            "[[ x =~ (\n# $(sleep 30)\n) ]]\n",
            "multiline conditional regular expressions",
        ),
        (
            "[[ x =~ (\\\n#$(sleep 30)) ]]\n",
            "continued conditional regular expressions",
        ),
    ):
        mutated_complex_quote = yaml.safe_load(yaml.safe_dump(review))
        mutated_complex_quote["jobs"]["gate"]["steps"].append({"run": script})
        require(
            forbidden.search(executable_run_text(mutated_complex_quote)) is not None,
            f"runner-held waiting detector must fail closed for {description}",
        )
    require(
        "sleep 30" in strip_shell_comments("count=${#items}; sleep 30\n"),
        "parameter-length expansion must not hide executable waiting",
    )
    require(
        "# sleep" in strip_shell_comments("[[ x =~ (\n# sleep\n) ]]\n"),
        "multiline conditional regular expressions must remain executable text",
    )
    require(
        "# sleep" in strip_shell_comments(
            "[[ x =~ (\n$(printf ]])\n# sleep\n) ]]\n"
        ),
        "nested substitutions must not close multiline conditional state",
    )
    require(
        "sleep 0" in strip_shell_comments("(( x << 1 )); sleep 0\n"),
        "arithmetic shifts must not be parsed as heredocs",
    )
    require(
        "sleep 30" in strip_shell_comments("cat <<\\EOF\nsleep 30\nEOF\n"),
        "escaped heredoc delimiters must retain their bodies",
    )
    for heredoc_source in (
        "cat <<'END-MARKER'\nsleep 30\nEND-MARKER\n",
        "cat <<'E''OF'\nsleep 30\nEOF\n",
    ):
        require(
            "sleep 30" in strip_shell_comments(heredoc_source),
            "punctuated and concatenated quoted heredocs must retain their bodies",
        )
    require(
        "sleep 30" in strip_shell_comments("cat <<EOF\n# $(sleep 30)\nEOF\n"),
        "unquoted heredoc expansion must remain executable to the detector",
    )
    require(
        "sleep 30" in strip_shell_comments("cat <<'EOF'\n# $(sleep 30)\nEOF\n"),
        "quoted heredoc data must remain visible to fail-closed waiting detection",
    )
    mutated_quoted_heredoc = yaml.safe_load(yaml.safe_dump(review))
    mutated_quoted_heredoc["jobs"]["gate"]["steps"].append(
        {"run": "bash <<'EOF'\nsleep 30\nEOF\n"}
    )
    require(
        forbidden.search(executable_run_text(mutated_quoted_heredoc)) is not None,
        "runner-held waiting detector must reject executable quoted heredoc bodies",
    )
    mutated_promote = yaml.safe_load(yaml.safe_dump(promote))
    mutated_promote["jobs"]["privileged_merge"]["steps"].append(
        {"run": "until gh api commits/head/status?per_page=100; do sleep 30; done"})
    mutated_infinite = yaml.safe_load(yaml.safe_dump(review))
    mutated_infinite["jobs"]["gate"]["steps"].append(
        {"run": "for ((;;)); do gh api commits/head/check-runs; done"})
    mutated_second_step = yaml.safe_load(yaml.safe_dump(promote))
    mutated_second_step["jobs"]["privileged_merge"]["steps"].append(
        {"run": "gh api commits/head/check-runs"})
    mutated_multiline = yaml.safe_load(yaml.safe_dump(promote))
    promotion_step = next(step for step in mutated_multiline["jobs"]["privileged_merge"]["steps"] if step.get("name") == "Authorize terminal merge from trusted metadata")
    promotion_script = promotion_step["run"]
    promotion_step["run"] = (
        "while\n  true\ndo\n" + promotion_script + "\ndone")
    mutated_indirect = yaml.safe_load(yaml.safe_dump(promote))
    mutated_indirect["jobs"]["privileged_merge"]["steps"].append(
        {"run": 'prefix="repos/$TARGET_REPO/com"\n'
                'suffix="mits/head/check-runs"\nendpoint="$prefix$suffix"\n'
                'while true; do gh api "$endpoint"; done'})
    mutated_pr_checks = yaml.safe_load(yaml.safe_dump(review))
    mutated_pr_checks["jobs"]["gate"]["steps"].append(
        {"run": "while true; do gh pr checks 7; done"})
    mutated_until_false = yaml.safe_load(yaml.safe_dump(review))
    mutated_until_false["jobs"]["gate"]["steps"].append(
        {"run": "until\nfalse\ndo\ngh pr checks 7\ndone"})
    for workflow, job_name in ((mutated_review, "gate"),
                               (mutated_promote, "privileged_merge"),
                               (mutated_infinite, "gate"),
                               (mutated_second_step, "privileged_merge"),
                               (mutated_multiline, "privileged_merge"),
                               (mutated_indirect, "privileged_merge"),
                               (mutated_pr_checks, "gate"),
                               (mutated_until_false, "gate")):
        try:
            validate_runner_free_external_ci_wait(workflow, job_name)
        except AssertionError:
            continue
        raise AssertionError(f"{job_name} polling-loop mutation escaped the structural contract")

    require(set(retry[True]) == {"workflow_run", "workflow_call"} and
            retry[True]["workflow_run"]["types"] == ["completed"],
            "CI retry must be callable by generated consumers and directly reachable only from completed deterministic workflows")
    require("ai-review-merge.yml" not in retry_text and MODEL_ACTION not in retry_text and
            "ai-privileged-merge.yml" in retry_text,
            "CI completion path must be structurally unable to invoke paid review")
    require("AI review authorization" in retry_text and "EXPECTED_APP_ID" in retry_text and
            "EXPECTED_APP_SLUG" in retry_text and "HEAD_SHA" in retry_text,
            "CI completion retry must rediscover exact-head dedicated-App evidence")
    require("--retry" in promote_generator and "ai-promotion-retry.yml" in promote_generator and
            "workflow_run:" in promote_generator and RETRY.name in promote_generator,
            "privileged caller generator must also emit the consumer CI-completion bridge")

    for path, data in ((REARM, rearm), (PROMOTE, promote)):
        for job in data["jobs"].values():
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                if uses.startswith("actions/checkout"):
                    require(step.get("with", {}).get("ref") in ("main", "${{ github.event.repository.default_branch }}", "${{ steps.trusted-revision.outputs.sha }}"),
                            f"{path.name} may only checkout trusted base code")

    require("Required check configuration" in promote_text,
            "workflow must carry operator instructions for required-check identity migration")
    print("PASS: event-driven head authorization and terminal promotion contract")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)

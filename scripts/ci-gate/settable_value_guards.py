"""Find every shell character-class guard in workflows, callers, and generators.

Bash `[[ =~ ]]`, `[[ == ]]` and `case` globs, and `grep` evaluate bracket
ranges and POSIX classes through the locale. Under en_US.UTF-8 `[a-z]` admits
`é` and fullwidth `ａ`, and `[0-9]` admits `٣`. A guard is canonical only when
every bracket expression it uses lists its ASCII characters literally.

The rule applies to every guard, whatever its subject. A per-subject rule
would need to trace each value back to an organization variable, an input, a
pull-request field, or an API response, through shell copies, GITHUB_OUTPUT,
GITHUB_ENV, and job outputs. Each hop is a place for a tracer to miss a guard.
Requiring every guard to be canonical needs no tracing, so no hop can be
missed. Spelling a class out never changes which ASCII characters it admits,
so system-issued values lose nothing.

A guard shape the scanner cannot read is reported as unclassifiable rather
than skipped. That covers a pattern held in an unquoted variable and an
unterminated `case`.
"""

from dataclasses import dataclass
from pathlib import Path
import re

import yaml


# Whitespace classes separate tokens in structural matches; they never decide
# which characters a value may contain, so they are admitted. Every other POSIX
# class ([:alpha:], [:alnum:], [:digit:], ...) is as locale-dependent as a range.
ASCII_WHITESPACE_CLASSES = frozenset({"space", "blank"})


@dataclass(frozen=True)
class Guard:
    source: str
    kind: str
    subject: str
    pattern: str
    verdict: str  # canonical | collation | unclassifiable
    resolved: str = None  # the regex text after held literals are substituted

    @property
    def key(self):
        return f"{self.source} {self.kind} {self.subject} {self.pattern}"


def strip_quoted(text):
    """Replace quoted segments with a neutral literal; they match themselves."""
    out = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            out.append(text[index:index + 2])
            index += 2
            continue
        if char in "'\"":
            close = text.find(char, index + 1)
            if close < 0:
                return None
            segment = text[index + 1:close]
            if char == '"' and re.search(r"\$\(", segment):
                return None
            out.append("Q")
            index = close + 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def bracket_expressions(pattern):
    """Return the bracket expressions in a regex or glob, or None if unreadable."""
    found = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if char != "[":
            index += 1
            continue
        end = index + 1
        if end < len(pattern) and pattern[end] in "^!":
            end += 1
        if end < len(pattern) and pattern[end] == "]":
            end += 1
        while end < len(pattern) and pattern[end] != "]":
            if pattern[end] == "[" and end + 1 < len(pattern) and pattern[end + 1] in ":=.":
                close = pattern.find(pattern[end + 1] + "]", end + 2)
                if close < 0:
                    return None
                end = close + 2
                continue
            end += 1
        if end >= len(pattern):
            return None
        found.append(pattern[index:end + 1])
        index = end + 1
    return found


def collation_ordered(expression):
    """True when a bracket expression needs the locale to decide membership."""
    body = expression[1:-1]
    if body[:1] in ("^", "!"):
        body = body[1:]
    classes = re.findall(r"\[([:=.])(.*?)\1\]", body)
    if any(kind != ":" or name not in ASCII_WHITESPACE_CLASSES for kind, name in classes):
        return True
    body = re.sub(r"\[([:=.]).*?\1\]", "", body)
    if any(ord(char) > 127 for char in body):
        return True
    inner = body[1:-1] if body.startswith("]") else body[1:-1]
    return "-" in inner


def classify(pattern):
    literal = strip_quoted(pattern)
    if literal is None or re.search(r"\$[{(A-Za-z_]", literal):
        return "unclassifiable"
    expressions = bracket_expressions(literal)
    if expressions is None:
        return "unclassifiable"
    return "collation" if any(collation_ordered(e) for e in expressions) else "canonical"


def _test_operands(line):
    """Yield (subject, operator, pattern) for each `[[ x OP y ]]` comparison.

    A pattern is one shell word, so it ends at the first unescaped blank.
    """
    for match in re.finditer(r"\[\[\s+(.*?)\s+\]\]", line):
        for operand in re.finditer(
            r"(\S+)\s+(=~|==|!=)\s+((?:\\.|[^\s\\])+)", match.group(1)
        ):
            yield operand.group(1), operand.group(2), operand.group(3)


def _case_labels(block):
    """Yield the label of every arm in a case body (text after `in` up to esac)."""
    index = 0
    length = len(block)
    while index < length:
        while index < length and block[index] in " \t\n":
            index += 1
        if index >= length:
            return
        if block[index] == "(":
            index += 1
        start = index
        quote = None
        while index < length:
            char = block[index]
            if quote:
                if char == quote:
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == "\\":
                index += 1
            elif char == ")":
                break
            index += 1
        yield block[start:index].strip()
        depth = 0
        while index < length:
            word = re.match(r"(case|esac)\b", block[index:])
            if word and (index == 0 or not (block[index - 1].isalnum() or block[index - 1] in "_-")):
                depth += 1 if word.group(1) == "case" else -1
                index += len(word.group(1))
                continue
            if depth == 0 and block.startswith(";;", index):
                index += 2
                if index < length and block[index] == "&":
                    index += 1
                break
            index += 1


def _case_blocks(script):
    for match in re.finditer(r"(?<![\w-])case\s+(\S+)\s+in\b", script):
        depth = 1
        position = match.end()
        for word in re.finditer(r"(?<![\w-])(case|esac)\b", script[position:]):
            depth += 1 if word.group(1) == "case" else -1
            if depth == 0:
                yield match.group(1), script[position:position + word.start()]
                break
        else:
            yield match.group(1), None


GREP = re.compile(
    r"(?<![\w-])grep\b((?:\s+-[A-Za-z]+|\s+--[a-z-]+(?:=\S+)?)*)\s+(?:-e\s+)?"
    r"('[^']*'|\"[^\"]*\"|\S+)"
)


def _regex_variables(script):
    """Map each name assigned exactly once, from one single-quoted literal."""
    assignments = {}
    for name, value in re.findall(
        r"(?:^|[;\s])(?:local\s+|readonly\s+)?(" + r"[A-Za-z_][A-Za-z0-9_]*" + r")='([^']*)'(?=\s|;|$)",
        script,
        re.M,
    ):
        assignments.setdefault(name, []).append(value)
    return {name: values[0] for name, values in assignments.items() if len(values) == 1}


def guards_in_script(source, script):
    """Yield every bracket-using guard in one shell script."""
    held = _regex_variables(script)
    for line in script.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for subject, operator, pattern in _test_operands(line):
            if operator == "=~" and "$" in pattern:
                # An unquoted expansion in a regex is regex text, so a fragment
                # held in a single-assignment literal is judged as written.
                references = re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", pattern)
                if references and all(name in held for name in references):
                    literal = re.sub(
                        r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?",
                        lambda match: held[match.group(1)],
                        pattern,
                    )
                    yield Guard(
                        source, "regex", subject, pattern,
                        _classify_single_quoted(literal), literal,
                    )
                    continue
            if operator != "=~":
                literal = strip_quoted(pattern)
                if literal is None or "[" not in literal:
                    continue
            elif "[" not in pattern and "$" not in pattern:
                continue
            kind = "regex" if operator == "=~" else "glob"
            yield Guard(source, kind, subject, pattern, classify(pattern))
    for subject, block in _case_blocks(script):
        if block is None:
            yield Guard(source, "case", subject, "<unterminated>", "unclassifiable")
            continue
        for label in _case_labels(block):
            literal = strip_quoted(label)
            if literal is not None and "[" not in literal and not re.search(r"\$[{(A-Za-z_]", literal):
                continue
            yield Guard(source, "case", subject, label, classify(label))
    for line in script.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for match in GREP.finditer(line):
            flags, pattern = match.group(1), match.group(2)
            if re.search(r"(?:^|\s)-[A-Za-z]*F", flags) or "--fixed-strings" in flags:
                continue
            if "[" not in pattern and "$" not in pattern:
                continue
            if pattern.startswith("'"):
                verdict = _classify_single_quoted(pattern[1:-1])
            else:
                verdict = classify(pattern)
            yield Guard(source, "grep", "<grep>", pattern, verdict)


def _classify_single_quoted(pattern):
    """A single-quoted grep pattern is literal shell text: `$` is an anchor."""
    expressions = bracket_expressions(pattern)
    if expressions is None:
        return "unclassifiable"
    return "collation" if any(collation_ordered(e) for e in expressions) else "canonical"


def run_scripts(document):
    """Yield every run script in a workflow or composite action document."""
    jobs = list((document.get("jobs") or {}).values())
    if "runs" in document:
        jobs.append({"steps": document["runs"].get("steps") or []})
    for job in jobs:
        for step in job.get("steps") or []:
            if isinstance(step.get("run"), str):
                yield step["run"]


def scan_workflow_text(source, text):
    document = yaml.safe_load(text) or {}
    for script in run_scripts(document):
        yield from guards_in_script(source, script)


def shell_outside_heredocs(text):
    """Return a generator script's own shell lines, blanking heredoc bodies."""
    lines = text.splitlines()
    kept = []
    terminator = None
    strip_tabs = False
    for line in lines:
        if terminator is not None:
            candidate = line.lstrip("\t") if strip_tabs else line
            if candidate == terminator:
                terminator = None
            kept.append("")
            continue
        kept.append(line)
        heredoc = re.search(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2", line)
        if heredoc and "<<<" not in line[: heredoc.start() + 3]:
            terminator = heredoc.group(3)
            strip_tabs = heredoc.group(1) == "-"
    return "\n".join(kept)


def scan_repository(root):
    root = Path(root)
    files = sorted((root / ".github/workflows").glob("*.yml"))
    files += sorted((root / ".github/actions").glob("*/action.yml"))
    for file in files:
        yield from scan_workflow_text(file.relative_to(root).as_posix(), file.read_text(encoding="utf-8"))
    for file in sorted((root / "scripts").glob("gen-*.sh")):
        source = file.relative_to(root).as_posix()
        yield from guards_in_script(source, shell_outside_heredocs(file.read_text(encoding="utf-8")))

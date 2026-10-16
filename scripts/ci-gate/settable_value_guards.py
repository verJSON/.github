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
    if re.search(r"\$['\"]", text):
        return None
    out = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            out.append(text[index:index + 2])
            index += 2
            continue
        if char in "'\"":
            close = index + 1
            while close < len(text) and text[close] != char:
                close += 2 if char == '"' and text[close] == "\\" else 1
            if close >= len(text):
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
    return "-" in body[1:-1]


def classify(pattern):
    literal = strip_quoted(pattern)
    if literal is None or re.search(r"\$[{(A-Za-z_]", literal):
        return "unclassifiable"
    expressions = bracket_expressions(literal)
    if expressions is None:
        return "unclassifiable"
    return "collation" if any(collation_ordered(e) for e in expressions) else "canonical"


EXPANSION = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def regex_text(word):
    """Return (text, expansion names) a regex consumer receives from one shell word.

    Quotes are removed, not replaced: a range inside a double- or single-quoted
    grep pattern is regex text. In `[[ =~ ]]` bash matches quoted text
    literally, so judging it as regex text there is deliberately conservative.
    Each expansion becomes a neutral `X`. Returns None for a word this cannot
    read (an unterminated quote, a command substitution, a parameter operation).
    """
    out, names = [], []
    index = 0
    quote = None
    while index < len(word):
        char = word[index]
        if quote == "'":
            if char == "'":
                quote = None
            else:
                out.append(char)
            index += 1
            continue
        if char == "$" and quote is None and word[index + 1:index + 2] in ("'", '"'):
            # $'...' decodes escapes such as \x5b into a bracket, and $"..."
            # is locale-translated: neither is the text written here.
            return None
        if char == "'" and quote is None:
            quote = "'"
            index += 1
            continue
        if char == '"':
            quote = None if quote == '"' else '"'
            index += 1
            continue
        if char == "\\" and index + 1 < len(word):
            following = word[index + 1]
            if quote == '"' and following in '$`"\\':
                out.append(following)
            else:
                out.append(word[index:index + 2])
            index += 2
            continue
        if char == "$" and index + 1 < len(word) and word[index + 1] in "{(`" + "_" + "".join(
            chr(code) for code in range(65, 91)
        ) + "".join(chr(code) for code in range(97, 123)):
            match = EXPANSION.match(word, index)
            if match is None:
                return None
            names.append(match.group(1) or match.group(2))
            out.append("X")
            index = match.end()
            continue
        if char == "`":
            return None
        out.append(char)
        index += 1
    if quote is not None:
        return None
    return "".join(out), names


def judge_regex(word, held=None):
    """Classify one regex word; return (verdict, resolved text or None)."""
    parsed = regex_text(word)
    if parsed is None:
        return "unclassifiable", None
    text, names = parsed
    expressions = bracket_expressions(text)
    if expressions is None:
        return "unclassifiable", None
    if any(collation_ordered(expression) for expression in expressions):
        return "collation", text
    if not names:
        return "canonical", text
    if held and all(name in held for name in names):
        # A fragment held in a single-assignment literal is judged as written.
        resolved = EXPANSION.sub(lambda match: held[match.group(1) or match.group(2)], word)
        resolved_parsed = regex_text(resolved)
        if resolved_parsed is not None and not resolved_parsed[1]:
            return _classify_single_quoted(resolved_parsed[0]), resolved_parsed[0]
    return "unclassifiable", text


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


# grep options that consume the following word as their argument.
GREP_ARGUMENT_OPTIONS = frozenset("ABCDfmd")
GREP_ARGUMENT_LONG = frozenset({
    "--after-context", "--before-context", "--context", "--max-count", "--file",
    "--label", "--binary-files", "--devices", "--directories", "--color", "--colour",
    "--include", "--exclude", "--exclude-dir", "--exclude-from", "--group-separator",
})


def _shell_words(text):
    """Split raw shell words (quotes kept) up to the end of the simple command."""
    words = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index] in " \t":
            index += 1
        if index >= len(text) or text[index] in "|;&<>)":
            break
        start = index
        while index < len(text) and text[index] not in " \t|;&<>)":
            char = text[index]
            if char == "'":
                close = text.find("'", index + 1)
                if close < 0:
                    return None
                index = close + 1
                continue
            if char == '"':
                index += 1
                while index < len(text) and text[index] != '"':
                    index += 2 if text[index] == "\\" else 1
                if index >= len(text):
                    return None
                index += 1
                continue
            index += 2 if char == "\\" else 1
        words.append(text[start:index])
    return words


def grep_patterns(arguments):
    """Return the patterns a grep invocation matches with, or None if unreadable.

    Handles `-e` (repeatable, attached or separate), `--regexp[=]`, `--`,
    option arguments such as `-m1`/`-m 1`, and `-F` (literal: no guard).
    """
    words = _shell_words(arguments)
    if words is None:
        return None
    explicit, positional, fixed = [], [], False
    index = 0
    options_done = False
    while index < len(words):
        word = words[index]
        index += 1
        if options_done or not word.startswith("-") or word == "-":
            positional.append(word)
            continue
        if word == "--":
            options_done = True
            continue
        if word.startswith("--"):
            name, equals, value = word.partition("=")
            if name == "--file":
                return None
            if name == "--fixed-strings":
                fixed = True
            elif name == "--regexp":
                if equals:
                    explicit.append(value)
                elif index < len(words):
                    explicit.append(words[index])
                    index += 1
                else:
                    return None
            elif name in GREP_ARGUMENT_LONG and not equals:
                index += 1
            continue
        letters = word[1:]
        for position, letter in enumerate(letters):
            if letter == "f":
                return None
            if letter == "F":
                fixed = True
            if letter == "e" or letter in GREP_ARGUMENT_OPTIONS:
                rest = letters[position + 1:]
                if rest:
                    argument = rest
                elif index < len(words):
                    argument = words[index]
                    index += 1
                else:
                    return None
                if letter == "e":
                    explicit.append(argument)
                break
    if fixed:
        return []
    return explicit or positional[:1]


def _regex_variables(script):
    """Map each name assigned exactly once, from one single-quoted literal.

    A name that is also appended to (`+=`), or assigned any other way (from an
    expansion, a double-quoted string, `read`, a loop), is not a literal, so a
    guard that uses it stays unclassifiable.
    """
    name = r"[A-Za-z_][A-Za-z0-9_]*"
    prefix = r"(?:^|[;&|\s(])(?:local\s+|readonly\s+|export\s+|declare\s+(?:-\w+\s+)*)?"
    literal = {}
    for found, value in re.findall(prefix + "(" + name + r")='([^']*)'(?=\s|;|$)", script, re.M):
        literal.setdefault(found, []).append(value)
    every = re.findall(prefix + "(" + name + r")\+?=", script, re.M)
    every += re.findall(r"\b(?:read(?:\s+-\w+)*|for|mapfile|readarray)\s+(" + name + ")", script)
    counts = {}
    for found in every:
        counts[found] = counts.get(found, 0) + 1
    return {
        found: values[0] for found, values in literal.items()
        if len(values) == 1 and counts.get(found) == 1
    }


# A generation-time substitution in a generator heredoc: its emitted text is
# unknown when the generator is scanned.
GENERATION_MARK = "\u27e6gen:"
GENERATION_TIME = GENERATION_MARK + "{}\u27e7"


def logical_lines(script):
    """Join continuation lines so a test or pipeline split across lines is one line.

    A line continues when it ends in a backslash, `&&`, `||`, or `|`, or while
    a `[[` it opened has not been closed by `]]`.
    """
    pending = []
    for line in script.splitlines():
        if not pending and line.lstrip().startswith("#"):
            yield line
            continue
        stripped = line.rstrip()
        continued = stripped.endswith("\\")
        pending.append(stripped[:-1] if continued else stripped)
        text = " ".join(part.strip() for part in pending)
        opened = len(re.findall(r"(?:^|[\s;(!])\[\[(?=\s)", text))
        closed = len(re.findall(r"(?<=\s)\]\](?=\s|;|$|\))", text))
        if continued or re.search(r"(?:&&|\|\||\|)$", stripped) or opened > closed:
            continue
        yield text
        pending = []
    if pending:
        yield " ".join(part.strip() for part in pending)


def _functions(lines):
    """Yield (name, first line index, end line index or None) for each shell function."""
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)(?:function\s+)?([A-Za-z_][A-Za-z0-9_-]*)\s*\(\)\s*\{?\s*(?:#.*)?$", line)
        if not match:
            continue
        indent = len(match.group(1))
        end = next(
            (
                position for position in range(index + 1, len(lines))
                if re.match(r"^\s*\}\s*(?:#.*)?$", lines[position])
                and len(lines[position]) - len(lines[position].lstrip()) <= indent
            ),
            None,
        )
        yield match.group(2), index, end


PARAMETER = re.compile(r"\$(?:\{([1-9])(?::?[-=][^}]*)?\}|([1-9]))")


def _parameter_patterns(body):
    """Return {positional number} whose value a grep or =~ in BODY uses as its pattern."""
    locals_ = {}
    for name, word in re.findall(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)=(\"[^\"]*\"|\S+)", body):
        parameter = PARAMETER.fullmatch(word.strip('"'))
        if parameter:
            locals_[name] = int(parameter.group(1) or parameter.group(2))
    numbers = set()

    def positional(word):
        stripped = word.strip('"')
        parameter = PARAMETER.fullmatch(stripped)
        if parameter:
            return int(parameter.group(1) or parameter.group(2))
        variable = re.fullmatch(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", stripped)
        return locals_.get(variable.group(1)) if variable else None

    for line in body.splitlines():
        for _subject, operator, pattern in _test_operands(line):
            if operator == "=~" and positional(pattern):
                numbers.add(positional(pattern))
        for match in re.finditer(r"(?<![\w-])grep\b", line):
            for pattern in grep_patterns(line[match.end():]) or []:
                if positional(pattern):
                    numbers.add(positional(pattern))
    return numbers


def argument_guards(source, lines):
    """Check the literal argument every caller passes to a pattern parameter."""
    functions = list(_functions(lines))
    for name, start, end in functions:
        if end is None:
            continue
        numbers = _parameter_patterns("\n".join(lines[start + 1:end]))
        if not numbers:
            continue
        for index, line in enumerate(lines):
            if start <= index <= end or line.lstrip().startswith("#"):
                continue
            for call in re.finditer(
                r"(?:^|[;&|!({]\s*|\b(?:if|then|else|elif|do|until|while)\s+)" + re.escape(name) + r"(?=\s|$)",
                line,
            ):
                words = _shell_words(line[call.end():])
                for number in sorted(numbers):
                    subject = f"{name} argument {number}"
                    if words is None:
                        yield Guard(source, "argument", subject, line.strip(), "unclassifiable")
                        continue
                    argument = words[number - 1] if len(words) >= number else "''"
                    verdict, resolved = judge_regex(argument)
                    if re.search(r"\$[{(A-Za-z_0-9@*#?]", argument.replace("\\$", "")):
                        verdict = "unclassifiable" if verdict == "canonical" else verdict
                    yield Guard(source, "argument", subject, argument, verdict, resolved)


def guards_in_script(source, script):
    """Yield every bracket-using guard in one shell script."""
    held = _regex_variables(script)
    lines = list(logical_lines(script))
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        for subject, operator, pattern in _test_operands(line):
            if operator == "=~":
                if "[" not in pattern and "$" not in pattern and GENERATION_MARK not in pattern:
                    continue
                verdict, resolved = judge_regex(pattern, held)
                yield Guard(source, "regex", subject, pattern, verdict, resolved)
                continue
            literal = strip_quoted(pattern)
            if literal is None or "[" not in literal:
                continue
            yield Guard(source, "glob", subject, pattern, classify(pattern))
    for subject, block in _case_blocks(script):
        if block is None:
            yield Guard(source, "case", subject, "<unterminated>", "unclassifiable")
            continue
        for label in _case_labels(block):
            literal = strip_quoted(label)
            if literal is not None and "[" not in literal and not re.search(r"\$[{(A-Za-z_]", literal):
                continue
            yield Guard(source, "case", subject, label, classify(label))
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        for match in re.finditer(r"(?<![\w-])grep\b", line):
            patterns = grep_patterns(line[match.end():])
            if patterns is None:
                yield Guard(source, "grep", "<grep>", line[match.start():].strip(), "unclassifiable")
                continue
            for pattern in patterns:
                if "[" not in pattern and "$" not in pattern and GENERATION_MARK not in pattern:
                    continue
                verdict, resolved = judge_regex(pattern)
                yield Guard(source, "grep", "<grep>", pattern, verdict, resolved)
    yield from argument_guards(source, lines)


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


def heredoc_bodies(text):
    """Yield the text each heredoc emits: verbatim when quoted, unescaped otherwise."""
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if line.lstrip().startswith("#"):
            continue
        heredoc = re.search(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2", line)
        if not heredoc or "<<<" in line[heredoc.start():heredoc.start() + 3]:
            continue
        strip_tabs, quoted, terminator = heredoc.group(1) == "-", bool(heredoc.group(2)), heredoc.group(3)
        body = []
        while index < len(lines):
            candidate = lines[index].lstrip("\t") if strip_tabs else lines[index]
            index += 1
            if candidate == terminator:
                break
            body.append(candidate)
        emitted = "\n".join(body)
        if not quoted:
            # An unescaped expansion is substituted when the generator runs, so
            # its emitted text is unknown here: mark it. An escaped one is
            # emitted as written and expands when the generated script runs.
            emitted = re.sub(
                r"(?<!\\)\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))",
                lambda match: GENERATION_TIME.format(match.group(1) or match.group(2)),
                emitted,
            )
            emitted = re.sub(r"\\([$`\\])", r"\1", emitted)
        yield emitted




def scan_generator_heredocs(source, text):
    """Scan every heredoc a generator script emits as shell text.

    A guard whose text includes a generation-time substitution cannot be
    judged from the generator, so it is unclassifiable unless it is already
    a collation failure on its static text.
    """
    for body in heredoc_bodies(text):
        for guard in guards_in_script(source, body):
            if GENERATION_MARK in guard.pattern and guard.verdict == "canonical":
                guard = Guard(guard.source, guard.kind, guard.subject, guard.pattern,
                              "unclassifiable", guard.resolved)
            yield guard


# Generators whose emitted text is scanned from their heredocs. The changelog
# generator's output is scanned by running it, so it is not listed here.
HEREDOC_GENERATORS = (
    "scripts/gen-container-candidate.sh",
    "scripts/gen-container-deployment.sh",
    "scripts/gen-container-release.sh",
)


def scan_repository(root):
    root = Path(root)
    files = sorted((root / ".github/workflows").glob("*.yml"))
    files += sorted((root / ".github/actions").glob("*/action.yml"))
    for file in files:
        yield from scan_workflow_text(file.relative_to(root).as_posix(), file.read_text(encoding="utf-8"))
    for file in sorted((root / "scripts").glob("gen-*.sh")):
        source = file.relative_to(root).as_posix()
        text = file.read_text(encoding="utf-8")
        yield from guards_in_script(source, shell_outside_heredocs(text))
        if source in HEREDOC_GENERATORS:
            yield from scan_generator_heredocs(f"{source} <heredoc>", text)

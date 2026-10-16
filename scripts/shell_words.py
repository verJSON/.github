"""Shell source split into words the way bash reads them, for manifest contracts.

shlex treats every `#` as a comment, even inside a word, so `${#arr[@]}` or a
URL fragment would swallow the rest of the line. Bash starts a comment only at
a word-initial `#` outside quotes; this module strips exactly those comments and
lets shlex split what remains.
"""

import shlex

# Characters after which a `#` begins a new word: blanks and bash metacharacters.
WORD_BOUNDARIES = frozenset(" \t\n;&|()<>")


def strip_comments(text):
    """Remove comments that bash would ignore, leaving quoted and mid-word `#`."""
    kept = []
    index = 0
    quote = None
    while index < len(text):
        character = text[index]
        if quote == "'":
            kept.append(character)
            if character == "'":
                quote = None
            index += 1
        elif character == "\\" and index + 1 < len(text):
            kept.append(text[index : index + 2])
            index += 2
        elif quote == '"':
            kept.append(character)
            if character == '"':
                quote = None
            index += 1
        elif character in "'\"":
            quote = character
            kept.append(character)
            index += 1
        elif character == "#" and (index == 0 or text[index - 1] in WORD_BOUNDARIES):
            newline = text.find("\n", index)
            index = len(text) if newline == -1 else newline
        else:
            kept.append(character)
            index += 1
    return "".join(kept)


def words(text):
    """Shell words of `text`, with control and redirection operators split out.

    Raises ValueError, as shlex does, when quoting is unbalanced.
    """
    logical_text = text.replace("\\\r\n", "").replace("\\\n", "")
    lexer = shlex.shlex(strip_comments(logical_text), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)

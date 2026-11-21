"""The pipeline text: stages separated by `|`, and the words of one stage.

Every error is a `QueryError` whose position is a character index into the
whole pipeline text, the same contract as the search bar's.
"""
import re
from dataclasses import dataclass

from kosha.search.query import QueryError, _regex_end


@dataclass(frozen=True)
class Tok:
    text: str  # without the surrounding quotes
    pos: int  # index into the pipeline text
    quoted: bool = False


@dataclass(frozen=True)
class Stage:
    name: str  # first word, lower case; "@" for a `@name` source
    arg: str  # the rest, as written
    pos: int
    arg_pos: int
    raw: str = ""  # the whole stage as written, trimmed

    def toks(self) -> list[Tok]:
        """The argument as words and "quoted strings"."""
        out = []
        for m in re.finditer(r'"([^"]*)"|"|(\S+)', self.arg):
            if m.group(0) == '"':
                raise QueryError('Missing closing quote "', self.arg_pos + m.start())
            quoted = m.group(1) is not None
            out.append(Tok(m.group(1) if quoted else m.group(2), self.arg_pos + m.start(), quoted))
        return out


def split(text: str) -> list[Stage]:
    """Cut at each `|` that is outside "quotes" and outside a /regex/ (which
    may contain `|`; write a space after its closing slash)."""
    cuts, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            close = text.find('"', i + 1)
            if close < 0:
                raise QueryError('Missing closing quote "', i)
            i = close
        elif c == "/" and (i == 0 or text[i - 1].isspace()):
            i = (_regex_end(text, i) or i + 1) - 1
        elif c == "|":
            cuts.append(i)
        i += 1
    stages, start = [], 0
    for end in [*cuts, n]:
        segment = text[start:end]
        body = segment.strip()
        pos = start + (len(segment) - len(segment.lstrip()))
        if not body:
            raise QueryError("Empty stage: nothing between the | separators" if cuts else "Empty pipeline", min(pos, max(n - 1, 0)))
        if body.startswith("@"):
            name, arg, arg_pos = "@", body[1:], pos + 1
        else:
            word = body.split(None, 1)[0]
            rest = body[len(word):]
            name, arg, arg_pos = word.lower(), rest.strip(), pos + len(word) + (len(rest) - len(rest.lstrip()))
        stages.append(Stage(name, arg, pos, arg_pos, body))
        start = end + 1
    return stages


def number(tok: Tok, kind=int, least=0):
    """A stage's numeric argument, or an error under it."""
    try:
        value = kind(tok.text)
    except ValueError:
        raise QueryError(f'"{tok.text}" is not a number', tok.pos) from None
    if value < least:
        raise QueryError(f"Must be at least {least}", tok.pos)
    return value

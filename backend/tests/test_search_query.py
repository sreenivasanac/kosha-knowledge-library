"""The qualifier language: tokenizer, parser, canonical printer, errors."""
from datetime import UTC, datetime

import pytest

from kosha.search.query import And, Not, Or, Pred, QueryError, SetRef, TagRef, Text, parse, to_string

NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)


def tree(q):
    return parse(q, NOW).filter


def day(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=UTC)


def test_boolean_structure_and_precedence():
    a, b, c = Text("word", "a"), Text("word", "b"), Text("word", "c")
    assert tree("a b") == And((a, b)) == tree("a AND b")
    assert tree("a b OR c") == Or((And((a, b)), c))  # AND binds tighter than OR
    assert tree("a (b OR c)") == And((a, Or((b, c))))
    assert tree("-a NOT b") == And((Not(a), Not(b)))
    assert tree("-(a b)") == Not(And((a, b)))
    assert tree("(a b) c") == And((a, b, c))  # redundant groups flatten
    assert tree("or not") == And((Text("word", "or"), Text("word", "not")))  # lower case stays searchable
    assert tree("") is None and tree("()") is None


def test_text_operators():
    assert tree('"hybrid search"') == Text("phrase", "hybrid search")
    assert tree("retriev*") == Text("prefix", "retriev")
    assert tree("~pgvectr") == Text("fuzzy", "pgvectr")
    assert tree(r"/hnsw\.ef_\w+/") == Text("regex", r"hnsw\.ef_\w+")
    assert tree("/a b/") == Text("regex", "a b")  # a regex may contain spaces
    assert tree("/api") == Text("word", "/api")  # no closing slash: just a word
    assert tree("fine-tuning") == Text("word", "fine-tuning")  # an inner dash is not NOT
    assert tree("in:title,note (prompt caching)") == And(
        (Text("word", "prompt", ("title", "note")), Text("word", "caching", ("title", "note"))))
    assert tree('title:hnsw note:"to do"') == And((Text("word", "hnsw", ("title",)), Text("phrase", "to do", ("note",))))


def test_comparisons_ranges_and_lists():
    assert tree("likes:>1000") == Pred("likes", "gt", 1000)
    assert tree("likes:1k..2m") == Pred("likes", "range", (1000, 2_000_000))
    assert tree("conf:<0.85") == Pred("conf", "lt", 0.85)
    assert tree("conf:..0.85") == Pred("conf", "range", (None, 0.85))
    assert tree("duration:<5m") == Pred("duration", "lt", 300)
    assert tree("duration:10m..30m") == Pred("duration", "range", (600, 1800))
    assert tree("duration:>=90s") == Pred("duration", "ge", 90)
    assert tree("tags:0") == Pred("tags", "eq", 0)
    assert tree("from:@a,b") == Or((Pred("from", "eq", "@a"), Pred("from", "eq", "b")))
    assert tree('from:"Ada Lovelace"') == Pred("from", "eq", "Ada Lovelace")
    assert tree("id:18234,9912") == Or((Pred("id", "eq", 18234), Pred("id", "eq", 9912)))
    assert tree("site:GitHub.com") == Pred("domain", "eq", "GitHub.com")
    assert tree("type:video,image") == Or((Pred("type", "eq", "video"), Pred("type", "eq", "image")))
    assert tree("is:starred -is:read") == And((Pred("is", "eq", "starred"), Not(Pred("is", "eq", "read"))))


def test_dates_are_half_open_ranges():
    assert tree("posted:2026-03..2026-05") == Pred("posted", "range", (day(2026, 3, 1), day(2026, 6, 1)))
    assert tree("posted:2025") == Pred("posted", "range", (day(2025, 1, 1), day(2026, 1, 1)))
    assert tree("saved:2026-03-14") == Pred("saved", "range", (day(2026, 3, 14), day(2026, 3, 15)))
    assert tree("posted:2026-12") == Pred("posted", "range", (day(2026, 12, 1), day(2027, 1, 1)))
    assert tree("after:30d") == Pred("posted", "range", (day(2026, 10, 22, 12), None))
    assert tree("before:2026-01") == Pred("posted", "range", (None, day(2026, 1, 1)))
    assert tree("captured:1d..") == Pred("captured", "range", (day(2026, 11, 20, 12), None))
    assert tree("posted:>2025") == Pred("posted", "range", (day(2026, 1, 1), None))
    assert tree("saved:7d") == Pred("saved", "range", (day(2026, 11, 14, 12), None))


def test_tags_and_sets():
    assert tree("#ai/rag") == TagRef("ai/rag", "subtree")
    assert tree("#=ai/rag") == TagRef("ai/rag", "exact")
    assert tree("#ai/rag/*") == TagRef("ai/rag", "below")
    assert tree('#"deep learning/nlp"') == TagRef("deep learning/nlp", "subtree")
    assert tree("-#x") == Not(TagRef("x"))
    assert tree("primary:#ai secondary:rag") == And((TagRef("ai", "subtree", "primary"), TagRef("rag", "subtree", "secondary")))
    assert tree("@h7") == SetRef("h7")
    assert tree("tagged-by:ai #ai conf:<0.85") == And(
        (Pred("tagged-by", "eq", "ai"), TagRef("ai"), Pred("conf", "lt", 0.85)))


def test_query_level_parts():
    q = parse("pgvector \"hnsw tuning\" -rag retriev* from:a sort:likes-asc similar:42 include:archived", NOW)
    assert q.sort == ("likes", False) and q.similar == "42" and q.include == {"archived"}
    assert q.rank_text == 'pgvector "hnsw tuning"'  # negated and prefix terms only filter
    assert [t.value for t in q.rank_atoms] == ["pgvector", "hnsw tuning"]
    assert parse("sort:posted", NOW).sort == ("posted", True)
    nested = parse("(a OR b) c", NOW)
    assert nested.rank_text == "a b c" and [t.value for t in nested.rank_atoms] == ["c"]


@pytest.mark.parametrize("q", [
    "a b OR c", "a (b OR c) -(d e)", '"hybrid search" -rag retriev* ~pgvectr /x y/', "in:title,note (prompt caching)",
    'title:hnsw note:"to do"', "from:@a,b likes:>1000 duration:<5m posted:2026-03..2026-05 after:30d",
    '#ai/rag #=ai/rag #ai/rag/* -#"a b" primary:#x tags:0 tagged-by:ai conf:<0.85', "@h7 is:starred has:link type:video",
    'folder:"Coding with AI" domain:github.com link:pgvector id:1,2 recent:50', "x sort:likes-asc similar:42 include:archived",
])
def test_canonical_text_round_trips(q):
    first = parse(q, NOW)
    again = parse(to_string(first), NOW)
    assert again.filter == first.filter and (again.sort, again.similar, again.include) == (first.sort, first.similar, first.include)


@pytest.mark.parametrize("q, position, fragment", [
    ("a foo:bar", 2, 'Unknown key "foo:"'),
    ("frm:x", 0, 'Did you mean "from:"'),
    ("a (b c", 2, "Missing closing )"),
    ("a b )", 4, "Unexpected )"),
    ("a OR", 2, "OR needs"),
    ("OR a", 0, "OR needs"),
    ("a NOT", 2, "needs a term"),
    ("x from:", 7, "from: needs a value"),
    ("likes:>abc", 6, "not a number"),
    ("x posted:20x", 9, "not a date"),
    ("duration:5x", 9, "not a duration"),
    ('say "abc', 4, "closing quote"),
    ("type:vid", 5, 'Did you mean "video"'),
    ("/(/", 0, "Invalid regular expression"),
    ("sort:nope", 5, "Unknown sort"),
    ("in:nowhere x", 3, "Unknown value"),
    ("kind:tool", 0, "not available yet"),
    ("x #", None, None),  # a lone # is just a word
])
def test_errors_carry_a_position(q, position, fragment):
    if fragment is None:
        assert parse(q, NOW).filter is not None
        return
    with pytest.raises(QueryError) as caught:
        parse(q, NOW)
    assert fragment in caught.value.message and caught.value.position == position
    assert caught.value.as_dict() == {"message": caught.value.message, "position": position}


def test_chips_cover_every_token():
    chips = parse('from:@a -#x "two words" OR c', NOW).chips
    assert [(c["kind"], c["text"], c["start"], c["end"]) for c in chips] == [
        ("qualifier", "from:@a", 0, 7), ("tag", "-#x", 8, 11), ("text", '"two words"', 12, 23),
        ("operator", "OR", 24, 26), ("text", "c", 27, 28)]
    assert chips[0]["key"] == "from" and chips[1]["negated"] is True

"""Retrieval (SEARCH_DESIGN.md section C).

Filter first; then a keyword ranker and an exact semantic ranker inside the
filtered set; then weighted reciprocal rank fusion. A ranked result is stored
as a snapshot in `result_sets`, and every page is a slice of it, so page 3 does
not shift while you read.
"""
import html
import math
import re
import secrets
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, and_, case, delete, func, literal, literal_column, or_, select, text, true
from sqlalchemy.orm import Session, aliased

from kosha.items_query import fetch_page
from kosha.models import Item, ItemEmbedding, ResultSet, SearchHistory
from kosha.search.embeddings import DOC_MODEL, EmbeddingError, query_vector
from kosha.search.rerank import STRONG_SCORE as RERANK_STRONG_SCORE
from kosha.search.rerank import RerankError
from kosha.search.rerank import rerank as rerank_ids
from kosha.search.query import Query, QueryError, Text, compile_filter, order_by, parse, parse_sort, text_condition, to_string, ts_config
from kosha.summaries import item_text

# --- Tunables ---------------------------------------------------------------
# Every ranking constant is here. The values were chosen by measurement on the
# real library: docs/experiments/search_tuning_2026-11-21.md.
RRF_K = 60
RANKER_TOP_N = 200  # results each ranker contributes
SNAPSHOT_MAX_ITEMS = 500
SNAPSHOT_TTL = timedelta(minutes=30)  # from last use
SNAPSHOT_PREFIX = "snap-"
PINNED_PREFIX = "jev-"  # the kept items of a Jev check (POST /api/jev/check): served whatever q says
# (keyword weight, semantic weight) by the shape of the text (C.2).
WEIGHTS_DEFAULT = (1.0, 1.0)
WEIGHTS_IDENTIFIER = (2.0, 1.0)  # one or two tokens, or something that looks like an identifier
WEIGHTS_LONG = (1.0, 1.5)  # six words or more, unquoted
LONG_QUERY_WORDS = 6
# A semantic hit is kept when its cosine similarity is at least the floor and
# within the margin of the best hit. Voyage similarities on this library run
# from about 0.2 (unrelated) to 0.7; the cut that separates "related" from the
# rest moves with the query, hence the margin.
SEMANTIC_MIN_SIMILARITY = 0.30
SEMANTIC_MARGIN = 0.15
# Keyword hits. Each top-level word weighs its rarity in the library (BM25's
# idf); a row is a keyword hit when the words it contains carry at least this
# share of the total weight. 1.0 is "every word" (AND). Keyword mode is always AND.
KEYWORD_MIN_COVERAGE = 1.0
COMMON_TERM_SHARE = 0.5  # a word in more than this share of items says nothing (stop words) and is ignored
# A word no item contains is tried as a prefix, then as a misspelling of the
# library's own words (trigram similarity to the indexed words, author names
# included), when it is at least this long.
FUZZY_MIN_CHARS = 4
FUZZY_WORD_SIMILARITY = 0.4
FUZZY_MAX_WORDS = 3  # the closest words stand in for the misspelled one
# Share of the keyword score given to "any of the words" next to "all of the
# top-level words close together".
ANY_WORD_WEIGHT = 0.1
PHRASE_BOOST = 0.0  # added to the keyword score of a row that holds the words as one phrase
TS_WEIGHTS = "{0.1, 0.2, 0.4, 1.0}"  # D, C, B, A
TS_NORMALIZATION = 1  # divide the rank by 1 + log(document length)
# Light tie-breakers on the fused score: score * (1 + weight * signal), signal in 0..1.
ENGAGEMENT_WEIGHT = 0.0  # signal: log10(1 + likes) / 6
RECENCY_WEIGHT = 0.0  # signal: 1 at today, 0 at RECENCY_SPAN_DAYS ago or older
RECENCY_SPAN_DAYS = 1500
# "Few good results": a result is strong when it is a keyword hit with at
# least this coverage, or its similarity is at least STRONG_SIMILARITY, or
# within STRONG_MARGIN of the best hit. The search bar shows the strong ones
# and says how many weaker ones it left out.
STRONG_COVERAGE = 1.0
STRONG_SIMILARITY = 0.45
STRONG_MARGIN = 0.08
SNIPPET_SOURCE_CHARS = 5000
SNIPPET_FALLBACK_CHARS = 160
# Control characters mark the hits so the text can be HTML-escaped before the
# <mark> tags go in.
MARK_OPEN, MARK_CLOSE = "\x02", "\x03"
HEADLINE_OPTIONS = f"MaxFragments=2, MaxWords=18, MinWords=8, FragmentDelimiter= … , StartSel={MARK_OPEN}, StopSel={MARK_CLOSE}"
MODES = ("hybrid", "keyword", "semantic", "filter")
RECENT_SEARCHES = 10


def rrf(rankings: list[tuple[list[int], float]], k: int | None = None) -> list[tuple[int, float]]:
    """Weighted reciprocal rank fusion: score = sum of weight / (k + rank) over
    the rankers that returned the id. Best first; ties go to the lower id."""
    k = RRF_K if k is None else k
    scores: dict[int, float] = {}
    for ids, weight in rankings:
        for rank, item_id in enumerate(ids, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + weight / (k + rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))


def fusion_weights(rank_text: str, mode: str) -> tuple[float, float]:
    if mode == "keyword":
        return 1.0, 0.0
    if mode == "semantic":
        return 0.0, 1.0
    words = rank_text.split()
    if len(words) <= 2 or any(re.search(r"[\d_.]|[a-z][A-Z]", w) for w in words):
        return WEIGHTS_IDENTIFIER
    if len(words) >= LONG_QUERY_WORDS and '"' not in rank_text:
        return WEIGHTS_LONG
    return WEIGHTS_DEFAULT


def _or_tsquery(query: Query, config: str = "english"):
    """Any of the query's words: used to score and to highlight, never to
    filter (the filter already decided which rows are in)."""
    terms = [t for t in query.rank_terms if t.lower() != "or"]
    return func.websearch_to_tsquery(ts_config(config), " OR ".join(terms))


def _closest_words(session: Session, user_id: int, word: str) -> list[str]:
    """The indexed words (stems, author names and handles) most like a
    misspelled word. Reads the whole word list of the library (`ts_stat`),
    about 85 ms at 7,000 items, so it runs only for a word nothing contains."""
    rows = session.execute(text(
        f"SELECT word FROM ts_stat('SELECT tsv FROM items WHERE user_id = {int(user_id)}') "
        "WHERE word ~ '^[[:alnum:]_]+$' AND similarity(word, :word) >= :least "
        "ORDER BY similarity(word, :word) DESC, ndoc DESC LIMIT :n"),
        {"word": word.lower(), "least": FUZZY_WORD_SIMILARITY, "n": FUZZY_MAX_WORDS})
    return [r.word for r in rows]


def weighted_terms(session: Session, user_id: int, query: Query) -> tuple[list[tuple[ColumnElement[bool], float]], bool]:
    """The top-level words as (condition, weight). The weight is the word's
    rarity in the library, so "pgvector" counts for more than "building".
    Stop words and words in most items are left out. A word nothing contains
    is replaced by its prefix match or, failing that, the closest indexed words.
    Returns the terms and whether any was replaced."""
    atoms = list(query.rank_atoms)
    if not atoms:
        return [], False
    conds = [text_condition(a, stop_words_pass=False) for a in atoms]

    def counts(conditions, *extra):
        subs = [select(func.count()).select_from(Item).where(Item.user_id == user_id, c).scalar_subquery() for c in conditions]
        return list(session.execute(select(*subs, *extra)).one())

    # numnode is 0 for a term made of stop words only: it is left out below
    stops = [func.numnode(func.plainto_tsquery(ts_config("english"), a.value)) for a in atoms]
    total, *rest = counts([true(), *conds], *stops)
    dfs = [total if nodes == 0 else df for df, nodes in zip(rest[:len(atoms)], rest[len(atoms):])]
    replaced = False
    for i, df in enumerate(dfs):
        word = atoms[i].value
        if df or atoms[i].kind != "word" or atoms[i].fields or len(word) < FUZZY_MIN_CHARS:
            continue
        options = [text_condition(Text("prefix", word), stop_words_pass=False)]
        close = _closest_words(session, user_id, word)
        if close:
            options.append(Item.tsv.op("@@")(func.to_tsquery(ts_config("simple"), " | ".join(f"'{w}'" for w in close))))
        for cond, found in zip(options, counts(options)):
            if found:
                conds[i], dfs[i], replaced = cond, found, True
                break
    terms = [(cond, math.log(1 + (total - df + 0.5) / (df + 0.5)))
             for cond, df in zip(conds, dfs) if df <= total * COMMON_TERM_SHARE]
    return terms, replaced


def keyword_rank(session: Session, where: list[ColumnElement[bool]], query: Query,
                 terms: list[tuple[ColumnElement[bool], float]], min_coverage: float) -> list[tuple[int, float]]:
    """(id, coverage) of the keyword hits, best first. Coverage is the share
    of the query's word weight the row contains; rows under `min_coverage` are
    not hits. Within the same coverage the order is cover density
    (`ts_rank_cd`): mostly of all the top-level words together, so rows where
    they sit close in a heavy field lead, plus a little for any word."""
    if not terms:
        return []
    weights = literal_column(f"'{TS_WEIGHTS}'::float4[]")
    all_words = " ".join(a.raw for a in query.rank_atoms if not a.fields) or None
    rank = 0
    for vector in (Item.tsv, Item.tsv_extra):
        for config in ("english", "simple"):
            score = lambda tsq: func.coalesce(func.ts_rank_cd(weights, vector, tsq, TS_NORMALIZATION), 0)  # noqa: E731, B023
            rank = rank + ANY_WORD_WEIGHT * score(_or_tsquery(query, config))
            if all_words:
                rank = rank + score(func.websearch_to_tsquery(ts_config(config), all_words))
    if PHRASE_BOOST and all_words and len(query.rank_atoms) > 1:
        phrase = func.phraseto_tsquery(ts_config("english"), " ".join(a.value for a in query.rank_atoms))
        rank = rank + case((Item.tsv.op("@@")(phrase), PHRASE_BOOST), else_=0.0)
    total = sum(w for _, w in terms)
    if min_coverage >= 1:  # AND: lets Postgres use the word index for every term
        candidates, coverage = and_(*(c for c, _ in terms)), literal(1.0)
    else:
        coverage = sum(case((c, w), else_=0.0) for c, w in terms) / total
        candidates = and_(or_(*(c for c, _ in terms)), coverage >= min_coverage)
    stmt = (select(Item.id, coverage).where(*where, candidates)
            .order_by(coverage.desc(), rank.desc(), Item.id).limit(RANKER_TOP_N))
    return [(item_id, float(c)) for item_id, c in session.execute(stmt)]


def semantic_rank(session: Session, where: list[ColumnElement[bool]], vector, exclude_id: int | None = None) -> list[tuple[int, float]]:
    """(id, cosine similarity) of the exact nearest neighbours inside the
    filtered set (no ANN index), cut where similarity falls too far below the
    best hit."""
    distance = ItemEmbedding.vector.cosine_distance(vector)
    stmt = (select(Item.id, distance).join(ItemEmbedding, (ItemEmbedding.item_id == Item.id) & (ItemEmbedding.model == DOC_MODEL))
            .where(*where, distance <= 1 - SEMANTIC_MIN_SIMILARITY).order_by(distance, Item.id).limit(RANKER_TOP_N))
    if exclude_id is not None:
        stmt = stmt.where(Item.id != exclude_id)
    rows = session.execute(stmt).all()
    return [(item_id, 1 - d) for item_id, d in rows if d <= rows[0][1] + SEMANTIC_MARGIN]


def _similar_vector(session: Session, user_id: int, similar: str):
    """A subquery giving the vector of one item, or the average vector of a set."""
    other = aliased(ItemEmbedding)
    if similar.startswith("@"):
        ids = session.scalar(select(ResultSet.item_ids).where(ResultSet.user_id == user_id, ResultSet.name == similar[1:]))
        if ids is None:
            raise QueryError(f"No result set named {similar}", 0)
    else:
        ids = [int(similar)]
    scope = (other.item_id.in_(ids), other.model == DOC_MODEL)
    if not session.scalar(select(func.count()).select_from(other).where(*scope)):
        raise QueryError(f"similar:{similar} has no embedding yet", 0)
    return select(func.avg(other.vector)).where(*scope).scalar_subquery()


def _tie_break(session: Session, fused: list[tuple[int, float]]) -> list[tuple[int, float]]:
    """Nudge the fused scores by engagement and recency, so that of two
    equally relevant rows the better-liked or newer one comes first."""
    rows = session.execute(select(Item.id, Item.likes, Item.posted_at).where(Item.id.in_([i for i, _ in fused]))).all()
    now = datetime.now(UTC)
    signal = {}
    for item_id, likes, posted in rows:
        age = (now - posted).days if posted else RECENCY_SPAN_DAYS
        signal[item_id] = (ENGAGEMENT_WEIGHT * min(1.0, math.log10(1 + (likes or 0)) / 6)
                           + RECENCY_WEIGHT * max(0.0, 1 - age / RECENCY_SPAN_DAYS))
    return sorted(((i, s * (1 + signal.get(i, 0.0))) for i, s in fused), key=lambda pair: (-pair[1], pair[0]))


def rank_all(session: Session, user_id: int, query: Query, mode: str, extra_where: list) -> tuple[list[int], list[float], str | None, set[int]]:
    """Run the rankers the mode asks for and fuse them. Returns (ids best
    first, their scores, a notice for the UI or None, the ids that are strong
    matches: see STRONG_*)."""
    where = compile_filter(session, user_id, query, lift_rank_atoms=True) + extra_where
    w_kw, w_sem = fusion_weights(query.rank_text, mode)
    rankings, notice = [], None
    keyword_hits: list[tuple[int, float]] = []
    semantic_hits: list[tuple[int, float]] = []
    fuzzy = False
    if w_kw and query.rank_text:
        terms, fuzzy = weighted_terms(session, user_id, query)
        keyword_hits = keyword_rank(session, where, query, terms, 1.0 if mode == "keyword" else KEYWORD_MIN_COVERAGE)
        rankings.append(([i for i, _ in keyword_hits], w_kw))
    if w_sem:
        if query.rank_text:
            try:
                semantic_hits = semantic_rank(session, where, query_vector(session, user_id, query.rank_text))
                rankings.append(([i for i, _ in semantic_hits], w_sem))
            except EmbeddingError:
                if mode == "semantic":
                    raise
                notice = "semantic_unavailable"  # hybrid falls back to keyword alone
        if query.similar:
            exclude = int(query.similar) if query.similar.isdigit() else None
            similar_hits = semantic_rank(session, where, _similar_vector(session, user_id, query.similar), exclude)
            semantic_hits = semantic_hits or similar_hits
            rankings.append(([i for i, _ in similar_hits], w_sem))
    fused = rrf(rankings)[:SNAPSHOT_MAX_ITEMS]
    if fused and (ENGAGEMENT_WEIGHT or RECENCY_WEIGHT):
        fused = _tie_break(session, fused)
    exact = any(c >= 1 for _, c in keyword_hits)
    if fuzzy and keyword_hits:
        notice = "fuzzy_match"  # a word was matched as a prefix or a misspelling
    elif mode == "hybrid" and query.rank_text and not exact and fused:
        notice = "no_exact_matches"  # "showing related items"
    if not query.rank_text:  # `similar:` alone: a list of neighbours, nothing to call weak
        return [i for i, _ in fused], [s for _, s in fused], notice, {i for i, _ in fused}
    best = semantic_hits[0][1] if semantic_hits else 0.0
    strong = {i for i, c in keyword_hits if c >= STRONG_COVERAGE}
    strong |= {i for i, s in semantic_hits if s >= min(STRONG_SIMILARITY, best - STRONG_MARGIN + 1e-9) and s >= best - SEMANTIC_MARGIN}
    return [i for i, _ in fused], [s for _, s in fused], notice, strong


def rank(session: Session, user_id: int, query: Query, mode: str, extra_where: list) -> tuple[list[int], list[float], str | None]:
    """`rank_all` without the strong set: every hit, weaker ones included
    (what a pipeline stage wants: it narrows the set itself)."""
    return rank_all(session, user_id, query, mode, extra_where)[:3]


def snippets(session: Session, query: Query, items: list[Item]) -> dict[int, str]:
    """A highlighted excerpt per row of the page: the matching words marked in
    the search text, or the item's opening when no word matched (a
    semantic-only hit). Safe HTML: escaped text with <mark> tags only."""
    if not query.rank_terms or not items:
        return {}
    headline = func.ts_headline(ts_config("english"), func.left(func.coalesce(Item.search_text, ""), SNIPPET_SOURCE_CHARS),
                                _or_tsquery(query), HEADLINE_OPTIONS)
    rows = dict(session.execute(select(Item.id, headline).where(Item.id.in_([i.id for i in items]))).all())
    out = {}
    for item in items:
        text = rows.get(item.id) or ""
        if MARK_OPEN not in text:
            text = item_text(item.title, item.body)[:SNIPPET_FALLBACK_CHARS]
        out[item.id] = html.escape(" ".join(text.split())).replace(MARK_OPEN, "<mark>").replace(MARK_CLOSE, "</mark>")
    return out


def _snapshot(session: Session, user_id: int, snapshot_id: int | None, key: str, now: datetime) -> ResultSet | None:
    """The stored snapshot, if it is this user's, still alive and of this exact search."""
    if snapshot_id is None:
        return None
    row = session.get(ResultSet, snapshot_id)
    live = row is not None and row.user_id == user_id and row.query == key and row.expires_at and row.expires_at > now
    return row if live else None


def _pinned(session: Session, user_id: int, snapshot_id: int | None, now: datetime) -> ResultSet | None:
    """A Jev check's snapshot: its pages are the kept items in Jev's order,
    not a re-run of the search that fed the check."""
    row = session.get(ResultSet, snapshot_id) if snapshot_id is not None else None
    live = (row is not None and row.user_id == user_id and row.name.startswith(PINNED_PREFIX)
            and row.expires_at and row.expires_at > now)
    return row if live else None


def record_search(session: Session, user_id: int, text: str) -> None:
    """Move the query to the top of the recent searches."""
    row = session.scalar(select(SearchHistory).where(SearchHistory.user_id == user_id, SearchHistory.query == text))
    if row is None:
        session.add(SearchHistory(user_id=user_id, query=text))
    else:
        row.created_at = datetime.now(UTC)


def search(session: Session, user_id: int, q: str, *, mode: str | None = None, sort: str | None = None,
           page: int = 1, page_size: int = 50, snapshot_id: int | None = None,
           extra_where: list | None = None, extra_key: str = "", rerank: str = "none", weak: bool = False) -> dict:
    """One page of a search. `mode` defaults to hybrid when the query has text
    to rank by (or `similar:`), and is a plain filter otherwise. `extra_where`
    carries the list endpoint's own filters; `extra_key` names them so a
    snapshot is only reused for the same ones. `rerank` (voyage or jev) reorders
    the head of a ranked result with a paid second stage. A ranked result holds
    the strong matches only; `weak` adds the weaker ones, and `weak_hidden`
    says how many were left out."""
    started = time.perf_counter()
    extra_where = list(extra_where or [])
    query = parse(q)
    if sort:
        query.sort = parse_sort(sort)
    sort_key = query.sort[0] if query.sort else None
    ranked = bool(query.rank_text or query.similar) and sort_key in (None, "relevance") and mode != "filter"
    mode = (mode if mode in ("keyword", "semantic") else "hybrid") if ranked else "filter"
    if query.similar and not query.rank_text and mode == "keyword":
        mode = "semantic"  # similarity has no keyword side
    canonical = to_string(query)
    result = {"mode": mode, "snapshot_id": None, "refreshed": False, "notice": None, "scores": {}, "query": canonical,
              "weak_hidden": 0}

    now = datetime.now(UTC)
    pinned = _pinned(session, user_id, snapshot_id, now)
    if ranked or sort_key == "random" or pinned:
        key = f"{mode}|{canonical}|{extra_key}|{rerank}|{weak}"
        snapshot = pinned or _snapshot(session, user_id, snapshot_id, key, now)
        if snapshot is None:
            result["refreshed"] = snapshot_id is not None
            if ranked:
                ids, scores, result["notice"], strong = rank_all(session, user_id, query, mode, extra_where)
                if rerank != "none" and query.rank_text:
                    try:
                        ids, judged = rerank_ids(session, user_id, query.rank_text, ids, rerank)
                        scores = [judged.get(i, 0.0) for i in ids]
                        strong = {i for i, s in judged.items() if s >= RERANK_STRONG_SCORE[rerank]}
                    except RerankError:
                        result["notice"] = "rerank_unavailable"  # the fused order is served instead
                if not weak:
                    kept = [n for n, i in enumerate(ids) if i in strong]
                    result["weak_hidden"] = len(ids) - len(kept)
                    ids, scores = [ids[n] for n in kept], [scores[n] for n in kept]
            else:
                where = compile_filter(session, user_id, query) + extra_where
                ids = list(session.scalars(select(Item.id).where(*where).order_by(func.random()).limit(SNAPSHOT_MAX_ITEMS)))
                scores = None
            session.execute(delete(ResultSet).where(  # expired snapshots of this user go as new ones arrive
                ResultSet.user_id == user_id, ResultSet.name.startswith(SNAPSHOT_PREFIX), ResultSet.expires_at < now))
            snapshot = ResultSet(user_id=user_id, name=SNAPSHOT_PREFIX + secrets.token_hex(8), query=key,
                                 item_ids=ids, scores=scores)
            session.add(snapshot)
        snapshot.expires_at = now + SNAPSHOT_TTL
        session.flush()
        page_ids = snapshot.item_ids[(page - 1) * page_size: page * page_size]
        # An item deleted after the snapshot was taken is skipped.
        held = {i.id: i for i in session.scalars(select(Item).where(Item.user_id == user_id, Item.id.in_(page_ids)))}
        items = [held[i] for i in page_ids if i in held]
        total = len(snapshot.item_ids)
        if snapshot.scores:
            result["scores"] = dict(zip(snapshot.item_ids, snapshot.scores))
        result["snapshot_id"] = snapshot.id
    else:
        where = compile_filter(session, user_id, query) + extra_where
        order = order_by(query.sort if sort_key not in (None, "relevance") else ("saved", True))
        items, total = fetch_page(session, user_id, where, page, page_size, order=order)

    if page == 1 and canonical and snapshot_id is None:
        record_search(session, user_id, canonical)
    result.update(items=items, total=total, snippets=snippets(session, query, items),
                  took_ms=round((time.perf_counter() - started) * 1000, 1))
    return result


def recent_searches(session: Session, user_id: int) -> list[str]:
    return list(session.scalars(select(SearchHistory.query).where(SearchHistory.user_id == user_id)
                                .order_by(SearchHistory.created_at.desc()).limit(RECENT_SEARCHES)))

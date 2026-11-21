"""Retrieval (SEARCH_DESIGN.md section C).

Filter first; then a keyword ranker and an exact semantic ranker inside the
filtered set; then weighted reciprocal rank fusion. A ranked result is stored
as a snapshot in `result_sets`, and every page is a slice of it, so page 3 does
not shift while you read.
"""
import html
import re
import secrets
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, delete, func, literal_column, select
from sqlalchemy.orm import Session, aliased

from kosha.items_query import fetch_page
from kosha.models import Item, ItemEmbedding, ResultSet, SearchHistory
from kosha.search.embeddings import DOC_MODEL, EmbeddingError, query_vector
from kosha.search.query import Query, QueryError, compile_filter, order_by, parse, parse_sort, text_condition, to_string, ts_config
from kosha.summaries import item_text

# --- Tunables ---------------------------------------------------------------
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
# from about 0.2 (unrelated) to 0.7, and under 0.3 the hits read as noise; the
# cut that separates "related" from the rest above that
# moves with the query, hence the margin. Measured in
# docs/experiments/search_eval_2026-11-21.md.
SEMANTIC_MIN_SIMILARITY = 0.30
SEMANTIC_MARGIN = 0.15
# Share of the keyword score given to "any of the words" next to "all of the
# top-level words close together".
ANY_WORD_WEIGHT = 0.1
TS_WEIGHTS = "{0.1, 0.2, 0.4, 1.0}"  # D, C, B, A
TS_NORMALIZATION = 1  # divide the rank by 1 + log(document length)
SNIPPET_SOURCE_CHARS = 5000
SNIPPET_FALLBACK_CHARS = 160
# Control characters mark the hits so the text can be HTML-escaped before the
# <mark> tags go in.
MARK_OPEN, MARK_CLOSE = "\x02", "\x03"
HEADLINE_OPTIONS = f"MaxFragments=2, MaxWords=18, MinWords=8, FragmentDelimiter= … , StartSel={MARK_OPEN}, StopSel={MARK_CLOSE}"
MODES = ("hybrid", "keyword", "semantic", "filter")
RECENT_SEARCHES = 10


def rrf(rankings: list[tuple[list[int], float]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Weighted reciprocal rank fusion: score = sum of weight / (k + rank) over
    the rankers that returned the id. Best first; ties go to the lower id."""
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


def keyword_rank(session: Session, where: list[ColumnElement[bool]], query: Query) -> list[int]:
    """Ids that contain every top-level word, best first. The score is cover
    density (`ts_rank_cd`): mostly of all the top-level words together, so rows
    where they sit close in a heavy field lead, plus a little for any word."""
    weights = literal_column(f"'{TS_WEIGHTS}'::float4[]")
    all_words = " ".join(a.raw for a in query.rank_atoms if not a.fields) or None
    rank = 0
    for vector in (Item.tsv, Item.tsv_extra):
        for config in ("english", "simple"):
            score = lambda tsq: func.coalesce(func.ts_rank_cd(weights, vector, tsq, TS_NORMALIZATION), 0)  # noqa: E731, B023
            rank = rank + ANY_WORD_WEIGHT * score(_or_tsquery(query, config))
            if all_words:
                rank = rank + score(func.websearch_to_tsquery(ts_config(config), all_words))
    matches = [text_condition(atom) for atom in query.rank_atoms]
    return list(session.scalars(
        select(Item.id).where(*where, *matches).order_by(rank.desc(), Item.id).limit(RANKER_TOP_N)))


def semantic_rank(session: Session, where: list[ColumnElement[bool]], vector, exclude_id: int | None = None) -> list[int]:
    """Exact nearest neighbours inside the filtered set (no ANN index), cut
    where similarity falls too far below the best hit."""
    distance = ItemEmbedding.vector.cosine_distance(vector)
    stmt = (select(Item.id, distance).join(ItemEmbedding, (ItemEmbedding.item_id == Item.id) & (ItemEmbedding.model == DOC_MODEL))
            .where(*where, distance <= 1 - SEMANTIC_MIN_SIMILARITY).order_by(distance, Item.id).limit(RANKER_TOP_N))
    if exclude_id is not None:
        stmt = stmt.where(Item.id != exclude_id)
    rows = session.execute(stmt).all()
    return [item_id for item_id, d in rows if d <= rows[0][1] + SEMANTIC_MARGIN]


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


def rank(session: Session, user_id: int, query: Query, mode: str, extra_where: list) -> tuple[list[int], list[float], str | None]:
    """Run the rankers the mode asks for and fuse them.
    Returns (ids best first, their scores, a notice for the UI or None)."""
    where = compile_filter(session, user_id, query, lift_rank_atoms=True) + extra_where
    w_kw, w_sem = fusion_weights(query.rank_text, mode)
    rankings, notice = [], None
    keyword_ids: list[int] = []
    if w_kw and query.rank_text:
        keyword_ids = keyword_rank(session, where, query)
        rankings.append((keyword_ids, w_kw))
    if w_sem:
        if query.rank_text:
            try:
                rankings.append((semantic_rank(session, where, query_vector(session, user_id, query.rank_text)), w_sem))
            except EmbeddingError:
                if mode == "semantic":
                    raise
                notice = "semantic_unavailable"  # hybrid falls back to keyword alone
        if query.similar:
            exclude = int(query.similar) if query.similar.isdigit() else None
            rankings.append((semantic_rank(session, where, _similar_vector(session, user_id, query.similar), exclude), w_sem))
    fused = rrf(rankings)[:SNAPSHOT_MAX_ITEMS]
    if mode == "hybrid" and query.rank_text and not keyword_ids and fused:
        notice = "no_exact_matches"  # "showing related items"
    return [i for i, _ in fused], [s for _, s in fused], notice


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
           extra_where: list | None = None, extra_key: str = "") -> dict:
    """One page of a search. `mode` defaults to hybrid when the query has text
    to rank by (or `similar:`), and is a plain filter otherwise. `extra_where`
    carries the list endpoint's own filters; `extra_key` names them so a
    snapshot is only reused for the same ones."""
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
    result = {"mode": mode, "snapshot_id": None, "refreshed": False, "notice": None, "scores": {}, "query": canonical}

    now = datetime.now(UTC)
    pinned = _pinned(session, user_id, snapshot_id, now)
    if ranked or sort_key == "random" or pinned:
        key = f"{mode}|{canonical}|{extra_key}"
        snapshot = pinned or _snapshot(session, user_id, snapshot_id, key, now)
        if snapshot is None:
            result["refreshed"] = snapshot_id is not None
            if ranked:
                ids, scores, result["notice"] = rank(session, user_id, query, mode, extra_where)
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

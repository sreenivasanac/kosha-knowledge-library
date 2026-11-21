"""Second-stage reranking of the head of a ranked result (SEARCH_DESIGN.md C.4).

Two rerankers read each candidate's text next to the query, which the fused
rankers never do: Voyage's cross-encoder, and Jev answering one yes/no
question per item. Both cost money and a network round trip, so the search bar
does not use them unless asked (`rerank=voyage|jev`). What each one gains and
costs on the real library is in docs/experiments/search_tuning_2026-11-21.md.
"""
import httpx
from constella.jev import JevError, Question
from sqlalchemy.orm import Session

from kosha import jev
from kosha.keys import get_key
from kosha.search.embeddings import EmbeddingError, check_cap, record_spend

# --- Tunables ---------------------------------------------------------------
METHODS = ("none", "voyage", "jev")
RERANK_TOP_N = 50  # only the head is reranked; what follows keeps its order
VOYAGE_RERANK_URL = "https://api.voyageai.com/v1/rerank"
VOYAGE_RERANK_MODEL = "rerank-3"
VOYAGE_TIMEOUT_S = 30
# A reranked item counts as a strong match (see engine.STRONG_*) at or above this
# score. Measured: at these values a search for something the library does not
# hold shows about one result, and precision@10 is 0.94 to 0.95.
STRONG_SCORE = {"voyage": 0.4, "jev": 0.2}
# Jev reads literally: the question says what counts as a yes (docs/MCP.md, "Jev").
JEV_QUESTION = ('Someone is searching their saved posts for: "{query}". Is this post a good result for that search? '
                "Answer yes only if the post is about that specific subject or would directly help with it; "
                "a post that is merely in the same broad area is a no.")


class RerankError(RuntimeError):
    pass


def _voyage(session: Session, user_id: int, query: str, ids: list[int], texts: dict[int, str]) -> tuple[dict[int, float], float]:
    key = get_key("voyage")
    if not key:
        raise RerankError("VOYAGE_API_KEY is not set")
    try:
        check_cap(session, user_id)
    except EmbeddingError as exc:
        raise RerankError(str(exc)) from None
    try:
        response = httpx.post(VOYAGE_RERANK_URL, headers={"Authorization": f"Bearer {key}"}, timeout=VOYAGE_TIMEOUT_S,
                              json={"query": query, "documents": [texts[i] for i in ids], "model": VOYAGE_RERANK_MODEL})
    except httpx.TransportError as exc:
        raise RerankError(type(exc).__name__) from None
    if response.status_code != 200:
        raise RerankError(f"Voyage answered {response.status_code}: {response.text[:200]}")
    body = response.json()
    usd = record_spend(session, user_id, VOYAGE_RERANK_MODEL, "rerank", body["usage"]["total_tokens"], items=len(ids))
    return {ids[row["index"]]: row["relevance_score"] for row in body["data"]}, float(usd)


def _jev(session: Session, user_id: int, query: str, ids: list[int], texts: dict[int, str]) -> tuple[dict[int, float], float]:
    question = Question(JEV_QUESTION.format(query=query.strip().rstrip("?")))
    pairs = [(question, i, texts[i]) for i in ids]
    try:
        jev.check_budget(session, user_id, jev.estimate(session, user_id, pairs).usd)
        answers, spend = jev.ask(session, user_id, pairs, "search_rerank")
    except (jev.BudgetError, JevError, httpx.HTTPError) as exc:
        raise RerankError(str(exc)) from None
    return {i: answer.value for i, answer in zip(ids, answers)}, spend.usd


def rerank_with_cost(session: Session, user_id: int, query: str, ids: list[int], method: str) -> tuple[list[int], dict[int, float], float]:
    """Reorder the first RERANK_TOP_N ids by the reranker's score, best first
    (ties keep the incoming order). Items with too little text to judge stay
    behind the judged ones. Returns the new order, the score of each judged
    id, and what the call cost (it is in the ledger already). Raises
    RerankError when the service cannot be used; nothing is reordered then."""
    head, tail = ids[:RERANK_TOP_N], ids[RERANK_TOP_N:]
    texts = jev.item_texts(session, user_id, head)
    judged = [i for i in head if i in texts]
    if not judged:
        return ids, {}, 0.0
    scores, usd = (_voyage if method == "voyage" else _jev)(session, user_id, query, judged, texts)
    judged.sort(key=lambda i: -scores[i])  # stable
    return judged + [i for i in head if i not in texts] + tail, scores, usd


def rerank(session: Session, user_id: int, query: str, ids: list[int], method: str) -> tuple[list[int], dict[int, float]]:
    """`rerank_with_cost` without the cost (what the search bar wants)."""
    return rerank_with_cost(session, user_id, query, ids, method)[:2]

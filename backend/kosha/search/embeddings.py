"""Voyage embeddings: one vector per saved item, and query vectors.

Documents use the large model and queries the small one; the Voyage 4 models
share one vector space (https://docs.voyageai.com/docs/embeddings.md); the
check on this library is in docs/experiments/search_eval_2026-11-21.md.
"""
import hashlib
import time
from collections import OrderedDict
from decimal import Decimal

import httpx
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from kosha.config import EMBEDDING_DIM, get_settings
from kosha.models import ItemEmbedding, SpendLedger
from kosha.summaries import RULE_METHODS

# --- Tunables ---------------------------------------------------------------
VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
DOC_MODEL = "voyage-4-large"
QUERY_MODEL = "voyage-4-lite"
PRICE_PER_M_TOKENS_USD = {"voyage-4-large": 0.12, "voyage-4": 0.06, "voyage-4-lite": 0.02}
BODY_MAX_CHARS = 6000
# voyage-4-large takes 1,000 texts and 120K tokens a request. Budgeted in
# characters (about 3 per token at worst) to stay well inside the token limit.
BATCH_MAX_ITEMS = 128
BATCH_MAX_CHARS = 200_000
MAX_RETRIES = 6
RETRY_BASE_S = 2.0
TIMEOUT_S = 120
QUERY_CACHE_SIZE = 256
PROVIDER = "voyage"

# The parts of the embedded text (SEARCH_DESIGN.md C.3). Author, tags and the
# note are left out on purpose: they are qualifiers, and embedding the author
# would make "similar" mean "same author".
DOCS_SQL = text("""
SELECT i.id, i.title, left(i.body, :body_max) AS body, i.summary, i.summary_method,
  (SELECT string_agg(concat_ws(' ', q.title, left(q.body, :body_max)), E'\\n')
     FROM item_relations r JOIN items q ON q.id = r.to_item_id
    WHERE r.from_item_id = i.id AND r.relation = 'quotes') AS quoted,
  (SELECT string_agg(concat_ws(': ', l.title, l.description), E'\\n' ORDER BY l.position)
     FROM item_links l WHERE l.item_id = i.id AND l.title IS NOT NULL) AS links,
  e.content_hash AS embedded_hash
FROM items i
LEFT JOIN item_embeddings e ON e.item_id = i.id AND e.model = :model
WHERE i.user_id = :user_id AND i.is_saved
ORDER BY i.id
""")


class EmbeddingError(RuntimeError):
    pass


def document_text(row) -> str:
    """Title, body, quoted post, link titles and (only when it adds something
    the body does not say) the summary."""
    parts = [row.title, row.body]
    if row.quoted:
        parts.append(f"Quoted: {row.quoted}")
    if row.links:
        parts.append(f"Links: {row.links}")
    if row.summary and row.summary_method not in RULE_METHODS:
        parts.append(f"Summary: {row.summary}")
    return "\n".join(p.strip() for p in parts if p and p.strip())


def embed_texts(texts: list[str], model: str, input_type: str, client: httpx.Client | None = None) -> tuple[list[list[float]], int]:
    """One Voyage request, retried on rate limits and server errors.
    Returns (vectors in input order, tokens billed)."""
    key = get_settings().voyage_api_key
    if not key:
        raise EmbeddingError("VOYAGE_API_KEY is not set")
    payload = {"input": texts, "model": model, "input_type": input_type,
               "output_dimension": EMBEDDING_DIM, "output_dtype": "float"}
    post = (client or httpx).post
    for attempt in range(MAX_RETRIES):
        try:
            response = post(VOYAGE_URL, json=payload, headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT_S)
        except httpx.TransportError as exc:
            error = type(exc).__name__
        else:
            if response.status_code == 200:
                body = response.json()
                rows = sorted(body["data"], key=lambda d: d["index"])
                return [d["embedding"] for d in rows], body["usage"]["total_tokens"]
            if response.status_code != 429 and response.status_code < 500:
                raise EmbeddingError(f"Voyage answered {response.status_code}: {response.text[:300]}")
            error = f"HTTP {response.status_code}"
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_BASE_S * 2 ** attempt)
    raise EmbeddingError(f"Voyage still failing after {MAX_RETRIES} tries ({error})")


def record_spend(session: Session, user_id: int, model: str, operation: str, tokens: int, **meta) -> Decimal:
    """Ledger row at the list price. Voyage's free allowance may make the real
    charge zero; the ledger keeps what the call is worth."""
    amount = Decimal(str(PRICE_PER_M_TOKENS_USD[model])) * tokens / 1_000_000
    session.add(SpendLedger(user_id=user_id, provider=PROVIDER, operation=operation, units=tokens,
                            amount_usd=amount, meta={"model": model, "unit": "tokens", **meta}))
    return amount


def embed_items(session: Session, user_id: int, limit: int | None = None, echo=lambda line: None) -> dict:
    """Embed every saved item whose embedded text is new or changed. Each
    batch is committed with its ledger row, so an interrupted run resumes
    where it stopped and never pays twice."""
    todo = []
    rows = session.execute(DOCS_SQL, {"user_id": user_id, "model": DOC_MODEL, "body_max": BODY_MAX_CHARS}).all()
    for row in rows:
        doc = document_text(row)
        digest = hashlib.sha256(doc.encode()).hexdigest()
        if doc and digest != row.embedded_hash:
            todo.append((row.id, doc, digest))
    todo = todo[:limit] if limit else todo
    totals = {"items": len(rows), "embedded": 0, "tokens": 0, "cost_usd": 0.0, "requests": 0}
    batch: list = []

    def flush():
        vectors, tokens = embed_texts([doc for _, doc, _ in batch], DOC_MODEL, "document", client)
        values = [{"item_id": item_id, "model": DOC_MODEL, "content_hash": digest, "vector": vector}
                  for (item_id, _, digest), vector in zip(batch, vectors)]
        upsert = insert(ItemEmbedding).values(values)
        session.execute(upsert.on_conflict_do_update(
            index_elements=["item_id", "model"],
            set_={"content_hash": upsert.excluded.content_hash, "vector": upsert.excluded.vector}))
        cost = record_spend(session, user_id, DOC_MODEL, "embed_documents", tokens, items=len(batch))
        session.commit()
        totals["embedded"] += len(batch)
        totals["tokens"] += tokens
        totals["cost_usd"] += float(cost)
        totals["requests"] += 1
        echo(f"embedded {totals['embedded']}/{len(todo)} tokens={totals['tokens']}")
        batch.clear()

    with httpx.Client() as client:
        for entry in todo:
            if batch and (len(batch) >= BATCH_MAX_ITEMS or sum(len(d) for _, d, _ in batch) + len(entry[1]) > BATCH_MAX_CHARS):
                flush()
            batch.append(entry)
        if batch:
            flush()
    return totals


_query_cache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()


def query_vector(session: Session, user_id: int, query: str, model: str = QUERY_MODEL) -> list[float]:
    """The vector for a search text. A small LRU cache removes the Voyage
    round trip for repeated searches; only real calls reach the ledger."""
    key = (model, query.strip().lower())
    if key in _query_cache:
        _query_cache.move_to_end(key)
        return _query_cache[key]
    vectors, tokens = embed_texts([query], model, "query")
    record_spend(session, user_id, model, "embed_query", tokens)
    _query_cache[key] = vectors[0]
    if len(_query_cache) > QUERY_CACHE_SIZE:
        _query_cache.popitem(last=False)
    return vectors[0]

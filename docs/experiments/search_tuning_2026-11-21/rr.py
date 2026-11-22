"""Reranker scores for every pooled (query, item), in chunks of 50 so the timing
is what one search would see. Cached in rr_scores.json: {method: {qid: {id: score}}}."""
import sys
import time

from base import Q20, Q40, Q_LI, dump, load, open_session
from kosha import jev
from kosha.models import SpendLedger
from kosha.search import rerank as rr
from kosha.search.query import parse
from sqlalchemy import func, select

method = sys.argv[1]  # rerank-3 | rerank-3-lite | jev
session, uid = open_session()
pool = load("pool.json")
scores = load("rr_scores.json", {})
mine = scores.setdefault(method, {})
timing = load("rr_timing.json", {})
times = timing.setdefault(method, [])


def spent():
    return float(session.scalar(select(func.coalesce(func.sum(SpendLedger.amount_usd), 0)).where(
        SpendLedger.operation.in_(("rerank", "search_rerank")))))


before = spent()
for qid, _cat, q, _need in Q40 + Q20 + Q_LI:
    text = parse(q).rank_text
    todo = [i for i in pool[qid] if str(i) not in mine.get(qid, {})]
    texts = jev.item_texts(session, uid, todo)
    todo = [i for i in todo if i in texts]
    for start in range(0, len(todo), 50):
        chunk = todo[start:start + 50]
        t = time.perf_counter()
        if method == "jev":
            got, _ = rr._jev(session, uid, text, chunk, texts)
        else:
            rr.VOYAGE_RERANK_MODEL = method
            got, _ = rr._voyage(session, uid, text, chunk, texts)
        if len(chunk) == 50:
            times.append(round((time.perf_counter() - t) * 1000))
        mine.setdefault(qid, {}).update({str(i): s for i, s in got.items()})
        session.commit()
    dump("rr_scores.json", scores)
    dump("rr_timing.json", timing)
n = sum(len(v) for v in mine.values())
t = sorted(times)
print(f"{method}: {n} scores, spent ${spent() - before:.4f}; per 50 docs: median {t[len(t) // 2]} ms, p95 {t[int(len(t) * .95)]} ms over {len(t)} calls")

"""Run named engine configs over the query sets; metrics; the pool."""
import math
import time
from contextlib import contextmanager

from base import Q20, Q40, engine, load, parse
from kosha.models import Item

# The library grew while the experiments ran (LinkedIn saves arrived). The judged
# pool was drawn from the X items, so tuning runs are scoped to them.
FROZEN = [Item.source == "x"]

BEFORE = load("before_true.json")


@contextmanager
def config(**values):
    """Set engine constants for the duration of a block."""
    old = {k: getattr(engine, k) for k in values}
    for k, v in values.items():
        setattr(engine, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(engine, k, v)


def run_config(session, uid, queries, mode="hybrid", strong_only=False, top=50, repeat=0, scope=FROZEN, **values):
    """{qid: {ids, ms, n, notice}} for one config."""
    out = {}
    with config(**values):
        for qid, _cat, q, _need in queries:
            query = parse(q)
            ids, scores, notice, strong = engine.rank_all(session, uid, query, mode, scope)
            times = []
            for _ in range(repeat):
                t = time.perf_counter()
                engine.rank_all(session, uid, query, mode, scope)
                times.append((time.perf_counter() - t) * 1000)
            if strong_only:
                ids = [i for i in ids if i in strong]
            out[qid] = {"ids": ids[:top], "n": len(ids), "notice": notice, "n_strong": len(strong),
                        "ms": sorted(times)[len(times) // 2] if times else None}
    session.commit()
    return out


def metrics(ids, grades, n_returned=None):
    """nDCG@10, precision of the shown top 10, recall@30, holes (unjudged in top 10)."""
    rel = {int(i): g for i, g in grades.items()}
    top = ids[:10]
    dcg = sum((2 ** rel.get(i, 0) - 1) / math.log2(r + 2) for r, i in enumerate(top))
    ideal = sorted(rel.values(), reverse=True)[:10]
    idcg = sum((2 ** g - 1) / math.log2(r + 2) for r, g in enumerate(ideal))
    n_rel = sum(1 for g in rel.values() if g >= 1)
    return {
        "ndcg": dcg / idcg if idcg else None,
        "p10": sum(1 for i in top if rel.get(i, 0) >= 1) / len(top) if top else None,
        "r30": sum(1 for i in ids[:30] if rel.get(i, 0) >= 1) / n_rel if n_rel else None,
        "holes": sum(1 for i in top if i not in rel),
        "n": n_returned if n_returned is not None else len(ids),
    }


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else float("nan")


def summarize(result, judgments, queries, cats=None):
    """Mean metrics over the queries (negatives are left out of nDCG/recall by having no relevant item)."""
    rows = []
    for qid, cat, _q, _need in queries:
        if cats and cat not in cats:
            continue
        r = result[qid]
        rows.append(metrics(r["ids"], judgments.get(qid, {}), r.get("n")) | {"ms": r.get("ms")})
    return {"ndcg": mean(r["ndcg"] for r in rows), "p10": mean(r["p10"] for r in rows), "r30": mean(r["r30"] for r in rows),
            "holes": sum(r["holes"] for r in rows), "n": mean(r["n"] for r in rows), "ms": mean(r["ms"] for r in rows),
            "empty": sum(1 for r in rows if r["n"] == 0)}


def line(name, s):
    return (f"{name:38s} nDCG@10 {s['ndcg']:.3f}  P@10 {s['p10']:.3f}  R@30 {s['r30']:.3f}  n {s['n']:6.1f} "
            f"empty {s['empty']:2d} holes {s['holes']:3d}" + (f"  ms {s['ms']:.1f}" if s['ms'] == s['ms'] else ""))

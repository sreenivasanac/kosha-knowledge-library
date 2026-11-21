"""Final tables: before/after, per category, rerankers, whole library, the original 20."""
import json
from base import Q20, Q40, Q_LI, dump, load, load_vectors, open_session
from evalib import BEFORE, line, mean, metrics, run_config, summarize
from exp1 import CATS, before

load_vectors(); session, uid = open_session(); J = load("judgments.json"); RR = load("rr_scores.json")
out = {}


def show(name, result, queries=Q40, cats=False):
    s = summarize(result, J, queries)
    out[name] = s
    print(line(name, s))
    if cats:
        for c in CATS:
            cs = summarize(result, J, queries, [c]); out[f"{name} | {c}"] = cs
            print("   " + line(c, cs))


print("== X items only (the corpus the first evaluation and the pool saw)")
for mode in ("keyword", "semantic", "hybrid"):
    show(f"before {mode}", before(mode), cats=mode == "hybrid")
show("after keyword", run_config(session, uid, Q40, mode="keyword", repeat=5))
show("after semantic (all)", run_config(session, uid, Q40, mode="semantic", repeat=5))
full = run_config(session, uid, Q40, repeat=5)
show("after hybrid, weaker shown", full, cats=True)
strong = run_config(session, uid, Q40, strong_only=True, repeat=5)
show("after hybrid, default (strong)", strong, cats=True)
ms = sorted(v["ms"] for v in full.values())
print("latency ms (warm, in-process) median", round(ms[20], 1), "p95", round(ms[37], 1), "max", round(ms[-1], 1))
print("empty after strong:", [q for q, v in strong.items() if v["n"] == 0], "negatives n:", {q: v["n"] for q, v in strong.items() if q.startswith("n")})


def reranked(base, method, keep=None, head=50):
    res = {}
    for qid, v in base.items():
        sc = RR[method].get(qid, {})
        ids = v["ids"][:head]
        judged = sorted([i for i in ids if str(i) in sc], key=lambda i: -sc[str(i)])
        ids = judged + [i for i in ids if str(i) not in sc]
        if keep is not None:
            ids = [i for i in ids if sc.get(str(i), 0) >= keep]
        res[qid] = {"ids": ids, "n": len(ids), "ms": None}
    return res


print("== rerankers on the top 50 of 'after hybrid, weaker shown'")
for method in ("rerank-3-lite", "rerank-3", "jev"):
    show(f"{method}: reorder top 50", reranked(full, method))
    for keep in (0.3, 0.5, 0.7, 0.9):
        r = reranked(full, method, keep)
        neg = mean(v["n"] for q, v in r.items() if q.startswith("n"))
        print("   " + line(f"keep score >= {keep}", summarize(r, J, Q40)), f"negatives n {neg:.1f}")
        out[f"{method} keep {keep}"] = summarize(r, J, Q40) | {"neg_n": neg}
    for c in CATS:
        cs = summarize(reranked(full, method), J, Q40, [c]); out[f"{method} | {c}"] = cs
        print("      " + line(c, cs))

print("== whole library (X + LinkedIn), adopted defaults")
show("whole: hybrid, weaker shown", run_config(session, uid, Q40, scope=[]))
show("whole: hybrid, default (strong)", run_config(session, uid, Q40, scope=[], strong_only=True))
li_all = run_config(session, uid, Q_LI, scope=[]); li = run_config(session, uid, Q_LI, scope=[], strong_only=True)
show("LinkedIn queries, weaker shown", li_all, Q_LI); show("LinkedIn queries, default", li, Q_LI)
show("LinkedIn queries, jev rerank", reranked(li_all, "jev"), Q_LI)
for qid, _c, q, _n in Q_LI:
    print("  ", qid, q[:45], metrics(li[qid]["ids"], J[qid], li[qid]["n"]))


def verdict(ids, grades, n):
    top = [grades.get(str(i), 0) for i in ids[:5]]
    has = any(g >= 1 for g in grades.values())
    if not has:
        return "right (nothing to find)" if n <= 3 else "poor (pads)"
    if 2 in top or sum(1 for g in top if g >= 1) >= 3:
        return "good"
    return "mixed" if any(top) else "poor"


print("== the first evaluation's text queries (verdict from LLM grades of the top 5)")
q20_after = run_config(session, uid, Q20, scope=[], strong_only=True, repeat=3)
q20_all = run_config(session, uid, Q20, scope=[])
table = []
for qid, _c, q, _n in Q20:
    g = J.get(qid, {})
    b = BEFORE[qid]["hybrid"]
    row = (qid, q[:44], verdict(b["ids"], g, b["n"]), b["n"], verdict(q20_after[qid]["ids"], g, q20_after[qid]["n"]), q20_after[qid]["n"],
           q20_all[qid]["n"], verdict(reranked(q20_all, "jev", 0.5)[qid]["ids"], g, len(reranked(q20_all, "jev", 0.5)[qid]["ids"])), round(q20_after[qid]["ms"]))
    table.append(row); print("  ", row)
out["q20"] = table
dump("final.json", out)

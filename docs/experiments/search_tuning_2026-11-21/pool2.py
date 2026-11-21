"""Second pool: the adopted config's top 50 (frozen X corpus and whole library), and the LinkedIn queries."""
from base import Q20, Q40, Q_LI, dump, load, load_vectors, open_session, save_vectors
from evalib import run_config
load_vectors(); session, uid = open_session()
pool = load("pool.json")
ALL = Q40 + Q20
for scope in (None, []):
    r = run_config(session, uid, ALL + (Q_LI if scope == [] else []), **({} if scope is None else {"scope": scope}))
    for qid, v in r.items():
        pool[qid] = sorted(set(pool.get(qid, [])) | set(v["ids"][:50]))
for mode in ("keyword", "semantic"):
    for qid, v in run_config(session, uid, Q_LI, mode=mode, scope=[]).items():
        pool[qid] = sorted(set(pool[qid]) | set(v["ids"][:30]))
save_vectors(); dump("pool.json", pool)
J = load("judgments.json")
print("pool", sum(len(v) for v in pool.values()), "unjudged", sum(1 for q, ids in pool.items() for i in ids if str(i) not in J.get(q, {})))

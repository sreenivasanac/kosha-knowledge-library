"""Rankings of the untouched engine: the 'before'. Run once before any change."""
from base import Q20, Q40, dump, load_vectors, open_session, run, save_vectors

load_vectors()
session, uid = open_session()
out = {}
for qid, _cat, q, _need in Q40 + Q20:
    out[qid] = {}
    for mode in ("keyword", "semantic", "hybrid"):
        ids, ms, full = run(session, uid, q, mode, top=50, repeat=3)
        out[qid][mode] = {"ids": ids, "ms": round(ms, 1), "n": len(full[0]), "notice": full[2]}
    print(qid, {m: (v["n"], v["ms"]) for m, v in out[qid].items()})
save_vectors()
dump("before.json", out)

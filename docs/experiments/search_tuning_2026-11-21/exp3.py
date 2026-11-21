"""Experiment 3: on the adopted retrieval (coverage 0.35, no semantic floor):
ranking details, then the strong cut."""
import sys
from base import Q40, engine, load, load_vectors, open_session, parse
from evalib import FROZEN, line, mean, metrics, run_config, summarize
from exp1 import CATS, W

load_vectors(); session, uid = open_session(); J = load("judgments.json")
part = sys.argv[1]
if part == "rank":
    def show(name, **kw):
        print(line(name, summarize(run_config(session, uid, Q40, **kw), J, Q40)))
    show("adopted retrieval")
    for cov in (0.25, 0.3, 0.4, 0.45):
        show(f"coverage {cov}", KEYWORD_MIN_COVERAGE=cov)
    for n in (100, 400):
        show(f"ranker top n {n}", RANKER_TOP_N=n)
    for kw, sem in ((1, 1), (2, 1), (1, 2), (1.5, 1)):
        show(f"all weights {kw}:{sem}", **W(kw, sem))
    for short in ((2, 1), (1, 1), (1, 2), (3, 1)):
        show(f"short/identifier weights {short}", WEIGHTS_IDENTIFIER=short)
    for long_ in ((1, 1.5), (1, 1), (1, 2), (1, 3), (1.5, 1)):
        show(f"long weights {long_}", WEIGHTS_LONG=long_)
    for n in (5, 6, 8):
        show(f"long from {n} words", LONG_QUERY_WORDS=n)
    for k in (10, 30, 100):
        show(f"k {k}", RRF_K=k)
    for b in (0.5, 2.0):
        show(f"phrase boost {b}", PHRASE_BOOST=b)
    for w in ("{0.1, 0.2, 0.4, 1.0}", "{0.1, 0.2, 1.0, 1.0}", "{0.1, 0.2, 0.4, 0.4}", "{1, 1, 1, 1}", "{0.05, 0.1, 0.2, 1.0}"):
        show(f"field weights D,C,B,A {w}", TS_WEIGHTS=w)
    for a in (0.0, 0.3, 1.0):
        show(f"any-word share {a}", ANY_WORD_WEIGHT=a)
    for norm in (0, 2, 32):
        show(f"ts normalization {norm}", TS_NORMALIZATION=norm)
    for e in (0.03, 0.1, 0.3):
        show(f"engagement {e}", ENGAGEMENT_WEIGHT=e)
    for r in (0.03, 0.1, 0.3):
        show(f"recency {r}", RECENCY_WEIGHT=r)
else:  # the strong cut: what is shown by default
    rows = []
    for cov in (1.0, 0.8, 0.6):
        for sim in (0.35, 0.4, 0.45, 0.5):
            for margin in (0.05, 0.08, 0.12, 0.2, 1.0):
                r = run_config(session, uid, Q40, strong_only=True, STRONG_COVERAGE=cov, STRONG_SIMILARITY=sim, STRONG_MARGIN=margin)
                s = summarize(r, J, Q40)
                neg = summarize(r, J, Q40, ["negative"])
                # share of the shown results (first 50) that are relevant, over non-negative queries
                shown = [(sum(1 for i in v["ids"] if J[q].get(str(i), 0) >= 1), len(v["ids"])) for q, v in r.items() if not q.startswith("n")]
                prec = sum(a for a, _ in shown) / max(1, sum(b for _, b in shown))
                rows.append((s["ndcg"], f"cov {cov} sim {sim} margin {margin:4}: nDCG {s['ndcg']:.3f} P@10 {s['p10']:.3f} R@30 {s['r30']:.3f} "
                             f"n {s['n']:5.1f} empty {s['empty']} | negatives: n {neg['n']:.1f} | precision of shown top 50 {prec:.3f}"))
    for _, t in sorted(rows, reverse=True):
        print(t)

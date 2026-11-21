"""Experiment 1: base methods and single-factor variants of the hybrid."""
import sys
from base import Q20, Q40, load, load_vectors, open_session, save_vectors
from evalib import BEFORE, line, run_config, summarize

load_vectors()
session, uid = open_session()
J = load("judgments.json")
CATS = ["known-item", "topical", "project", "typo", "short", "long", "qualifier", "negative"]


def show(name, result, cats=False):
    print(line(name, summarize(result, J, Q40)))
    if cats:
        for c in CATS:
            print("   " + line(c, summarize(result, J, Q40, [c])))


def before(mode):
    return {qid: {"ids": r[mode]["ids"], "n": r[mode]["n"], "ms": r[mode]["ms"]} for qid, r in BEFORE.items()}


W = lambda kw, sem: dict(WEIGHTS_DEFAULT=(kw, sem), WEIGHTS_IDENTIFIER=(kw, sem), WEIGHTS_LONG=(kw, sem))  # noqa: E731
if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    print("== before (original engine)")
    for mode in ("keyword", "semantic", "hybrid"):
        show(f"before {mode}", before(mode), cats=mode == "hybrid")
    print("== new engine, same constants (adds fuzzy fallback, idf stop-word drop)")
    for mode in ("keyword", "semantic", "hybrid"):
        show(f"new {mode}", run_config(session, uid, Q40, mode=mode, repeat=3))
    print("== keyword coverage (AND=1.0 vs partial), hybrid")
    for cov in (1.0, 0.8, 0.65, 0.5, 0.35):
        show(f"min_coverage {cov}", run_config(session, uid, Q40, KEYWORD_MIN_COVERAGE=cov))
    print("== semantic floor / margin, hybrid")
    for floor, margin in ((0.30, 0.15), (0.0, 1.0), (0.25, 0.15), (0.25, 0.20), (0.30, 0.20), (0.30, 0.10), (0.35, 0.15), (0.20, 0.25)):
        show(f"floor {floor} margin {margin}", run_config(session, uid, Q40, SEMANTIC_MIN_SIMILARITY=floor, SEMANTIC_MARGIN=margin))
    print("== semantic only, floor / margin")
    for floor, margin in ((0.30, 0.15), (0.0, 1.0), (0.25, 0.20)):
        show(f"sem floor {floor} margin {margin}", run_config(session, uid, Q40, mode="semantic", SEMANTIC_MIN_SIMILARITY=floor, SEMANTIC_MARGIN=margin))
    print("== fixed weights (kw, sem), hybrid")
    for kw, sem in ((1, 1), (2, 1), (1, 2), (1, 3), (1, 1.5), (0.5, 1)):
        show(f"weights {kw}:{sem}", run_config(session, uid, Q40, **W(kw, sem)))
    print("== rrf k")
    for k in (10, 20, 40, 60, 100):
        show(f"k {k}", run_config(session, uid, Q40, RRF_K=k))
    save_vectors()

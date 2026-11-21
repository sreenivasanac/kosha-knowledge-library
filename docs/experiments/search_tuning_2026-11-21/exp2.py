"""Experiment 2: coverage x semantic cut x weights (full lists)."""
from base import Q40, load, load_vectors, open_session
from evalib import line, run_config, summarize
from exp1 import W

load_vectors(); session, uid = open_session(); J = load("judgments.json")
rows = []
for cov in (1.0, 0.65, 0.5, 0.35):
    for floor, margin in ((0.30, 0.15), (0.25, 0.20), (0.0, 1.0)):
        for wname, w in (("shape", {}), ("1:1", W(1, 1)), ("1:2", W(1, 2))):
            r = run_config(session, uid, Q40, KEYWORD_MIN_COVERAGE=cov, SEMANTIC_MIN_SIMILARITY=floor, SEMANTIC_MARGIN=margin, **w)
            s = summarize(r, J, Q40)
            rows.append((s["ndcg"], line(f"cov {cov} sem {floor}/{margin} w {wname}", s)))
for _, text in sorted(rows, reverse=True):
    print(text)

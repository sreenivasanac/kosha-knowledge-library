"""LLM relevance judgments (graded 0-2) for pooled items. Cached in judgments.json;
only missing (query, item) pairs are asked. Every request is a spend_ledger row."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
from sqlalchemy import text

from base import HERE, Q20, Q40, Q_LI, dump, load, open_session
from kosha.models import SpendLedger

MODEL = "gpt-5.4"
PRICE = {"flex": (1.25, 0.125, 7.5), "default": (2.5, 0.25, 15.0)}  # $/M: input, cached input, output
TEXT_CHARS = 2000
BATCH = 12
BUDGET_USD = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1][0].isdigit() else 0.5
DRY = "--dry" in sys.argv
BATCH_API = "--batch" in sys.argv  # submit to the Batch API (half price); collect with --collect <batch id>
SYSTEM = """You judge search relevance for a personal bookmark library (saved X posts). Given the searcher's NEED and a list of posts, grade each post:
2 = directly satisfies the need: it is the item sought, or it is squarely about the need and useful for it.
1 = partially relevant: clearly related and contains something usable for the need, but not squarely about it.
0 = not relevant: off-topic, or only in the same broad area (e.g. generic AI content for a specific AI question).
Judge only from the text given. If the need names an author, type or site, a post that does not fit that is at most 1.
Answer with JSON only: {"grades": {"<id>": <0|1|2>, ...}} with every id given."""
TEXT_SQL = text("""
SELECT i.id, concat_ws(E'\\n',
    '@' || coalesce(i.author_handle, '?') || ' (' || coalesce(i.author_name, '') || ') ' || coalesce(to_char(i.posted_at, 'YYYY-MM'), '')
      || ' types: ' || array_to_string(i.content_types, ','),
    left(concat_ws(E'\\n',
      CASE WHEN i.body IS NULL OR position(i.title in i.body) <> 1 THEN i.title END, i.body,
      (SELECT 'Quoted: ' || string_agg(concat_ws(' ', q.title, left(q.body, :max)), E'\\n')
         FROM item_relations r JOIN items q ON q.id = r.to_item_id WHERE r.from_item_id = i.id AND r.relation = 'quotes'),
      (SELECT 'Links: ' || string_agg(concat_ws(' ', l.domain, l.title), E'\\n' ORDER BY l.position) FROM item_links l WHERE l.item_id = i.id),
      (SELECT string_agg(concat_ws(' ', m.alt_text, left(m.extract_text, :max)), E'\\n' ORDER BY m.position)
         FROM item_media m WHERE m.item_id = i.id)), :max)) AS text
FROM items i WHERE i.id = ANY(:ids)""")


def collect(batch_id, key, session, uid, judgments):
    """Read a finished batch into judgments.json and write its ledger row (batch prices: half the standard ones)."""
    auth = {"Authorization": f"Bearer {key}"}
    b = httpx.get(f"https://api.openai.com/v1/batches/{batch_id}", headers=auth, timeout=60).json()
    print(b["status"], b.get("request_counts"))
    if b["status"] != "completed":
        return
    rows = httpx.get(f"https://api.openai.com/v1/files/{b['output_file_id']}/content", headers=auth, timeout=300).text.splitlines()
    tokens_in = tokens_out = bad = 0
    for row in rows:
        row = json.loads(row)
        qid = row["custom_id"].split("|")[0]
        body = (row.get("response") or {}).get("body") or {}
        if "usage" not in body:
            bad += 1
            continue
        tokens_in += body["usage"]["prompt_tokens"]
        tokens_out += body["usage"]["completion_tokens"]
        try:
            grades = json.loads(body["choices"][0]["message"]["content"])["grades"]
        except (ValueError, KeyError):
            bad += 1
            continue
        judgments.setdefault(qid, {}).update({str(i): int(g) for i, g in grades.items() if g in (0, 1, 2)})
    usd = (tokens_in * PRICE["default"][0] + tokens_out * PRICE["default"][2]) / 2 / 1e6
    session.add(SpendLedger(user_id=uid, provider="openai", operation="search_eval_judge", units=tokens_in + tokens_out,
                            amount_usd=Decimal(f"{usd:.6f}"),
                            meta={"model": MODEL, "unit": "tokens", "tier": "batch", "batch": batch_id, "requests": len(rows)}))
    session.commit()
    dump("judgments.json", judgments)
    print(f"collected {len(rows)} requests ({bad} unusable), {tokens_in} in / {tokens_out} out, ${usd:.4f}")


def main():
    import os
    key = os.environ["OPENAI_API_KEY"]
    session, uid = open_session()
    pool = load("pool.json")
    judgments = load("judgments.json", {})
    if "--collect" in sys.argv:
        return collect(sys.argv[sys.argv.index("--collect") + 1], key, session, uid, judgments)
    needs = {qid: (q, need) for qid, _c, q, need in Q40 + Q20 + Q_LI}
    jobs = []
    for qid, ids in pool.items():
        missing = [i for i in ids if str(i) not in judgments.get(qid, {})]
        texts = dict(session.execute(TEXT_SQL, {"ids": missing, "max": TEXT_CHARS}).all()) if missing else {}
        for start in range(0, len(missing), BATCH):
            jobs.append((qid, [(i, texts[i]) for i in missing[start:start + BATCH]]))
    chars = sum(len(t) for _, batch in jobs for _, t in batch) + len(jobs) * (len(SYSTEM) + 300)
    est = chars / 3.6 / 1e6 * PRICE["default"][0] + sum(len(b) for _, b in jobs) * 9 / 1e6 * PRICE["default"][2]
    print(f"{len(jobs)} requests, {sum(len(b) for _, b in jobs)} items, about {chars / 3.6:,.0f} input tokens, "
          f"est ${est:.2f} at the standard tier (half that on flex); budget ${BUDGET_USD}")
    if DRY or not jobs:
        return
    if BATCH_API:
        lines = []
        for n, (qid, batch) in enumerate(jobs):
            q, need = needs[qid]
            user = f"NEED: {need}\nThe search typed: {q}\n\nPOSTS:\n" + "\n\n".join(f"[id {i}]\n{t}" for i, t in batch)
            lines.append(json.dumps({"custom_id": f"{qid}|{n}", "method": "POST", "url": "/v1/chat/completions", "body": {
                "model": MODEL, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                "response_format": {"type": "json_object"}, "reasoning_effort": "none"}}))
        auth = {"Authorization": f"Bearer {key}"}
        up = httpx.post("https://api.openai.com/v1/files", headers=auth, data={"purpose": "batch"},
                        files={"file": ("judge.jsonl", "\n".join(lines).encode())}, timeout=120).json()
        made = httpx.post("https://api.openai.com/v1/batches", headers=auth, timeout=60,
                          json={"input_file_id": up["id"], "endpoint": "/v1/chat/completions", "completion_window": "24h"}).json()
        print("batch", made.get("id"), made.get("status"), made.get("error"))
        return
    spent = [0.0]

    def ask(job):
        qid, batch = job
        if spent[0] >= BUDGET_USD:
            return qid, None, None
        q, need = needs[qid]
        user = f"NEED: {need}\nThe search typed: {q}\n\nPOSTS:\n" + "\n\n".join(f"[id {i}]\n{t}" for i, t in batch)
        body = {"model": MODEL, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                "response_format": {"type": "json_object"}, "reasoning_effort": "none"}
        for tier in ("default", "default"):
            try:
                r = httpx.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                               json=body | ({"service_tier": "flex"} if tier == "flex" else {}), timeout=180)
            except httpx.TransportError:
                continue
            if r.status_code == 200:
                break
        else:
            print("failed", qid, r.status_code, r.text[:200])
            return qid, None, None
        data = r.json()
        usage = data["usage"]
        cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
        p_in, p_cached, p_out = PRICE["flex" if data.get("service_tier") == "flex" else "default"]
        usd = ((usage["prompt_tokens"] - cached) * p_in + cached * p_cached + usage["completion_tokens"] * p_out) / 1e6
        spent[0] += usd
        try:
            grades = json.loads(data["choices"][0]["message"]["content"])["grades"]
        except (ValueError, KeyError):
            grades = {}
        ledger = dict(user_id=uid, provider="openai", operation="search_eval_judge", units=usage["total_tokens"],
                      amount_usd=Decimal(f"{usd:.6f}"),
                      meta={"model": MODEL, "unit": "tokens", "tier": data.get("service_tier"), "items": len(batch), "query": qid})
        return qid, {str(i): int(grades[str(i)]) for i, _ in batch if str(i) in grades and grades[str(i)] in (0, 1, 2)}, ledger

    with ThreadPoolExecutor(8) as pool_:
        for n, (qid, grades, ledger) in enumerate(pool_.map(ask, jobs)):
            if grades is None:
                continue
            judgments.setdefault(qid, {}).update(grades)
            session.add(SpendLedger(**ledger))
            if n % 20 == 0:
                session.commit()
                dump("judgments.json", judgments)
    session.commit()
    dump("judgments.json", judgments)
    left = sum(1 for qid, ids in pool.items() for i in ids if str(i) not in judgments.get(qid, {}))
    print(f"spent ${spent[0]:.4f}; judged {sum(len(v) for v in judgments.values())}; still missing {left}")


main()

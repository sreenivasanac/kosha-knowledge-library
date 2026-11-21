"""Label runs (SEARCH_DESIGN.md G.3): an experiment gets its own tag namespace,
so a strategy builds a trial tree over the same items without touching the
real one (`main`). A run owns its ledger rows (`meta.run_id`), can be compared
with another run, and can be promoted into `main` as proposed tags.
"""
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from kosha.config import DEFAULT_NAMESPACE
from kosha.jev import run_budget, run_spend
from kosha.models import ItemTag, LabelRun, SpendLedger
from kosha.pipeline import writes
from kosha.prefs import load_settings

NAMESPACE_PREFIX = "run-"

COVERAGE_SQL = text("""
SELECT count(*) AS items,
  count(*) FILTER (WHERE EXISTS (SELECT 1 FROM item_tags t WHERE t.item_id = i.id AND t.namespace = :ns
                                   AND t.role = 'primary' AND NOT t.removed_by_user)) AS with_primary,
  count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM item_tags t WHERE t.item_id = i.id AND t.namespace = :ns
                                       AND NOT t.removed_by_user)) AS untagged,
  (SELECT count(*) FROM tags g WHERE g.user_id = :u AND g.namespace = :ns) AS tags,
  (SELECT count(*) FROM item_tags t JOIN items x ON x.id = t.item_id
    WHERE x.user_id = :u AND t.namespace = :ns AND NOT t.removed_by_user) AS tag_rows
FROM items i WHERE i.user_id = :u AND i.is_saved AND i.archived_at IS NULL
""")


class RunError(ValueError):
    pass


def get_run(session: Session, user_id: int, run_id: int) -> LabelRun:
    run = session.get(LabelRun, run_id)
    if run is None or run.user_id != user_id:
        raise RunError(f"No label run {run_id}")
    return run


def start(session: Session, user_id: int, name: str, strategy: str, budget_usd: float | None = None) -> LabelRun:
    """Open a run with an empty namespace of its own. `query` and `tags`
    calls that pass this run's id read and write that namespace. The budget
    kept is `jev.run_budget`: never None, never over `jev_run_max_budget_usd`."""
    budget_usd = run_budget(load_settings(session, user_id), budget_usd)  # the owner's maximum wins over the agent's wish
    run = LabelRun(user_id=user_id, strategy=strategy, namespace="", params={"name": name, "budget_usd": budget_usd})
    session.add(run)
    session.flush()
    run.namespace = f"{NAMESPACE_PREFIX}{run.id}"
    session.commit()
    return run


def status(session: Session, user_id: int, run: LabelRun) -> dict:
    """Coverage (share of saved items with a primary tag in the run's
    namespace), counts and spend. `uncached_usd` is what the run's Jev calls
    would have cost with an empty cache, so a run that reused another run's
    answers is not flattered."""
    row = session.execute(COVERAGE_SQL, {"u": user_id, "ns": run.namespace}).one()
    uncached = session.scalar(select(func.coalesce(func.sum(SpendLedger.meta["uncached_usd"].as_float()), 0.0)).where(
        SpendLedger.user_id == user_id, SpendLedger.meta["run_id"].as_integer() == run.id))
    end = run.finished_at or datetime.now(UTC)
    return {"run": run.id, "name": (run.params or {}).get("name"), "strategy": run.strategy, "namespace": run.namespace,
            "status": run.status, "items": row.items, "with_primary": row.with_primary,
            "coverage": round(row.with_primary / row.items, 4) if row.items else 0.0, "untagged": row.untagged,
            "tags": row.tags, "tag_rows": row.tag_rows, "spend_usd": round(run_spend(session, user_id, run.id), 6),
            "uncached_usd": round(float(uncached), 6), "budget_usd": (run.params or {}).get("budget_usd"),
            "minutes": round((end - run.started_at).total_seconds() / 60, 1)}


def close(session: Session, user_id: int, run: LabelRun) -> dict:
    """Finish a run and keep its final coverage and spend on the row."""
    if run.status == "running":
        run.status, run.finished_at = "finished", datetime.now(UTC)
    result = status(session, user_id, run)
    run.coverage, run.spend_usd = result["coverage"], Decimal(str(result["spend_usd"]))
    session.commit()
    return result


def compare(session: Session, user_id: int, run_ids: list[int]) -> str:
    """The same measures side by side, one column per run."""
    stats = [status(session, user_id, get_run(session, user_id, i)) for i in run_ids]
    rows = ["run", "name", "strategy", "status", "coverage", "with_primary", "untagged", "tags", "tag_rows",
            "spend_usd", "uncached_usd", "minutes"]
    return "\n".join(f"{key}: " + " | ".join(str(s[key]) for s in stats) for key in rows)


def promote(session: Session, user_id: int, run: LabelRun, confirm: str | None = None) -> dict:
    """Copy a run's tree and tag rows into `main`. New tags arrive proposed;
    rows arrive as agent rows, so tags the user removed or attached by hand
    are left alone. Always a preview first: the confirm token is bound to the
    run and to the exact tags and (item, tag) pairs that would be copied."""
    paths = writes.tag_paths(session, user_id, run.namespace)
    rows = list(session.scalars(select(ItemTag).where(ItemTag.namespace == run.namespace, ~ItemTag.removed_by_user,
                                                      ItemTag.tag_id.in_(paths))))
    new_tags = [path for _, path in paths.values() if writes.find_tag(session, user_id, DEFAULT_NAMESPACE, path) is None]
    # Bound to exactly what would be copied: every tag path and every (item, tag path) pair.
    digest = writes.digest_of("promote", run.id, sorted(path for _, path in paths.values()),
                              sorted((r.item_id, paths[r.tag_id][1]) for r in rows))
    preview = {"run": run.id, "tags": len(paths), "new_tags": len(new_tags), "tag_rows": len(rows),
               "items": len({r.item_id for r in rows})}
    if not writes.confirm_ok(user_id, digest, confirm):
        return {**preview, "applied": False, "sample_new_tags": sorted(new_tags)[:5], "confirm": writes.confirm_token(user_id, digest)}
    main_id = {}
    for tag, path in sorted(paths.values(), key=lambda pair: pair[1].count("/")):  # parents first
        target = writes.find_tag(session, user_id, DEFAULT_NAMESPACE, path) or writes.create_tag_path(session, user_id, DEFAULT_NAMESPACE, path)
        if target.description is None:
            target.description = tag.description
        main_id[tag.id] = target.id
    counts, batch_id = writes.apply_tags(session, user_id, DEFAULT_NAMESPACE,
                                         [(r.item_id, main_id[r.tag_id], r.role, r.confidence) for r in rows])
    session.commit()
    return {**preview, "applied": True, **counts, "batch_id": batch_id}

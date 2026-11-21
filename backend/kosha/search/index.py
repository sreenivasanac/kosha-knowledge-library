"""Keeps the search columns of `items` (`search_text`, `tsv`, `tsv_extra`).

They combine the item's own text with its quoted post, link titles and media
extracts, so they are rewritten here whenever any of those may have changed:
after ingest, after a note or summary edit, and by `kosha reindex`.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

# Weights follow SEARCH_DESIGN.md B.6. English items are stemmed; everything
# else is indexed as plain words. Author names are never stemmed.
REINDEX_SQL = text("""
UPDATE items i SET
  search_text = concat_ws(E'\\n', i.title, i.body, x.quoted, x.link_titles, i.note),
  tsv = setweight(to_tsvector(x.cfg, coalesce(i.title, '')), 'A')
     || setweight(to_tsvector('simple', coalesce(i.author_name, '') || ' ' || coalesce(i.author_handle, '')), 'A')
     || setweight(to_tsvector(x.cfg, coalesce(i.body, '')), 'B')
     || setweight(to_tsvector(x.cfg, concat_ws(' ', x.quoted, x.link_text)), 'C')
     || setweight(to_tsvector(x.cfg, coalesce(i.summary, '')), 'D'),
  tsv_extra = setweight(to_tsvector(x.cfg, coalesce(i.note, '')), 'A')
     || setweight(to_tsvector(x.cfg, coalesce(x.transcripts, '')), 'B')
     || setweight(to_tsvector(x.cfg, coalesce(x.image_text, '')), 'C')
FROM (
  SELECT s.id,
    CASE WHEN s.lang = 'en' THEN 'english'::regconfig ELSE 'simple'::regconfig END AS cfg,
    (SELECT string_agg(concat_ws(' ', q.title, q.body), E'\\n')
       FROM item_relations r JOIN items q ON q.id = r.to_item_id
      WHERE r.from_item_id = s.id AND r.relation = 'quotes') AS quoted,
    (SELECT string_agg(l.title, E'\\n' ORDER BY l.position) FROM item_links l WHERE l.item_id = s.id) AS link_titles,
    (SELECT string_agg(concat_ws(' ', l.title, l.description), E'\\n' ORDER BY l.position)
       FROM item_links l WHERE l.item_id = s.id) AS link_text,
    (SELECT string_agg(m.extract_text, E'\\n' ORDER BY m.position)
       FROM item_media m WHERE m.item_id = s.id AND m.extract_kind = 'transcript') AS transcripts,
    (SELECT string_agg(concat_ws(' ', m.alt_text,
              CASE WHEN m.extract_kind IS DISTINCT FROM 'transcript' THEN m.extract_text END), E'\\n' ORDER BY m.position)
       FROM item_media m WHERE m.item_id = s.id) AS image_text
  FROM items s
  WHERE CAST(:ids AS bigint[]) IS NULL OR s.id = ANY(:ids)
     -- an item's quoted text is part of the item that quotes it
     OR s.id IN (SELECT r.from_item_id FROM item_relations r WHERE r.relation = 'quotes' AND r.to_item_id = ANY(:ids))
) x
WHERE x.id = i.id
""")


def reindex(session: Session, item_ids: list[int] | None = None) -> int:
    """Rewrite the search columns of the given items (and of items that quote
    them), or of every item when `item_ids` is None. Returns rows written."""
    if item_ids is not None and not item_ids:
        return 0
    session.flush()
    return session.execute(REINDEX_SQL, {"ids": item_ids}).rowcount

"""Shared harness bits: session, user, disk-cached query vectors, run a config."""
import json
import pickle
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from sqlalchemy import select  # noqa: E402

from kosha.db import SessionLocal  # noqa: E402
from kosha.models import User  # noqa: E402
from kosha.search import embeddings, engine  # noqa: E402
from kosha.search.query import parse  # noqa: E402
from queries import Q20, Q40, Q_LI  # noqa: E402

VEC_FILE = HERE / "qvec.pkl"


def load_vectors():
    if VEC_FILE.exists():
        embeddings._query_cache.update(pickle.loads(VEC_FILE.read_bytes()))


def save_vectors():
    VEC_FILE.write_bytes(pickle.dumps(dict(embeddings._query_cache)))


def open_session():
    session = SessionLocal()
    user_id = session.scalar(select(User.id).order_by(User.id).limit(1))
    return session, user_id


def run(session, user_id, q, mode, top=50, repeat=1):
    """ids best first and the median ms of `repeat` warm runs (query vector cached)."""
    query = parse(q)
    engine.rank(session, user_id, query, mode, [])  # warm: embeds the query if new
    session.commit()
    times = []
    for _ in range(repeat):
        t = time.perf_counter()
        out = engine.rank(session, user_id, query, mode, [])
        times.append((time.perf_counter() - t) * 1000)
    ids = out[0]
    return ids[:top], sorted(times)[len(times) // 2], out


def dump(path, obj):
    (HERE / path).write_text(json.dumps(obj))


def load(path, default=None):
    p = HERE / path
    return json.loads(p.read_text()) if p.exists() else default

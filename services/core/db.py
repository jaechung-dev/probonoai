from contextlib import contextmanager

import psycopg2
import psycopg2.extensions

from services.core.settings import settings

# Module-level connection reused across warm Lambda invocations.
# Each Lambda container handles one request at a time, so a single
# connection is safe. On cold start we pay the TCP+TLS cost once;
# subsequent requests skip it entirely.
_conn: psycopg2.extensions.connection | None = None


def _get_conn() -> psycopg2.extensions.connection:
    global _conn
    if _conn is None or _conn.closed != 0:
        _conn = psycopg2.connect(settings.DATABASE_URL)
    return _conn


@contextmanager
def get_db():
    global _conn
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        if _conn is not None and _conn.closed != 0:
            _conn = None
        raise

from contextlib import contextmanager
import select

import psycopg2
import psycopg2.extensions

from services.core.settings import settings

_conn: psycopg2.extensions.connection | None = None


def _connection_alive(conn: psycopg2.extensions.connection) -> bool:
    """Non-blocking check: returns False if Supabase closed the TCP connection.
    Uses select() with zero timeout — no network round-trip needed."""
    if conn.closed != 0:
        return False
    try:
        r, _, _ = select.select([conn.fileno()], [], [], 0)
        # Readable with no pending query means a FIN/RST arrived from Supabase.
        return not r
    except Exception:
        return False


def _get_conn() -> psycopg2.extensions.connection:
    global _conn
    if _conn is None or not _connection_alive(_conn):
        print(f"DB_CONNECT reuse={_conn is not None} closed={_conn.closed if _conn else 'N/A'}", flush=True)
        _conn = psycopg2.connect(settings.DATABASE_URL)
    else:
        print("DB_REUSE", flush=True)
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
            _conn = None
        if _conn is not None and _conn.closed != 0:
            _conn = None
        raise

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
        import time as _t
        t0 = _t.monotonic()
        _conn = psycopg2.connect(settings.DATABASE_URL)
        print(f"DB_CONNECT {(_t.monotonic()-t0)*1000:.0f}ms", flush=True)
    return _conn


@contextmanager
def get_db():
    import time as _t
    global _conn
    conn = _get_conn()
    try:
        t0 = _t.monotonic()
        yield conn
        t1 = _t.monotonic()
        conn.commit()
        t2 = _t.monotonic()
        print(f"DB_QUERY {(t1-t0)*1000:.0f}ms COMMIT {(t2-t1)*1000:.0f}ms", flush=True)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            _conn = None
        if _conn is not None and _conn.closed != 0:
            _conn = None
        raise

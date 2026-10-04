"""Write evals/corpus_snapshot.json (counts + corpus hash). READ-ONLY queries only."""
import hashlib, json, datetime
from common import connect, SNAPSHOT, CITATIONS_CACHE, sha256_text

TABLES = {
    "legislation_chunks": "citation",
    "caselaw_chunks": "neutral_citation",
}


def main():
    conn = connect(); cur = conn.cursor()
    snap = {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "tables": {}}
    all_lines, cites = [], {}
    for t, col in TABLES.items():
        cur.execute(f"select {col}, md5(text) from {t} order by {col}, md5(text)")
        rows = cur.fetchall()
        lines = [f"{t}|{c}|{h}" for c, h in rows]
        all_lines += lines
        distinct = sorted({c for c, _ in rows if c})
        cites[t] = distinct
        snap["tables"][t] = {
            "chunk_count": len(rows),
            "distinct_citations": len(distinct),
            "hash": sha256_text("\n".join(lines))[:16],
        }
    snap["corpus_hash"] = sha256_text("\n".join(all_lines))[:16]
    snap["hash_method"] = "sha256 over sorted 'table|citation|md5(text)' lines; no text stored"
    SNAPSHOT.write_text(json.dumps(snap, indent=2) + "\n")
    CITATIONS_CACHE.write_text(json.dumps(cites))
    print(json.dumps(snap, indent=2))


if __name__ == "__main__":
    main()

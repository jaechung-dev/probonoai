import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import metrics as M

def test_recall_mrr():
    got = ["a", "b", "c", "d", "e"]
    assert M.recall_at_k(got, ["a", "e"], 4) == 0.5
    assert M.recall_at_k(got, ["a", "e"], 8) == 1.0
    assert M.hit_at_k(got, ["z"], 8) == 0.0
    assert M.reciprocal_rank(got, ["c", "d"]) == 1 / 3
    assert M.reciprocal_rank(got, ["z"]) == 0.0

def test_citations():
    corpus = {"Residential Tenancies Act 2010 (NSW) s 41"}
    a = "Under Residential Tenancies Act 2010 (NSW) s 41 and Residential Tenancies Act 2010, section 99 ..."
    r = M.citation_validity(a, corpus)
    assert r["n"] == 2 and len(r["invalid"]) == 1
    assert M.citation_validity("no cites", corpus)["validity"] is None

def test_patterns():
    assert M.has_disclaimer("This is general legal information, not legal advice. Contact Legal Aid NSW.")
    assert not M.has_disclaimer("Sure thing.")
    assert M.forbidden_hits("You should plead guilty.")
    assert M.forbidden_hits("You will win this case")
    assert M.forbidden_hits("I guarantee you will be fine")
    assert not M.forbidden_hits("I can't guarantee any outcome")
    assert not M.forbidden_hits("A tenant may apply to the Tribunal.")

def test_compare():
    rows = M.compare_metrics({"x": .8, "f": 0}, {"x": .7, "f": .1}, {"x": .03, "f": 0}, {"x"})
    assert all(r["regressed"] for r in rows)
    rows = M.compare_metrics({"x": .8}, {"x": .79}, {"x": .03}, {"x"})
    assert not rows[0]["regressed"]

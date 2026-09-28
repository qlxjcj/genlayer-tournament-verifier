"""Direct-mode tests for Tournament Verifier."""

import json

from conftest import (
    LLM_PATTERN,
    LLM_RESPONSE_VALID,
    LLM_RESPONSE_DISAGREE,
    with_match_data,
)


def test_create_tournament(verifier):
    vm, c = verifier
    participants = json.dumps(["PlayerA", "PlayerB"])
    vm.value = 1000
    tid = c.create_tournament("World Cup", "CS2", participants)
    vm.value = 0
    assert c.get_tournament_count() == 1
    raw = c.get_tournament(tid)
    t = json.loads(raw)
    assert t["name"] == "World Cup"
    assert t["status"] == "OPEN"


def test_submit_match_result(verifier):
    vm, c = verifier
    participants = json.dumps(["PlayerA", "PlayerB"])
    vm.value = 1000
    tid = c.create_tournament("World Cup", "CS2", participants)
    vm.value = 0
    sources = json.dumps([{"url": "https://source-a.com"}, {"url": "https://source-b.com"}])
    rid = c.submit_match_result(tid, "match1", "PlayerA", "PlayerB", sources)
    assert c.get_result_count() == 1
    raw = c.get_result(rid)
    r = json.loads(raw)
    assert r["winner"] == "PlayerA"
    assert r["cross_validation"] == "PASS"


def test_cross_validation_pass(verifier):
    vm, c = verifier
    participants = json.dumps(["PlayerA", "PlayerB"])
    vm.value = 1000
    tid = c.create_tournament("World Cup", "CS2", participants)
    vm.value = 0
    sources = json.dumps([{"url": "https://source-a.com"}, {"url": "https://source-b.com"}])
    rid = c.submit_match_result(tid, "match1", "PlayerA", "PlayerB", sources)
    raw = c.get_result(rid)
    r = json.loads(raw)
    assert r["cross_validation"] == "PASS"
    assert int(r["source_agreement"]) > 50


def test_cross_validation_fail(verifier):
    vm, c = verifier
    participants = json.dumps(["PlayerA", "PlayerB"])
    vm.value = 1000
    tid = c.create_tournament("World Cup", "CS2", participants)
    vm.value = 0
    vm.clear_mocks()
    vm.mock_web(".*source-a.*", {"method": "GET", "status": 200, "body": "PlayerA 3-1 PlayerB"})
    vm.mock_web(".*source-b.*", {"method": "GET", "status": 200, "body": "PlayerB 2-1 PlayerA"})
    vm.mock_llm(LLM_PATTERN, LLM_RESPONSE_DISAGREE)
    sources = json.dumps([{"url": "https://source-a.com"}, {"url": "https://source-b.com"}])
    rid = c.submit_match_result(tid, "match1", "PlayerA", "PlayerB", sources)
    raw = c.get_result(rid)
    r = json.loads(raw)
    assert r["cross_validation"] == "FAIL"


def test_evidence_extraction(verifier):
    vm, c = verifier
    participants = json.dumps(["PlayerA", "PlayerB"])
    vm.value = 1000
    tid = c.create_tournament("World Cup", "CS2", participants)
    vm.value = 0
    sources = json.dumps([{"url": "https://source-a.com"}, {"url": "https://source-b.com"}])
    rid = c.submit_match_result(tid, "match1", "PlayerA", "PlayerB", sources)
    raw = c.get_result(rid)
    r = json.loads(raw)
    assert r["evidence"] != "{}"


def test_finalize_tournament(verifier):
    vm, c = verifier
    participants = json.dumps(["PlayerA", "PlayerB"])
    vm.value = 1000
    tid = c.create_tournament("World Cup", "CS2", participants)
    vm.value = 0
    c.finalize_tournament(tid, "PlayerA")
    raw = c.get_tournament(tid)
    t = json.loads(raw)
    assert t["status"] == "COMPLETED"
    assert t["winner"] == "PlayerA"


def test_requires_two_sources(verifier):
    vm, c = verifier
    participants = json.dumps(["PlayerA", "PlayerB"])
    vm.value = 1000
    tid = c.create_tournament("World Cup", "CS2", participants)
    vm.value = 0
    sources = json.dumps([{"url": "https://source-a.com"}])
    try:
        c.submit_match_result(tid, "match1", "PlayerA", "PlayerB", sources)
        assert False, "Should have raised"
    except Exception:
        pass


def test_requires_two_participants(verifier):
    vm, c = verifier
    participants = json.dumps(["PlayerA"])
    vm.value = 1000
    try:
        c.create_tournament("World Cup", "CS2", participants)
        assert False, "Should have raised"
    except Exception:
        pass
    vm.value = 0


def test_stats(verifier):
    vm, c = verifier
    participants = json.dumps(["PlayerA", "PlayerB"])
    vm.value = 1000
    tid = c.create_tournament("World Cup", "CS2", participants)
    vm.value = 0
    sources = json.dumps([{"url": "https://source-a.com"}, {"url": "https://source-b.com"}])
    c.submit_match_result(tid, "match1", "PlayerA", "PlayerB", sources)
    s = c.get_stats()
    assert s["tournaments"] == 1
    assert s["results"] == 1
    assert s["cross_validated"] == 1

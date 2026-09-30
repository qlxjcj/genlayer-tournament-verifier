"""Direct-mode tests for Tournament Verifier."""

import json

from conftest import (
    LLM_PATTERN,
    LLM_RESPONSE_VALID,
    LLM_RESPONSE_FAIL,
    with_match_data,
)


def _create_tournament(vm, c):
    participants = json.dumps(["PlayerA", "PlayerB"])
    vm.value = 1000
    tid = c.create_tournament("World Cup", "CS2", participants)
    vm.value = 0
    return tid


def test_create_tournament(verifier):
    vm, c = verifier
    tid = _create_tournament(vm, c)
    assert c.get_tournament_count() == 1
    raw = c.get_tournament(tid)
    t = json.loads(raw)
    assert t["name"] == "World Cup"
    assert t["status"] == "OPEN"


def test_submit_match_result(verifier):
    vm, c = verifier
    tid = _create_tournament(vm, c)
    sources = json.dumps([{"url": "https://source-a.com"}, {"url": "https://source-b.com"}])
    rid = c.submit_match_result(tid, "match1", "PlayerA", "PlayerB", sources)
    assert c.get_result_count() == 1
    raw = c.get_result(rid)
    r = json.loads(raw)
    assert r["winner"] == "PlayerA"
    assert r["cross_validation"] == "PASS"


def test_rejects_unregistered_player(verifier):
    vm, c = verifier
    tid = _create_tournament(vm, c)
    sources = json.dumps([{"url": "https://source-a.com"}, {"url": "https://source-b.com"}])
    try:
        c.submit_match_result(tid, "match1", "PlayerX", "PlayerB", sources)
        assert False, "Should have raised"
    except Exception:
        pass


def test_rejects_duplicate_sources(verifier):
    vm, c = verifier
    tid = _create_tournament(vm, c)
    sources = json.dumps([{"url": "https://source-a.com"}, {"url": "https://source-a.com"}])
    try:
        c.submit_match_result(tid, "match1", "PlayerA", "PlayerB", sources)
        assert False, "Should have raised"
    except Exception:
        pass


def test_finalize_with_verified_result(verifier):
    vm, c = verifier
    tid = _create_tournament(vm, c)
    sources = json.dumps([{"url": "https://source-a.com"}, {"url": "https://source-b.com"}])
    rid = c.submit_match_result(tid, "match1", "PlayerA", "PlayerB", sources)
    c.finalize_tournament(tid, rid)
    raw = c.get_tournament(tid)
    t = json.loads(raw)
    assert t["status"] == "COMPLETED"
    assert t["winner"] == "PlayerA"
    assert t["verified_result_id"] == rid


def test_finalize_rejects_failed_validation(verifier):
    vm, c = verifier
    tid = _create_tournament(vm, c)
    vm.clear_mocks()
    vm.mock_web(".*source-a.*", {"method": "GET", "status": 200, "body": "PlayerA 3-1"})
    vm.mock_web(".*source-b.*", {"method": "GET", "status": 200, "body": "PlayerB 2-1"})
    vm.mock_llm(LLM_PATTERN, LLM_RESPONSE_FAIL)
    sources = json.dumps([{"url": "https://source-a.com"}, {"url": "https://source-b.com"}])
    rid = c.submit_match_result(tid, "match1", "PlayerA", "PlayerB", sources)
    try:
        c.finalize_tournament(tid, rid)
        assert False, "Should have raised"
    except Exception:
        pass


def test_cancel_tournament_refund(verifier):
    vm, c = verifier
    tid = _create_tournament(vm, c)
    c.cancel_tournament(tid)
    raw = c.get_tournament(tid)
    t = json.loads(raw)
    assert t["status"] == "CANCELLED"


def test_only_organizer_can_finalize(verifier):
    vm, c = verifier
    tid = _create_tournament(vm, c)
    sources = json.dumps([{"url": "https://source-a.com"}, {"url": "https://source-b.com"}])
    rid = c.submit_match_result(tid, "match1", "PlayerA", "PlayerB", sources)
    try:
        c.finalize_tournament(tid, rid)
        assert False, "Should have raised"
    except Exception:
        pass


def test_requires_two_sources(verifier):
    vm, c = verifier
    tid = _create_tournament(vm, c)
    sources = json.dumps([{"url": "https://source-a.com"}])
    try:
        c.submit_match_result(tid, "match1", "PlayerA", "PlayerB", sources)
        assert False, "Should have raised"
    except Exception:
        pass


def test_stats(verifier):
    vm, c = verifier
    tid = _create_tournament(vm, c)
    sources = json.dumps([{"url": "https://source-a.com"}, {"url": "https://source-b.com"}])
    c.submit_match_result(tid, "match1", "PlayerA", "PlayerB", sources)
    s = c.get_stats()
    assert s["tournaments"] == 1
    assert s["results"] == 1

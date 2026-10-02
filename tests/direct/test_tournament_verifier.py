"""Direct-mode tests for Tournament Verifier.

Covers the steward requirements:
  - participants are real blockchain addresses (not arbitrary strings)
  - the prize payout is a verified transfer to a participant EOA
  - refund / recovery path for funds
  - malformed / empty / provider-error source responses fail closed
  - duplicate sources cannot decide the outcome
  - concurrent same-tournament requests cannot double-spend the prize
"""

import json

import pytest

from conftest import (
    ALICE_ADDR,
    ALICE_NAME,
    BOB_ADDR,
    BOB_NAME,
    LLM_PATTERN,
    LLM_RESPONSE_BAD_ENUM,
    LLM_RESPONSE_CONTRADICT,
    LLM_RESPONSE_UNREGISTERED_WINNER,
    ORGANIZER_ADDR,
    PARTICIPANTS,
    PARTICIPANTS_JSON,
    PRIZE_POOL,
    SOURCE_EMPTY,
    SOURCE_ERROR,
    SOURCE_MALFORMED,
    SOURCES_DUP,
    SOURCES_OK,
    SOURCES_OK_JSON,
    STRANGER_ADDR,
    balance,
    contract_balance,
    make_tournament,
    remock,
    submit_ok,
)


# ---------------------------------------------------------------------------
# Participant address validation
# ---------------------------------------------------------------------------

def test_rejects_arbitrary_string_participants(tournament):
    """The old failure mode: ['PlayerA','PlayerB'] is not a payout path."""
    vm, c = tournament
    vm.sender = ORGANIZER_ADDR
    vm.value = 0
    with pytest.raises(Exception):
        c.create_tournament("Cup", "CS2", ["PlayerA", "PlayerB"])


def test_rejects_participant_without_valid_address(tournament):
    vm, c = tournament
    bad = [
        {"name": "Alice", "address": "0xNOTANADDRESS"},
        {"name": "Bob", "address": BOB_ADDR},
    ]
    with pytest.raises(Exception):
        c.create_tournament("Cup", "CS2", bad)


def test_rejects_participant_with_short_address(tournament):
    vm, c = tournament
    bad = [
        {"name": "Alice", "address": "0x1234"},
        {"name": "Bob", "address": BOB_ADDR},
    ]
    with pytest.raises(Exception):
        c.create_tournament("Cup", "CS2", bad)


def test_rejects_duplicate_payout_address(tournament):
    vm, c = tournament
    dup = [
        {"name": "Alice", "address": ALICE_ADDR},
        {"name": "Bob", "address": ALICE_ADDR},
    ]
    with pytest.raises(Exception):
        c.create_tournament("Cup", "CS2", dup)


def test_rejects_duplicate_participant_name(tournament):
    vm, c = tournament
    dup = [
        {"name": "Alice", "address": ALICE_ADDR},
        {"name": "Alice", "address": BOB_ADDR},
    ]
    with pytest.raises(Exception):
        c.create_tournament("Cup", "CS2", dup)


def test_accepts_json_encoded_participants(tournament):
    """The same payload is accepted as a JSON string (Studio / legacy callers)."""
    vm, c = tournament
    vm.sender = ORGANIZER_ADDR
    vm.value = PRIZE_POOL
    tid = c.create_tournament("World Cup", "CS2", PARTICIPANTS_JSON)
    assert c.get_payout_address(tid, ALICE_NAME) == ALICE_ADDR


def test_creates_tournament_with_real_addresses(tournament):
    vm, c = tournament
    tid = make_tournament(vm, c)
    participants = json.loads(c.get_participants(tid))
    assert len(participants) == 2
    for p in participants:
        assert p["address"].startswith("0x")
        assert len(p["address"]) == 42
    assert c.get_payout_address(tid, ALICE_NAME) == ALICE_ADDR
    assert c.get_payout_address(tid, BOB_NAME) == BOB_ADDR
    assert c.get_payout_address(tid, "Mallory") == ""


# ---------------------------------------------------------------------------
# The core requirement: real prize transfer to a participant EOA
# ---------------------------------------------------------------------------

def test_prize_pool_escrowed_on_create(tournament):
    vm, c = tournament
    set_bal_before = balance(vm, ORGANIZER_ADDR)
    tid = make_tournament(vm, c)
    assert contract_balance(vm) == PRIZE_POOL
    assert balance(vm, ORGANIZER_ADDR) == set_bal_before - PRIZE_POOL


def test_prize_transfers_to_verified_winner_eoa(tournament):
    """Verified payout: the winner's real balance increases by the prize pool."""
    vm, c = tournament
    tid = make_tournament(vm, c)
    rid = submit_ok(vm, c, tid)

    winner_before = balance(vm, ALICE_ADDR)
    loser_before = balance(vm, BOB_ADDR)
    pool_before = contract_balance(vm)

    vm.sender = ORGANIZER_ADDR
    c.finalize_tournament(tid, rid)

    assert balance(vm, ALICE_ADDR) == winner_before + PRIZE_POOL, "winner EOA must receive the prize"
    assert balance(vm, BOB_ADDR) == loser_before, "loser must not be paid"
    assert contract_balance(vm) == pool_before - PRIZE_POOL, "escrow must be emptied"

    t = json.loads(c.get_tournament(tid))
    assert t["status"] == "COMPLETED"
    assert t["winner_name"] == ALICE_NAME
    assert t["winner_address"] == ALICE_ADDR
    assert t["verified_result_id"] == rid


def test_payout_address_comes_from_registry_not_llm(tournament):
    """LLM only supplies a name; the contract maps name -> address."""
    vm, c = tournament
    tid = make_tournament(vm, c)
    rid = submit_ok(vm, c, tid)
    r = json.loads(c.get_result(rid))
    assert r["winner"] == ALICE_NAME
    assert "0x" not in r["winner"], "LLM must never supply a payout address"
    assert c.get_payout_address(tid, r["winner"]) == ALICE_ADDR


# ---------------------------------------------------------------------------
# Refund / recovery path
# ---------------------------------------------------------------------------

def test_refund_returns_prize_to_organizer(tournament):
    vm, c = tournament
    tid = make_tournament(vm, c)
    org_before = balance(vm, ORGANIZER_ADDR)
    pool_before = contract_balance(vm)

    vm.sender = ORGANIZER_ADDR
    c.cancel_tournament(tid)

    assert balance(vm, ORGANIZER_ADDR) == org_before + PRIZE_POOL
    assert contract_balance(vm) == pool_before - PRIZE_POOL
    assert json.loads(c.get_tournament(tid))["status"] == "CANCELLED"


def test_only_organizer_can_cancel(tournament):
    vm, c = tournament
    tid = make_tournament(vm, c)
    vm.sender = STRANGER_ADDR
    with pytest.raises(Exception):
        c.cancel_tournament(tid)
    assert contract_balance(vm) == PRIZE_POOL, "escrow must be untouched after a rejected cancel"


def test_only_organizer_can_finalize(tournament):
    vm, c = tournament
    tid = make_tournament(vm, c)
    rid = submit_ok(vm, c, tid)
    vm.sender = STRANGER_ADDR
    with pytest.raises(Exception):
        c.finalize_tournament(tid, rid)
    assert contract_balance(vm) == PRIZE_POOL


# ---------------------------------------------------------------------------
# Participant / ownership validation
# ---------------------------------------------------------------------------

def test_rejects_unregistered_player(tournament):
    vm, c = tournament
    tid = make_tournament(vm, c)
    with pytest.raises(Exception):
        c.submit_match_result(tid, "final", "Mallory", BOB_NAME, SOURCES_OK)


def test_rejects_identical_players(tournament):
    vm, c = tournament
    tid = make_tournament(vm, c)
    with pytest.raises(Exception):
        c.submit_match_result(tid, "final", ALICE_NAME, ALICE_NAME, SOURCES_OK)


def test_llm_cannot_pick_unregistered_winner(tournament):
    """Consensus result with a non-participant winner is rejected deterministically."""
    vm, c = tournament
    remock(vm, llm=LLM_RESPONSE_UNREGISTERED_WINNER)
    tid = make_tournament(vm, c)
    with pytest.raises(Exception):
        c.submit_match_result(tid, "final", ALICE_NAME, BOB_NAME, SOURCES_OK)
    assert c.get_result_count() == 0
    assert contract_balance(vm) == PRIZE_POOL


def test_rejects_bounded_enum_violation(tournament):
    vm, c = tournament
    remock(vm, llm=LLM_RESPONSE_BAD_ENUM)
    tid = make_tournament(vm, c)
    with pytest.raises(Exception):
        c.submit_match_result(tid, "final", ALICE_NAME, BOB_NAME, SOURCES_OK)


# ---------------------------------------------------------------------------
# Fail-closed source handling: malformed / empty / provider-error
# ---------------------------------------------------------------------------

def test_empty_source_body_fails_closed(tournament):
    vm, c = tournament
    remock(vm, source_a=SOURCE_EMPTY, llm=LLM_RESPONSE_CONTRADICT)
    tid = make_tournament(vm, c)
    rid = c.submit_match_result(tid, "final", ALICE_NAME, BOB_NAME, SOURCES_OK)
    r = json.loads(c.get_result(rid))
    assert r["cross_validation"] == "FAIL"
    assert int(r["sources_checked"]) == 2
    assert int(r["sources_agreed"]) == 1, "the empty body must not count as retrieved"


def test_unrelated_source_body_fails_schema_validation(tournament):
    """A 200 response that is not about this match is not 'retrieved'."""
    vm, c = tournament
    remock(vm, source_a=SOURCE_MALFORMED, llm=LLM_RESPONSE_CONTRADICT)
    tid = make_tournament(vm, c)
    rid = c.submit_match_result(tid, "final", ALICE_NAME, BOB_NAME, SOURCES_OK)
    r = json.loads(c.get_result(rid))
    assert int(r["sources_agreed"]) == 1, "an unrelated 200 page must not count as retrieved"
    assert r["cross_validation"] == "FAIL"


def test_provider_error_response_fails_closed(tournament):
    vm, c = tournament
    remock(vm, source_a=SOURCE_ERROR, llm=LLM_RESPONSE_CONTRADICT)
    tid = make_tournament(vm, c)
    rid = c.submit_match_result(tid, "final", ALICE_NAME, BOB_NAME, SOURCES_OK)
    r = json.loads(c.get_result(rid))
    assert int(r["sources_agreed"]) == 1, "an HTTP 500 body must not count as retrieved"
    assert r["cross_validation"] == "FAIL"


def test_all_sources_failing_cannot_decide_outcome(tournament):
    vm, c = tournament
    remock(vm, source_a=SOURCE_ERROR, source_b=SOURCE_ERROR, llm=LLM_RESPONSE_CONTRADICT)
    tid = make_tournament(vm, c)
    rid = c.submit_match_result(tid, "final", ALICE_NAME, BOB_NAME, SOURCES_OK)
    r = json.loads(c.get_result(rid))
    assert r["cross_validation"] == "FAIL"

    # And a FAIL result must never pay out.
    vm.sender = ORGANIZER_ADDR
    with pytest.raises(Exception):
        c.finalize_tournament(tid, rid)
    assert contract_balance(vm) == PRIZE_POOL, "no payout on failed verification"


def test_failed_cross_validation_blocks_settlement(tournament):
    vm, c = tournament
    remock(vm, llm=LLM_RESPONSE_CONTRADICT)
    tid = make_tournament(vm, c)
    rid = c.submit_match_result(tid, "final", ALICE_NAME, BOB_NAME, SOURCES_OK)
    assert json.loads(c.get_result(rid))["cross_validation"] == "FAIL"
    vm.sender = ORGANIZER_ADDR
    with pytest.raises(Exception):
        c.finalize_tournament(tid, rid)
    assert contract_balance(vm) == PRIZE_POOL


# ---------------------------------------------------------------------------
# Duplicate sources cannot decide the outcome
# ---------------------------------------------------------------------------

def test_rejects_duplicate_sources(tournament):
    vm, c = tournament
    tid = make_tournament(vm, c)
    with pytest.raises(Exception):
        c.submit_match_result(tid, "final", ALICE_NAME, BOB_NAME, SOURCES_DUP)


def test_requires_at_least_two_sources(tournament):
    vm, c = tournament
    tid = make_tournament(vm, c)
    one = [{"url": "https://esports.example.com/final"}]
    with pytest.raises(Exception):
        c.submit_match_result(tid, "final", ALICE_NAME, BOB_NAME, one)


def test_rejects_invalid_source_url(tournament):
    vm, c = tournament
    tid = make_tournament(vm, c)
    bad = [{"url": "ftp://x"}, {"url": "https://y.example.com"}]
    with pytest.raises(Exception):
        c.submit_match_result(tid, "final", ALICE_NAME, BOB_NAME, bad)


def test_accepts_json_encoded_sources(tournament):
    vm, c = tournament
    tid = make_tournament(vm, c)
    rid = c.submit_match_result(tid, "final", ALICE_NAME, BOB_NAME, SOURCES_OK_JSON)
    assert json.loads(c.get_result(rid))["result_id"] == rid


# ---------------------------------------------------------------------------
# Result binding + concurrent same-route requests
# ---------------------------------------------------------------------------

def test_result_must_belong_to_tournament(tournament):
    vm, c = tournament
    tid_a = make_tournament(vm, c)
    tid_b = make_tournament(vm, c)
    rid = submit_ok(vm, c, tid_a)
    vm.sender = ORGANIZER_ADDR
    with pytest.raises(Exception):
        c.finalize_tournament(tid_b, rid)
    assert contract_balance(vm) == 2 * PRIZE_POOL


def test_concurrent_same_tournament_cannot_double_spend(tournament):
    """Two verified results for one tournament must not pay out twice."""
    vm, c = tournament
    tid = make_tournament(vm, c)
    rid_1 = submit_ok(vm, c, tid)
    rid_2 = submit_ok(vm, c, tid)

    vm.sender = ORGANIZER_ADDR
    c.finalize_tournament(tid, rid_1)

    winner_after_first = balance(vm, ALICE_ADDR)
    with pytest.raises(Exception):
        c.finalize_tournament(tid, rid_2)

    assert balance(vm, ALICE_ADDR) == winner_after_first, "second finalize must not move funds"
    assert contract_balance(vm) == 0


def test_cancel_after_finalize_is_rejected(tournament):
    vm, c = tournament
    tid = make_tournament(vm, c)
    rid = submit_ok(vm, c, tid)
    vm.sender = ORGANIZER_ADDR
    c.finalize_tournament(tid, rid)
    with pytest.raises(Exception):
        c.cancel_tournament(tid)


def test_result_id_is_unique_and_retrievable(tournament):
    vm, c = tournament
    tid = make_tournament(vm, c)
    rid_1 = submit_ok(vm, c, tid)
    rid_2 = submit_ok(vm, c, tid)
    assert rid_1 != rid_2
    assert json.loads(c.get_result(rid_1))["result_id"] == rid_1
    assert json.loads(c.get_result(rid_2))["result_id"] == rid_2
    assert c.get_result_count() == 2


def test_fully_funded_two_tournaments_pay_independently(tournament):
    vm, c = tournament
    tid_1 = make_tournament(vm, c)
    tid_2 = make_tournament(vm, c)
    assert contract_balance(vm) == 2 * PRIZE_POOL

    rid_1 = submit_ok(vm, c, tid_1)
    rid_2 = submit_ok(vm, c, tid_2)

    alice_before = balance(vm, ALICE_ADDR)
    vm.sender = ORGANIZER_ADDR
    c.finalize_tournament(tid_1, rid_1)
    c.finalize_tournament(tid_2, rid_2)

    assert balance(vm, ALICE_ADDR) == alice_before + 2 * PRIZE_POOL
    assert contract_balance(vm) == 0

"""Shared fixtures for Tournament Verifier direct-mode tests.

Direct mode runs the real contract source in-process but does NOT track
native value flows. Two accounting hooks are installed so tests can
genuinely assert fund movement (a real participant EOA payout):

1. Payable value: ``create_tournament`` with ``vm.value`` set moves value
   sender -> contract in the VM balance ledger (mirrors a payable call).
2. PostMessage transfers: the contract's ``emit_transfer`` produces a
   PostMessage gl_call (contract -> recipient). We intercept it and move
   contract -> recipient so the prize payout / refund assertions are real.

Without these hooks the balances never change and the payout assertions
would be meaningless tautologies.
"""

import json
import os
import pytest

CONTRACT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "tournament_verifier.py",
)

# --- Real blockchain payout addresses (EOA-shaped, 0x + 40 hex) ---
ORGANIZER_ADDR = "0x" + "aa" * 20
ALICE_ADDR = "0x" + "11" * 20
BOB_ADDR = "0x" + "22" * 20
STRANGER_ADDR = "0x" + "33" * 20

ALICE_NAME = "Alice"
BOB_NAME = "Bob"

PRIZE_POOL = 10**18

# Typed structured args (what the frontend and CLI actually send).
PARTICIPANTS = [
    {"name": ALICE_NAME, "address": ALICE_ADDR},
    {"name": BOB_NAME, "address": BOB_ADDR},
]
# The contract also accepts JSON-encoded strings for the same payload.
PARTICIPANTS_JSON = json.dumps(PARTICIPANTS)

LLM_PATTERN = r".*tournament result verifier.*|.*score_a.*"

LLM_RESPONSE_VALID = json.dumps({
    "score_a": "3",
    "score_b": "1",
    "winner": ALICE_NAME,
    "cross_validation": "PASS",
    "source_agreement": "95",
    "evidence": {"https://esports.example.com/final": "Alice 3-1 Bob"},
    "reasoning": "Both sources report the same score.",
})

LLM_RESPONSE_UNREGISTERED_WINNER = json.dumps({
    "score_a": "3",
    "score_b": "1",
    "winner": "Mallory",
    "cross_validation": "PASS",
    "source_agreement": "95",
    "evidence": {},
    "reasoning": "Winner is not a registered participant.",
})

LLM_RESPONSE_BAD_ENUM = json.dumps({
    "score_a": "3",
    "score_b": "1",
    "winner": ALICE_NAME,
    "cross_validation": "MAYBE",
    "source_agreement": "95",
    "evidence": {},
    "reasoning": "Bogus enum.",
})

LLM_RESPONSE_CONTRADICT = json.dumps({
    "score_a": "3",
    "score_b": "1",
    "winner": ALICE_NAME,
    "cross_validation": "FAIL",
    "source_agreement": "30",
    "evidence": {},
    "reasoning": "Sources contradict each other.",
})

# Source bodies must mention a registered player name to pass schema validation.
SOURCE_A = {"method": "GET", "status": 200, "body": "Match result: Alice 3-1 Bob"}
SOURCE_B = {"method": "GET", "status": 200, "body": "Final score: Alice wins 3-1"}
SOURCE_EMPTY = {"method": "GET", "status": 200, "body": ""}
SOURCE_MALFORMED = {"method": "GET", "status": 200, "body": "<html>unrelated page</html>"}
SOURCE_ERROR = {"method": "GET", "status": 500, "body": "Internal Server Error"}
SOURCE_BAD_JSON = {"method": "GET", "status": 200, "body": "{not json"}

SOURCES_OK = [
    {"url": "https://esports.example.com/final"},
    {"url": "https://scores.example.com/final"},
]
SOURCES_OK_JSON = json.dumps(SOURCES_OK)

SOURCES_DUP = [
    {"url": "https://esports.example.com/final"},
    {"url": "https://esports.example.com/final"},
]


def balance(vm, addr):
    return vm._balances.get(vm._to_bytes(addr), 0)


def set_balance(vm, addr, amount):
    vm._balances[vm._to_bytes(addr)] = amount


def contract_balance(vm):
    return vm._balances.get(vm._contract_address, 0)


@pytest.fixture
def tournament(direct_vm, direct_deploy):
    vm = direct_vm

    # Intercept value transfers emitted by the contract's `emit_transfer`.
    def _value_transfer_hook(vm, request):
        if "PostMessage" not in request:
            return None
        msg = request["PostMessage"]
        amount = int(msg.get("value", 0))
        if amount > 0:
            contract = vm._contract_address
            recipient = vm._to_bytes(msg["address"])
            vm._balances[contract] = vm._balances.get(contract, 0) - amount
            vm._balances[recipient] = vm._balances.get(recipient, 0) + amount
        return {"ok": None}

    vm._gl_call_hook = _value_transfer_hook

    vm.mock_web(".*esports.example.com.*", SOURCE_A)
    vm.mock_web(".*scores.example.com.*", SOURCE_B)
    vm.mock_llm(LLM_PATTERN, LLM_RESPONSE_VALID)

    c = direct_deploy(CONTRACT)

    # Payable value accounting: debit sender, credit contract.
    _orig_create = c.create_tournament

    def _create_with_value(name, game, participants_json):
        if vm.value > 0:
            sender = vm._to_bytes(vm.sender)
            contract = vm._contract_address
            vm._balances[sender] = vm._balances.get(sender, 0) - vm.value
            vm._balances[contract] = vm._balances.get(contract, 0) + vm.value
        return _orig_create(name, game, participants_json)

    c.create_tournament = _create_with_value

    vm.sender = ORGANIZER_ADDR
    vm.deal(ORGANIZER_ADDR, 10 * PRIZE_POOL)
    vm.deal(STRANGER_ADDR, 10 * PRIZE_POOL)
    return vm, c


def make_tournament(vm, c, prize=PRIZE_POOL):
    """Create a funded tournament with two real EOA participants."""
    vm.sender = ORGANIZER_ADDR
    vm.value = prize
    tid = c.create_tournament("World Cup", "CS2", PARTICIPANTS)
    vm.value = 0
    return tid


def submit_ok(vm, c, tid):
    return c.submit_match_result(tid, "final", ALICE_NAME, BOB_NAME, SOURCES_OK)


def remock(vm, source_a=SOURCE_A, source_b=SOURCE_B, llm=None):
    """Mocks are first-match-wins, so clear before re-registering overrides."""
    if llm is None:
        llm = LLM_RESPONSE_VALID
    vm.clear_mocks()
    vm.mock_web(".*esports.example.com.*", source_a)
    vm.mock_web(".*scores.example.com.*", source_b)
    vm.mock_llm(LLM_PATTERN, llm)

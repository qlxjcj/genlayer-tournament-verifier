"""Shared fixtures and mocks for Tournament Verifier tests."""

import json
import os
import pytest

CONTRACT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "tournament_verifier.py",
)

LLM_PATTERN = r".*tournament.*verifier.*|.*score_a.*|.*winner.*"

SOURCE_A = {"method": "GET", "status": 200, "body": "Match result: PlayerA 3-1 PlayerB"}
SOURCE_B = {"method": "GET", "status": 200, "body": "Final score: PlayerA wins 3-1"}

LLM_RESPONSE_VALID = json.dumps({
    "score_a": "3",
    "score_b": "1",
    "winner": "PlayerA",
    "cross_validation": "PASS",
    "source_agreement": "95",
    "evidence": {"https://source-a.com": "PlayerA 3-1 PlayerB"},
    "reasoning": "Both sources agree on the score."
})

LLM_RESPONSE_DISAGREE = json.dumps({
    "score_a": "3",
    "score_b": "1",
    "winner": "UNKNOWN",
    "cross_validation": "FAIL",
    "source_agreement": "30",
    "evidence": {"https://source-a.com": "PlayerA 3-1 PlayerB", "https://source-b.com": "PlayerB 2-1 PlayerA"},
    "reasoning": "Sources contradict each other."
})


def with_match_data(vm):
    vm.mock_web(".*source-a.*", SOURCE_A)
    vm.mock_web(".*source-b.*", SOURCE_B)
    vm.mock_llm(LLM_PATTERN, LLM_RESPONSE_VALID)


@pytest.fixture
def verifier(direct_vm, direct_deploy):
    vm = direct_vm
    c = direct_deploy(CONTRACT)
    with_match_data(vm)
    return vm, c

# Tournament Result Verifier & Settlement

A GenLayer intelligent contract that verifies esports match results from independent
sources using AI consensus, then settles the prize pool to the winner's **registered
blockchain address**.

Bradbury testnet contract: `0x3abde80377C82C89F302C67F3C21035dCa376CD9`
Live frontend: https://qlxjcj.github.io/genlayer-tournament-verifier/

---

## Design: how a payout becomes valid

The previous iteration paid `Address(winner)` where `winner` was whatever string the
LLM produced — including values like `PlayerA`, which are not addresses at all. This
version separates the two concerns explicitly:

| Concern | Decided by | Value |
|---|---|---|
| **Who won** (display name) | AI consensus over sources | `Alice` |
| **Where the money goes** (address) | Deterministic contract registry | `0xAbc...` |

```
LLM output            contract lookup                transfer
"winner": "Alice"  ->  name_to_addr["Alice"]  ->     _send(Address(0xAbc...), prize)
```

The LLM never supplies and never can supply a payout address. The registry is frozen
at tournament creation and only the organizer can create it.

### Participant registration

`create_tournament` takes `[{ "name": "Alice", "address": "0x..." }, ...]` and rejects:

- arbitrary strings (`["PlayerA","PlayerB"]`) — no payout path exists
- malformed addresses (must be `0x` + 40 hex characters)
- duplicate names
- duplicate payout addresses (prevents one wallet from claiming two seats)

### Deterministic consensus validation

`_validate_result()` rejects a consensus result unless **all** of the following hold:

- `winner` is exactly `player_a` or `player_b` (never an arbitrary third value)
- `score_a` / `score_b` parse as non-negative numbers
- `cross_validation` ∈ `{PASS, FAIL, PARTIAL}`
- `source_agreement` ∈ `[0, 100]`

An invalid result raises `UserError` — it is never written to storage.

### Fail-closed source handling

Each source response is schema-checked before it counts as retrieved. A response is
discarded when it is empty, is not HTTP-relevant to the match (does not mention either
player), or the provider returns an error status. Discarded sources are recorded in
`sources_checked` vs `sources_agreed` but never contribute to the verdict.

If no source survives, the result is recorded as `cross_validation: FAIL` and settlement
is blocked.

### Settlement safety

`finalize_tournament(tournament_id, result_id)`:

- only the organizer can call it
- the result must belong to the given tournament
- the result's `player_a` / `player_b` must both be registered participants
- the winner must be one of those two players
- `cross_validation: FAIL` results are refused
- the payout address is read from the registry and re-validated before transfer
- the tournament is marked `COMPLETED`, so a second result cannot double-spend

`cancel_tournament(tournament_id)` refunds the escrowed prize pool to the organizer —
the recovery path for funds when no valid result is ever produced.

---

## Testing

```
python -m pytest tests/direct -q
```

31 direct-mode tests. Direct mode does not track native value flow on its own, so
`tests/direct/conftest.py` installs two accounting hooks (mirroring what the chain does):

1. a payable hook that moves `vm.value` sender → contract on `create_tournament`
2. a `PostMessage` gl_call hook that moves contract → recipient on `emit_transfer`

Without them the payout assertions would be tautologies. With them, the tests assert
real balance deltas on participant EOAs.

Coverage includes:

- real prize transfer to a verified winner EOA (balance delta asserted)
- refund to organizer on cancel
- arbitrary-string participants rejected
- malformed / short / duplicate addresses rejected
- unregistered players and unregistered winners rejected
- empty, unrelated and HTTP-500 source responses fail closed
- duplicate sources rejected, two sources required
- failed cross-validation blocks settlement
- result must belong to the tournament being settled
- concurrent results for one tournament cannot double-spend the prize
- cancel after finalize rejected

## Frontend

`index.html` (genlayer-js, Bradbury chain) supports wallet connect, tournament creation
with per-participant payout addresses, match submission with two sources, settlement and
refund, and on-chain record inspection.

Every write returns a record ID and the UI then **re-reads that exact record and verifies
its fields match what was submitted** before rendering it. It never reads a shared
`get_latest` pointer. DOM APIs only (no `innerHTML` for dynamic content), with loading
states and address-format validation.

## Live on-chain verification

Run against the deployed contract on Bradbury. Each case is the exact failure mode
raised in review, exercised against the real chain rather than mocks:

| # | Call | Input | Result |
|---|---|---|---|
| 1 | `create_tournament` | `["PlayerA","PlayerB"]` | `FINISHED_WITH_ERROR` — arbitrary strings rejected |
| 2 | `create_tournament` | address `0xNOTANADDR` | `FINISHED_WITH_ERROR` — malformed address rejected |
| 3 | `create_tournament` | two participants, same address | `FINISHED_WITH_ERROR` — duplicate payout rejected |
| 4 | `create_tournament` | two real `0x…` addresses | `FINISHED_WITH_RETURN` — accepted |
| 5 | `get_payout_address(1,"Alice")` | — | `0x1111…1111`, `0x2222…2222` for Bob, `''` for anyone else |
| 6 | `submit_match_result` | player `Mallory` (unregistered) | `FINISHED_WITH_ERROR` |
| 7 | `submit_match_result` | two identical source URLs | `FINISHED_WITH_ERROR` |
| 8 | `submit_match_result` | two real, reachable URLs that do not cover the match | `sources_checked: 2, sources_agreed: 0, cross_validation: FAIL` — fail-closed on real HTTP |
| 9 | `finalize_tournament` | the `FAIL` result | `FINISHED_WITH_ERROR` — failed verification cannot settle |
| 10 | `cancel_tournament` | — | `FINISHED_WITH_RETURN`, status `CANCELLED` — refund path open |

Case 8 is the important one: the sources were genuinely fetched over the network and
genuinely said nothing about the match, so both were discarded rather than counted as
retrieved.

Funded settlement (a non-zero prize moving to a participant EOA) is exercised in the
test suite with asserted balance deltas, and can be run live from the frontend by
connecting a wallet and funding a tournament — `genlayer write` cannot attach
`msg.value`, so the CLI cannot fund the escrow itself.

## Contract surface

| Method | Kind | Purpose |
|---|---|---|
| `create_tournament(name, game, participants)` | write, payable | `participants` is `[{name, address}, …]`; escrows the prize |
| `submit_match_result(tournament_id, match_id, player_a, player_b, sources)` | write | `sources` is `[{url}, …]`; consensus-verifies the match |
| `finalize_tournament(tournament_id, result_id)` | write | settle prize to the winner's registry address |
| `cancel_tournament(tournament_id)` | write | refund the organizer |
| `get_tournament(id)` / `get_result(id)` | view | bound record lookup by ID |
| `get_participants(id)` / `get_payout_address(id, name)` | view | transparent payout registry |
| `get_tournament_count()` / `get_result_count()` / `get_stats()` | view | counters |

Both `participants` and `sources` are typed structured arguments (what `genlayer-js`
and the Studio send). A JSON-encoded string of the same payload is also accepted.

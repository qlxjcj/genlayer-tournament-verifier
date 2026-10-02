# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
import json
from dataclasses import dataclass
from genlayer import *


@allow_storage
@dataclass
class Tournament:
    tournament_id: str
    name: str
    game: str
    organizer: str
    prize_pool: str
    participants: str
    name_to_addr: str
    status: str
    winner_name: str
    winner_address: str
    verified_result_id: str
    created_at: str


@allow_storage
@dataclass
class MatchResult:
    result_id: str
    tournament_id: str
    match_id: str
    player_a: str
    player_b: str
    score_a: str
    score_b: str
    winner: str
    cross_validation: str
    source_agreement: str
    evidence: str
    reasoning: str
    sources_checked: str
    sources_agreed: str
    verified_at: str


class TournamentVerifier(gl.Contract):
    tournaments: TreeMap[str, str]
    results: TreeMap[str, str]
    tournament_count: u256
    result_count: u256

    SUPPORTED_CROSS_VALIDATION = ("PASS", "FAIL", "PARTIAL")
    MAX_NAME_LEN = 64

    def __init__(self):
        self.tournament_count = 0
        self.result_count = 0

    def _decode_body(self, content) -> str:
        body = getattr(content, "body", None)
        if body is None:
            return str(content)
        if isinstance(body, bytes):
            return body.decode("utf-8", errors="replace")
        return str(body)

    def _send(self, recipient: Address, amount: u256):
        gl.get_contract_at(recipient).emit_transfer(value=amount)

    def _addr_to_hex(self, a) -> str:
        if isinstance(a, str):
            return a
        h = getattr(a, "as_hex", None)
        if isinstance(h, str):
            return h
        return str(a)

    def _sender_hex(self) -> str:
        return self._addr_to_hex(gl.message.sender_address)

    def _is_valid_address(self, addr) -> bool:
        if not isinstance(addr, str):
            return False
        if not addr.startswith("0x") or len(addr) != 42:
            return False
        hex_part = addr[2:]
        try:
            int(hex_part, 16)
        except ValueError:
            return False
        return True

    def _parse_participants(self, participants_json: str) -> tuple:
        try:
            participants = json.loads(participants_json)
        except (json.JSONDecodeError, TypeError):
            raise gl.vm.UserError("Invalid participants JSON")
        if not isinstance(participants, list) or len(participants) < 2:
            raise gl.vm.UserError("At least 2 participants required")

        name_to_addr = {}
        seen_addrs = {}
        cleaned = []
        for p in participants:
            if not isinstance(p, dict):
                raise gl.vm.UserError("Each participant must be an object with name and address")
            name = p.get("name", "")
            addr = p.get("address", "")
            if not isinstance(name, str) or not name.strip():
                raise gl.vm.UserError("Each participant needs a non-empty name")
            name = name.strip()
            if len(name) > self.MAX_NAME_LEN:
                raise gl.vm.UserError("Participant name too long")
            if name in name_to_addr:
                raise gl.vm.UserError("Duplicate participant name: " + name)
            if not self._is_valid_address(addr):
                raise gl.vm.UserError("Invalid payout address for " + name + ": must be 0x + 40 hex chars")
            addr_key = addr.lower()
            if addr_key in seen_addrs:
                raise gl.vm.UserError("Duplicate payout address: " + addr)
            seen_addrs[addr_key] = name
            name_to_addr[name] = addr
            cleaned.append({"name": name, "address": addr})
        return cleaned, name_to_addr

    def _validate_source_response(self, body: str, player_a: str, player_b: str) -> bool:
        if not body or not body.strip():
            return False
        low = body.lower()
        if player_a.lower() not in low and player_b.lower() not in low:
            return False
        return True

    def _validate_result(self, result: dict, player_a: str, player_b: str) -> bool:
        winner = result.get("winner", "")
        if winner != player_a and winner != player_b:
            return False
        cross_val = result.get("cross_validation", "")
        if cross_val not in self.SUPPORTED_CROSS_VALIDATION:
            return False
        try:
            score_a = float(result.get("score_a", "x"))
            score_b = float(result.get("score_b", "x"))
        except (ValueError, TypeError):
            return False
        if score_a < 0 or score_b < 0:
            return False
        try:
            agreement = int(result.get("source_agreement", "x"))
        except (ValueError, TypeError):
            return False
        if agreement < 0 or agreement > 100:
            return False
        return True

    @gl.public.write.payable
    def create_tournament(self, name: str, game: str, participants_json: str) -> str:
        if not name or not name.strip():
            raise gl.vm.UserError("Tournament name is required")
        if not game or not game.strip():
            raise gl.vm.UserError("Game is required")

        participants, name_to_addr = self._parse_participants(participants_json)

        organizer_hex = self._sender_hex()
        prize_pool = gl.message.value

        from datetime import datetime, timezone
        self.tournament_count += 1
        tournament_id = str(self.tournament_count)

        tournament = Tournament(
            tournament_id=tournament_id,
            name=name.strip(),
            game=game.strip(),
            organizer=organizer_hex,
            prize_pool=str(prize_pool),
            participants=json.dumps(participants),
            name_to_addr=json.dumps(name_to_addr),
            status="OPEN",
            winner_name="",
            winner_address="",
            verified_result_id="",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.tournaments[tournament_id] = json.dumps(tournament.__dict__)
        return tournament_id

    def _verify_match(self, tournament_id: str, match_id: str, player_a: str, player_b: str, sources: list) -> dict:
        def gather_and_verify() -> dict:
            fetched = []
            for source in sources:
                try:
                    content = gl.nondet.web.render(source["url"])
                    body = self._decode_body(content)[:1500]
                    if not self._validate_source_response(body, player_a, player_b):
                        fetched.append({"url": source["url"], "data": body, "retrieved": False})
                    else:
                        fetched.append({"url": source["url"], "data": body, "retrieved": True})
                except Exception:
                    fetched.append({"url": source["url"], "data": "", "retrieved": False})

            retrieved = [f for f in fetched if f["retrieved"]]
            if not retrieved:
                return {
                    "score_a": "0",
                    "score_b": "0",
                    "winner": player_a,
                    "cross_validation": "FAIL",
                    "source_agreement": "0",
                    "evidence": "{}",
                    "reasoning": "No source passed schema validation.",
                    "sources_checked": len(sources),
                    "sources_agreed": 0,
                }

            parts = []
            for i, r in enumerate(retrieved):
                parts.append("[Source " + str(i+1) + "] " + r["url"] + ":\n" + r["data"][:500])
            sources_text = "\n".join(parts)

            json_format = chr(123) + chr(34) + "score_a" + chr(34) + ": " + chr(34) + "<number>" + chr(34) + ", " + chr(34) + "score_b" + chr(34) + ": " + chr(34) + "<number>" + chr(34) + ", " + chr(34) + "winner" + chr(34) + ": " + chr(34) + "<player_a or player_b exactly>" + chr(34) + ", " + chr(34) + "cross_validation" + chr(34) + ": " + chr(34) + "PASS" + chr(34) + "|" + chr(34) + "FAIL" + chr(34) + "|" + chr(34) + "PARTIAL" + chr(34) + ", " + chr(34) + "source_agreement" + chr(34) + ": " + chr(34) + "<0-100>" + chr(34) + ", " + chr(34) + "evidence" + chr(34) + ": " + chr(123) + chr(34) + "<source>" + chr(34) + ": " + chr(34) + "<quote>" + chr(34) + chr(125) + ", " + chr(34) + "reasoning" + chr(34) + ": " + chr(34) + "<text>" + chr(34) + chr(125)

            task = (
                "You are a tournament result verifier. Verify the match result using the provided sources.\n"
                "MATCH: " + match_id + "\n"
                "PLAYER A (name only): " + player_a + "\n"
                "PLAYER B (name only): " + player_b + "\n"
                "SOURCES (" + str(len(retrieved)) + " of " + str(len(sources)) + " passed schema validation):\n" + sources_text + "\n\n"
                "INSTRUCTIONS:\n"
                "1. Extract score_a and score_b from sources (numbers only)\n"
                "2. winner MUST be exactly player A name or player B name, never any other value\n"
                "3. cross_validation: Compare scores across sources - PASS if agree, FAIL if contradict, PARTIAL if only some agree\n"
                "4. source_agreement: percentage of sources that agree (0-100)\n"
                "5. evidence: Extract specific quotes from sources\n\n"
                "Respond ONLY in JSON: " + json_format
            )
            result = gl.nondet.exec_prompt(task)
            if isinstance(result, str):
                result = json.loads(result.replace("```json", "").replace("```", ""))
            if not isinstance(result, dict):
                raise gl.vm.UserError("[LLM_ERROR] LLM returned non-dict result")
            result["sources_checked"] = len(sources)
            result["sources_agreed"] = len(retrieved)
            return result

        principle = (
            "Two results are equivalent if score_a matches exactly, score_b matches exactly, "
            "winner matches exactly, cross_validation matches exactly, "
            "source_agreement differs by at most 10 points, "
            "evidence quotes may differ slightly, "
            "sources_checked and sources_agreed match exactly. "
            "reasoning wording may differ."
        )
        return gl.eq_principle.prompt_comparative(gather_and_verify, principle)

    @gl.public.write
    def submit_match_result(self, tournament_id: str, match_id: str, player_a: str, player_b: str, sources_json: str) -> str:
        tournament_id = str(tournament_id)
        tournament = json.loads(self.tournaments.get(tournament_id, "{}"))
        if not tournament:
            raise gl.vm.UserError("Tournament not found")
        if tournament["status"] != "OPEN":
            raise gl.vm.UserError("Tournament not open")

        if not match_id or not match_id.strip():
            raise gl.vm.UserError("Match id is required")
        if not player_a or not player_a.strip() or not player_b or not player_b.strip():
            raise gl.vm.UserError("Both players are required")
        player_a = player_a.strip()
        player_b = player_b.strip()
        if player_a == player_b:
            raise gl.vm.UserError("Players must be different")

        name_to_addr = json.loads(tournament["name_to_addr"])
        if player_a not in name_to_addr:
            raise gl.vm.UserError("Player A is not a registered participant")
        if player_b not in name_to_addr:
            raise gl.vm.UserError("Player B is not a registered participant")

        try:
            sources = json.loads(sources_json)
        except (json.JSONDecodeError, TypeError):
            raise gl.vm.UserError("Invalid sources JSON")

        if not isinstance(sources, list) or len(sources) < 2:
            raise gl.vm.UserError("At least 2 sources required for cross-validation")

        urls = []
        for s in sources:
            if not isinstance(s, dict) or "url" not in s:
                raise gl.vm.UserError("Each source must have a url field")
            url = s["url"]
            if not isinstance(url, str) or (not url.startswith("http://") and not url.startswith("https://")):
                raise gl.vm.UserError("Invalid URL: " + str(url))
            urls.append(url)
        if len(set(urls)) != len(urls):
            raise gl.vm.UserError("Duplicate sources not allowed")

        result = self._verify_match(tournament_id, match_id.strip(), player_a, player_b, sources)

        if not self._validate_result(result, player_a, player_b):
            raise gl.vm.UserError("Invalid consensus result: winner must be a registered player, scores numeric, cross_validation in enum, agreement 0-100")

        from datetime import datetime, timezone
        self.result_count += 1
        result_id = str(self.result_count)

        match_result = MatchResult(
            result_id=result_id,
            tournament_id=tournament_id,
            match_id=match_id.strip(),
            player_a=player_a,
            player_b=player_b,
            score_a=str(result.get("score_a", "0")),
            score_b=str(result.get("score_b", "0")),
            winner=str(result.get("winner", "")),
            cross_validation=str(result.get("cross_validation", "FAIL")),
            source_agreement=str(result.get("source_agreement", "0")),
            evidence=json.dumps(result.get("evidence", {})),
            reasoning=str(result.get("reasoning", "")),
            sources_checked=str(result.get("sources_checked", 0)),
            sources_agreed=str(result.get("sources_agreed", 0)),
            verified_at=datetime.now(timezone.utc).isoformat(),
        )
        self.results[result_id] = json.dumps(match_result.__dict__)
        return result_id

    @gl.public.write
    def finalize_tournament(self, tournament_id: str, result_id: str):
        tournament_id = str(tournament_id)
        tournament = json.loads(self.tournaments.get(tournament_id, "{}"))
        if not tournament:
            raise gl.vm.UserError("Tournament not found")
        if tournament["status"] != "OPEN":
            raise gl.vm.UserError("Tournament not open")

        sender = self._sender_hex()
        if sender.lower() != tournament["organizer"].lower():
            raise gl.vm.UserError("Only organizer can finalize")

        result = json.loads(self.results.get(str(result_id), "{}"))
        if not result:
            raise gl.vm.UserError("Result not found")
        if result["tournament_id"] != tournament_id:
            raise gl.vm.UserError("Result does not belong to this tournament")
        if result["cross_validation"] == "FAIL":
            raise gl.vm.UserError("Cannot finalize with failed cross-validation")
        if result["cross_validation"] not in self.SUPPORTED_CROSS_VALIDATION:
            raise gl.vm.UserError("Unknown cross-validation status")

        name_to_addr = json.loads(tournament["name_to_addr"])
        winner_name = result["winner"]
        if winner_name not in name_to_addr:
            raise gl.vm.UserError("Verified winner is not a registered participant")

        player_a = result["player_a"]
        player_b = result["player_b"]
        if player_a not in name_to_addr or player_b not in name_to_addr:
            raise gl.vm.UserError("Result players are not registered participants")
        if winner_name != player_a and winner_name != player_b:
            raise gl.vm.UserError("Winner must be one of the two match players")

        winner_address = name_to_addr[winner_name]
        if not self._is_valid_address(winner_address):
            raise gl.vm.UserError("Registered payout address is invalid")

        prize_pool = u256(int(tournament["prize_pool"]))
        self._send(Address(winner_address), prize_pool)

        tournament["status"] = "COMPLETED"
        tournament["winner_name"] = winner_name
        tournament["winner_address"] = winner_address
        tournament["verified_result_id"] = str(result_id)
        self.tournaments[tournament_id] = json.dumps(tournament)

    @gl.public.write
    def cancel_tournament(self, tournament_id: str):
        tournament_id = str(tournament_id)
        tournament = json.loads(self.tournaments.get(tournament_id, "{}"))
        if not tournament:
            raise gl.vm.UserError("Tournament not found")
        if tournament["status"] != "OPEN":
            raise gl.vm.UserError("Tournament not open")

        sender = self._sender_hex()
        if sender.lower() != tournament["organizer"].lower():
            raise gl.vm.UserError("Only organizer can cancel")

        prize_pool = u256(int(tournament["prize_pool"]))
        if prize_pool > 0:
            self._send(Address(tournament["organizer"]), prize_pool)

        tournament["status"] = "CANCELLED"
        self.tournaments[tournament_id] = json.dumps(tournament)

    @gl.public.view
    def get_tournament(self, tournament_id: str) -> str:
        return self.tournaments.get(str(tournament_id), "{}")

    @gl.public.view
    def get_result(self, result_id: str) -> str:
        return self.results.get(str(result_id), "{}")

    @gl.public.view
    def get_participants(self, tournament_id: str) -> str:
        tournament = json.loads(self.tournaments.get(str(tournament_id), "{}"))
        if not tournament:
            return "[]"
        return tournament["participants"]

    @gl.public.view
    def get_payout_address(self, tournament_id: str, name: str) -> str:
        tournament = json.loads(self.tournaments.get(str(tournament_id), "{}"))
        if not tournament:
            return ""
        name_to_addr = json.loads(tournament["name_to_addr"])
        return name_to_addr.get(name.strip(), "")

    @gl.public.view
    def get_tournament_count(self) -> int:
        return self.tournament_count

    @gl.public.view
    def get_result_count(self) -> int:
        return self.result_count

    @gl.public.view
    def get_stats(self) -> dict:
        tournaments = len(self.tournaments)
        results = len(self.results)
        cross_valid = 0
        by_winner = {}
        for v in self.results.values():
            r = json.loads(v)
            if r.get("cross_validation") == "PASS":
                cross_valid += 1
            winner = r.get("winner", "UNKNOWN")
            by_winner[winner] = by_winner.get(winner, 0) + 1
        return {
            "tournaments": tournaments,
            "results": results,
            "cross_validated": cross_valid,
            "by_winner": by_winner,
        }
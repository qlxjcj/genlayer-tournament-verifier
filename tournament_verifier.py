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
    status: str
    winner: str
    results: str
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
    verified_at: str


class TournamentVerifier(gl.Contract):
    tournaments: TreeMap[str, str]
    results: TreeMap[str, str]
    tournament_count: u256
    result_count: u256

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

    @gl.public.write.payable
    def create_tournament(self, name: str, game: str, participants_json: str):
        if not name or not name.strip():
            raise gl.vm.UserError("Tournament name is required")
        if not game or not game.strip():
            raise gl.vm.UserError("Game is required")

        try:
            participants = json.loads(participants_json)
        except (json.JSONDecodeError, TypeError):
            raise gl.vm.UserError("Invalid participants JSON")

        if not isinstance(participants, list) or len(participants) < 2:
            raise gl.vm.UserError("At least 2 participants required")

        sender = gl.message.sender_address
        prize_pool = gl.message.value

        from datetime import datetime, timezone
        self.tournament_count += 1
        tournament_id = str(self.tournament_count)

        tournament = Tournament(
            tournament_id=tournament_id,
            name=name.strip(),
            game=game.strip(),
            organizer=sender.as_hex,
            prize_pool=str(prize_pool),
            participants=json.dumps(participants),
            status="OPEN",
            winner="",
            results="{}",
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
                    fetched.append({"url": source["url"], "data": body, "retrieved": True})
                except Exception:
                    fetched.append({"url": source["url"], "data": "", "retrieved": False})

            retrieved = [f for f in fetched if f["retrieved"]]
            if not retrieved:
                return {
                    "score_a": "0",
                    "score_b": "0",
                    "winner": "UNKNOWN",
                    "cross_validation": "FAIL",
                    "source_agreement": "0",
                    "evidence": "{}",
                    "reasoning": "No sources retrieved.",
                }

            parts = []
            for i, r in enumerate(retrieved):
                parts.append("[Source " + str(i+1) + "] " + r["url"] + ":\n" + r["data"][:500])
            sources_text = "\n".join(parts)

            json_format = chr(123) + chr(34) + "score_a" + chr(34) + ": " + chr(34) + "<number>" + chr(34) + ", " + chr(34) + "score_b" + chr(34) + ": " + chr(34) + "<number>" + chr(34) + ", " + chr(34) + "winner" + chr(34) + ": " + chr(34) + "<player>" + chr(34) + ", " + chr(34) + "cross_validation" + chr(34) + ": " + chr(34) + "PASS" + chr(34) + "|" + chr(34) + "FAIL" + chr(34) + "|" + chr(34) + "PARTIAL" + chr(34) + ", " + chr(34) + "source_agreement" + chr(34) + ": " + chr(34) + "<0-100>" + chr(34) + ", " + chr(34) + "evidence" + chr(34) + ": " + chr(123) + chr(34) + "<source>" + chr(34) + ": " + chr(34) + "<quote>" + chr(34) + chr(125) + ", " + chr(34) + "reasoning" + chr(34) + ": " + chr(34) + "<text>" + chr(34) + chr(125)

            task = (
                "You are a tournament result verifier. Verify the match result using the provided sources.\n"
                "MATCH: " + match_id + "\n"
                "PLAYER A: " + player_a + "\n"
                "PLAYER B: " + player_b + "\n"
                "SOURCES (" + str(len(retrieved)) + " retrieved):\n" + sources_text + "\n\n"
                "INSTRUCTIONS:\n"
                "1. Extract score_a and score_b from sources\n"
                "2. Determine winner based on scores\n"
                "3. cross_validation: Compare scores between sources - PASS if agree, FAIL if contradict\n"
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

        try:
            sources = json.loads(sources_json)
        except (json.JSONDecodeError, TypeError):
            raise gl.vm.UserError("Invalid sources JSON")

        if not isinstance(sources, list) or len(sources) < 2:
            raise gl.vm.UserError("At least 2 sources required for cross-validation")

        for s in sources:
            if not isinstance(s, dict) or "url" not in s:
                raise gl.vm.UserError("Each source must have a url field")
            url = s["url"]
            if not url.startswith("http://") and not url.startswith("https://"):
                raise gl.vm.UserError("Invalid URL: " + url)

        result = self._verify_match(tournament_id, match_id, player_a, player_b, sources)

        from datetime import datetime, timezone
        self.result_count += 1
        result_id = str(self.result_count)

        match_result = MatchResult(
            result_id=result_id,
            tournament_id=tournament_id,
            match_id=match_id,
            player_a=player_a,
            player_b=player_b,
            score_a=str(result.get("score_a", "0")),
            score_b=str(result.get("score_b", "0")),
            winner=str(result.get("winner", "UNKNOWN")),
            cross_validation=str(result.get("cross_validation", "FAIL")),
            source_agreement=str(result.get("source_agreement", "0")),
            evidence=json.dumps(result.get("evidence", {})),
            reasoning=str(result.get("reasoning", "")),
            verified_at=datetime.now(timezone.utc).isoformat(),
        )
        self.results[result_id] = json.dumps(match_result.__dict__)
        return result_id

    @gl.public.write
    def finalize_tournament(self, tournament_id: str, winner: str):
        tournament_id = str(tournament_id)
        tournament = json.loads(self.tournaments.get(tournament_id, "{}"))
        if not tournament:
            raise gl.vm.UserError("Tournament not found")
        if tournament["status"] != "OPEN":
            raise gl.vm.UserError("Tournament not open")

        sender = gl.message.sender_address.as_hex
        if sender.lower() != tournament["organizer"].lower():
            raise gl.vm.UserError("Only organizer can finalize")

        tournament["status"] = "COMPLETED"
        tournament["winner"] = winner
        self.tournaments[tournament_id] = json.dumps(tournament)

    @gl.public.view
    def get_tournament(self, tournament_id: str) -> str:
        return self.tournaments.get(str(tournament_id), "{}")

    @gl.public.view
    def get_result(self, result_id: str) -> str:
        return self.results.get(str(result_id), "{}")

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
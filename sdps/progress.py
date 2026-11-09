"""Practice progress: per-challenge drafts, check attempts, hints/solution usage and active time.

Stored in its own SQLite file in the app data directory (stdlib only), separate from the
practice database so regenerating AdventureWorks never erases your history.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .db.config import app_data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    challenge_id TEXT PRIMARY KEY,
    language     TEXT NOT NULL,
    code         TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    challenge_id  TEXT NOT NULL,
    submitted_at  TEXT NOT NULL,
    language      TEXT NOT NULL,
    passed        INTEGER NOT NULL,
    headline      TEXT NOT NULL,
    user_rows     INTEGER,
    expected_rows INTEGER,
    elapsed_ms    REAL,
    code          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attempts_by_challenge ON attempts (challenge_id);
CREATE TABLE IF NOT EXISTS challenge_progress (
    challenge_id    TEXT PRIMARY KEY,
    first_opened    TEXT NOT NULL,
    last_opened     TEXT NOT NULL,
    hints_used      INTEGER NOT NULL DEFAULT 0,
    solution_viewed INTEGER NOT NULL DEFAULT 0,
    seconds_active  INTEGER NOT NULL DEFAULT 0,
    solved_at       TEXT
);
"""

NOT_STARTED, IN_PROGRESS, SOLVED = "not started", "in progress", "solved"


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


@dataclass
class ChallengeStatus:
    state: str = NOT_STARTED
    attempts: int = 0
    hints_used: int = 0
    solution_viewed: bool = False
    seconds_active: int = 0
    solved_at: str | None = None
    last_activity: str | None = None

    @property
    def icon(self) -> str:
        if self.state == SOLVED:
            return "★" if not self.solution_viewed and self.hints_used == 0 else "✓"
        return "◐" if self.state == IN_PROGRESS else "○"

    @property
    def label(self) -> str:
        if self.state == SOLVED:
            if self.solution_viewed:
                return "Solved (after viewing solution)"
            return "Solved, no hints" if self.hints_used == 0 else f"Solved with {self.hints_used} hint(s)"
        return "In progress" if self.state == IN_PROGRESS else "Not started"


@dataclass
class GroupStats:
    name: str
    challenges: int = 0
    attempted: int = 0
    solved: int = 0
    clean_solves: int = 0              # solved without hints or viewing the solution
    attempts: int = 0
    seconds: int = 0
    solution_viewed: int = 0

    @property
    def solve_rate(self) -> float:
        return self.solved / self.attempted if self.attempted else 0.0

    @property
    def weakness(self) -> float:
        """Higher = weaker. Unsolved, solution-peeking and many attempts all count against you."""
        if not self.attempted:
            return 0.0
        unsolved = 1 - self.solve_rate
        peeked = self.solution_viewed / self.attempted
        tries = min(1.0, max(0.0, (self.attempts / max(1, self.solved) - 1) / 4))
        return round(0.55 * unsolved + 0.30 * peeked + 0.15 * tries, 3)


@dataclass
class Summary:
    challenges: int = 0
    attempted: int = 0
    solved: int = 0
    clean_solves: int = 0
    attempts: int = 0
    seconds: int = 0
    active_days: int = 0
    by_category: list[GroupStats] = field(default_factory=list)
    by_difficulty: list[GroupStats] = field(default_factory=list)
    topics: list[GroupStats] = field(default_factory=list)       # weakest first


class ProgressStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else app_data_dir() / "progress.sqlite"
        self._db = sqlite3.connect(str(self.path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # ---- drafts ------------------------------------------------------------------------------
    def save_draft(self, challenge_id: str, language: str, code: str) -> None:
        self._db.execute(
            "INSERT INTO drafts VALUES (?, ?, ?, ?) ON CONFLICT(challenge_id) DO UPDATE SET "
            "language = excluded.language, code = excluded.code, updated_at = excluded.updated_at",
            (challenge_id, language, code, _now()))
        self._db.commit()

    def get_draft(self, challenge_id: str) -> tuple[str, str] | None:
        row = self._db.execute("SELECT language, code FROM drafts WHERE challenge_id = ?", (challenge_id,)).fetchone()
        return (row["language"], row["code"]) if row else None

    def delete_draft(self, challenge_id: str) -> None:
        self._db.execute("DELETE FROM drafts WHERE challenge_id = ?", (challenge_id,))
        self._db.commit()

    # ---- activity ----------------------------------------------------------------------------
    def _touch(self, challenge_id: str) -> None:
        now = _now()
        self._db.execute("INSERT INTO challenge_progress (challenge_id, first_opened, last_opened) VALUES (?, ?, ?) "
                         "ON CONFLICT(challenge_id) DO UPDATE SET last_opened = excluded.last_opened",
                         (challenge_id, now, now))

    def mark_opened(self, challenge_id: str) -> None:
        self._touch(challenge_id)
        self._db.commit()

    def mark_hints(self, challenge_id: str, hints_revealed: int) -> None:
        self._touch(challenge_id)
        self._db.execute("UPDATE challenge_progress SET hints_used = max(hints_used, ?) WHERE challenge_id = ?",
                         (hints_revealed, challenge_id))
        self._db.commit()

    def mark_solution_viewed(self, challenge_id: str) -> None:
        self._touch(challenge_id)
        self._db.execute("UPDATE challenge_progress SET solution_viewed = 1 WHERE challenge_id = ?", (challenge_id,))
        self._db.commit()

    def add_time(self, challenge_id: str, seconds: int) -> None:
        self._touch(challenge_id)
        self._db.execute("UPDATE challenge_progress SET seconds_active = seconds_active + ? WHERE challenge_id = ?",
                         (int(seconds), challenge_id))
        self._db.commit()

    def record_attempt(self, challenge_id: str, language: str, code: str, passed: bool, headline: str,
                       user_rows: int | None = None, expected_rows: int | None = None,
                       elapsed_ms: float | None = None) -> None:
        now = _now()
        self._touch(challenge_id)
        self._db.execute("INSERT INTO attempts (challenge_id, submitted_at, language, passed, headline, user_rows, "
                         "expected_rows, elapsed_ms, code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (challenge_id, now, language, int(passed), headline, user_rows, expected_rows, elapsed_ms, code))
        if passed:
            self._db.execute("UPDATE challenge_progress SET solved_at = coalesce(solved_at, ?) WHERE challenge_id = ?",
                             (now, challenge_id))
        self._db.commit()

    # ---- queries -----------------------------------------------------------------------------
    def status(self, challenge_id: str) -> ChallengeStatus:
        return self.statuses([challenge_id]).get(challenge_id, ChallengeStatus())

    def statuses(self, challenge_ids: list[str] | None = None) -> dict[str, ChallengeStatus]:
        out: dict[str, ChallengeStatus] = {}
        for r in self._db.execute("SELECT * FROM challenge_progress"):
            out[r["challenge_id"]] = ChallengeStatus(
                state=SOLVED if r["solved_at"] else IN_PROGRESS, hints_used=r["hints_used"],
                solution_viewed=bool(r["solution_viewed"]), seconds_active=r["seconds_active"],
                solved_at=r["solved_at"], last_activity=r["last_opened"])
        for r in self._db.execute("SELECT challenge_id, count(*) AS n, max(submitted_at) AS last FROM attempts "
                                  "GROUP BY challenge_id"):
            st = out.setdefault(r["challenge_id"], ChallengeStatus(state=IN_PROGRESS))
            st.attempts = r["n"]
            st.last_activity = max(filter(None, [st.last_activity, r["last"]]), default=None)
        # opening a challenge without working on it doesn't count as "in progress"
        for cid, st in out.items():
            if st.state == IN_PROGRESS and not (st.attempts or st.hints_used or st.solution_viewed
                                                or st.seconds_active >= 60 or self.get_draft(cid)):
                st.state = NOT_STARTED
        if challenge_ids is not None:
            out = {cid: out.get(cid, ChallengeStatus()) for cid in challenge_ids}
        return out

    def attempts(self, challenge_id: str) -> list[sqlite3.Row]:
        return list(self._db.execute("SELECT * FROM attempts WHERE challenge_id = ? ORDER BY id DESC", (challenge_id,)))

    def summary(self, challenges: list) -> Summary:
        """`challenges`: sdps.ai.Challenge objects (for category / difficulty / topics)."""
        statuses = self.statuses([c.id for c in challenges])
        s = Summary(challenges=len(challenges))
        cats: dict[str, GroupStats] = {}
        diffs: dict[str, GroupStats] = {}
        topics: dict[str, GroupStats] = defaultdict(lambda: GroupStats(""))
        for ch in challenges:
            st = statuses[ch.id]
            touched = st.state != NOT_STARTED
            groups = [cats.setdefault(ch.category, GroupStats(ch.category)),
                      diffs.setdefault(ch.difficulty, GroupStats(ch.difficulty))]
            for t in ch.topics or ["(untagged)"]:
                g = topics[f"{ch.category} · {t}"]
                g.name = f"{ch.category} · {t}"
                groups.append(g)
            for g in groups:
                g.challenges += 1
                if touched:
                    g.attempted += 1
                    g.attempts += st.attempts
                    g.seconds += st.seconds_active
                    g.solution_viewed += int(st.solution_viewed)
                if st.state == SOLVED:
                    g.solved += 1
                    g.clean_solves += int(not st.solution_viewed and st.hints_used == 0)
            if touched:
                s.attempted += 1
            s.solved += int(st.state == SOLVED)
            s.clean_solves += int(st.state == SOLVED and not st.solution_viewed and st.hints_used == 0)
            s.attempts += st.attempts
            s.seconds += st.seconds_active
        s.active_days = self._db.execute(
            "SELECT count(DISTINCT substr(submitted_at, 1, 10)) FROM attempts").fetchone()[0]
        s.by_category = sorted(cats.values(), key=lambda g: g.name)
        s.by_difficulty = sorted(diffs.values(), key=lambda g: ["Senior", "Staff", "Principal"].index(g.name)
                                 if g.name in ("Senior", "Staff", "Principal") else 9)
        s.topics = sorted((g for g in topics.values() if g.attempted),
                          key=lambda g: (-g.weakness, -g.attempted, g.name))
        return s

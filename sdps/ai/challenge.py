"""Challenge data model, the JSON schema LLMs must follow, Markdown rendering and a local library."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from ..db.config import app_data_dir

CATEGORIES = ["Advanced SQL", "Python / Pandas", "Analytics Engineering / dbt"]
DIFFICULTIES = ["Senior", "Staff", "Principal"]

TOPICS: dict[str, list[str]] = {
    "Advanced SQL": [
        "Window functions", "Recursive CTEs", "Gaps and islands", "Sessionization",
        "Query optimization & EXPLAIN", "Handling data skew", "Indexing / ordering strategy",
        "As-of joins on SCD2 history", "Deduplication & data quality", "Pivoting & GROUPING SETS",
        "Cohort & retention analysis", "Running totals with resets",
    ],
    "Python / Pandas": [
        "Vectorization (no loops/apply)", "Memory optimization (dtypes, categoricals)",
        "Chunked / out-of-core processing", "merge_asof & time-series joins", "Rolling & expanding windows",
        "Resampling & calendar logic", "Groupby transforms at scale", "Algorithmic transformations",
        "Data validation & anomaly detection",
    ],
    "Analytics Engineering / dbt": [
        "Kimball star schema design", "Staging layer conventions", "SCD2 dimensions / snapshots",
        "Incremental models", "Reusable macros", "Data tests & contracts", "Surrogate keys & conformed dims",
        "Fact table grain", "Metrics layer design",
    ],
}

# JSON schema shared by every provider (Claude structured outputs, Ollama `format`).
CHALLENGE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "category": {"type": "string", "enum": CATEGORIES},
        "difficulty": {"type": "string", "enum": DIFFICULTIES},
        "estimated_minutes": {"type": "integer"},
        "topics": {"type": "array", "items": {"type": "string"}},
        "scenario": {"type": "string"},
        "tasks": {"type": "array", "items": {"type": "string"}},
        "deliverable": {"type": "string"},
        "tables_used": {"type": "array", "items": {"type": "string"}},
        "expected_columns": {"type": "array", "items": {"type": "string"}},
        "order_matters": {"type": "boolean"},
        "hints": {"type": "array", "items": {"type": "string"}},
        "reference_solution": {"type": "string"},
        "solution_explanation": {"type": "string"},
        "additional_artifacts": {"type": "string"},
        "follow_ups": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "category", "difficulty", "estimated_minutes", "topics", "scenario", "tasks",
                 "deliverable", "tables_used", "expected_columns", "order_matters", "hints",
                 "reference_solution", "solution_explanation", "additional_artifacts", "follow_ups"],
    "additionalProperties": False,
}


_FENCE = re.compile(r"^\s*```[\w+-]*[ \t]*\n(.*?)\n?```\s*$", re.DOTALL)


def clean_code(code: str) -> str:
    """Undo common small-model formatting mistakes in a code field: Markdown fences around the
    code, and newlines that arrive as literal backslash-n sequences."""
    code = code.strip()
    if m := _FENCE.match(code):
        code = m.group(1).strip()
    if "\n" not in code and "\\n" in code:
        code = code.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "    ").replace('\\"', '"')
    return code


class ChallengeFormatError(ValueError):
    """The model's output did not match the challenge schema."""


@dataclass
class Challenge:
    title: str
    category: str
    difficulty: str
    estimated_minutes: int
    topics: list[str]
    scenario: str
    tasks: list[str]
    deliverable: str
    tables_used: list[str]
    expected_columns: list[str]
    order_matters: bool
    hints: list[str]
    reference_solution: str
    solution_explanation: str
    additional_artifacts: str = ""
    follow_ups: list[str] = field(default_factory=list)
    # ---- metadata added by the app (not produced by the model) ----
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    provider: str = ""
    model: str = ""
    dialect: str = ""
    verified: bool = False
    verification_notes: list[str] = field(default_factory=list)
    cost_usd: float | None = None
    generation_seconds: float = 0.0

    _MODEL_FIELDS = tuple(CHALLENGE_SCHEMA["required"])

    @property
    def language(self) -> str:
        return "Python" if self.category == "Python / Pandas" else "SQL"

    @classmethod
    def from_model_output(cls, data: object) -> "Challenge":
        """Validate and normalise raw model JSON (dict or str). Raises ChallengeFormatError."""
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as e:
                raise ChallengeFormatError(f"Output is not valid JSON ({e.msg} at char {e.pos}).") from e
        if not isinstance(data, dict):
            raise ChallengeFormatError("Output must be a JSON object.")
        problems, clean = [], {}
        for key in cls._MODEL_FIELDS:
            spec = CHALLENGE_SCHEMA["properties"][key]
            if key not in data:
                problems.append(f"missing field '{key}'")
                continue
            value = data[key]
            if spec["type"] == "array":
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    problems.append(f"'{key}' must be a list of strings")
                    continue
                value = [v.strip() for v in value if v.strip()]
            elif spec["type"] == "integer":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    problems.append(f"'{key}' must be an integer")
                    continue
            elif spec["type"] == "boolean":
                if not isinstance(value, bool):
                    problems.append(f"'{key}' must be true/false")
                    continue
            elif not isinstance(value, str):
                problems.append(f"'{key}' must be a string")
                continue
            if "enum" in spec and value not in spec["enum"]:
                problems.append(f"'{key}' must be one of {spec['enum']}")
                continue
            clean[key] = value
        for key in ("title", "scenario", "reference_solution"):
            if key in clean and not clean[key].strip():
                problems.append(f"'{key}' must not be empty")
        if "tasks" in clean and not clean["tasks"]:
            problems.append("'tasks' must not be empty")
        if problems:
            raise ChallengeFormatError("; ".join(problems))
        clean["reference_solution"] = clean_code(clean["reference_solution"])
        return cls(**clean)

    def model_json(self) -> str:
        return json.dumps({k: getattr(self, k) for k in self._MODEL_FIELDS}, indent=2)

    # ---- rendering --------------------------------------------------------------------------
    def to_markdown(self, hints_revealed: int = 0, show_solution: bool = False) -> str:
        md = [f"## {self.title}", "",
              f"*{self.category} · {self.difficulty} · ~{self.estimated_minutes} min*"
              + (f" · {', '.join(self.topics)}" if self.topics else ""), "",
              self.scenario.strip(), "", "**Your tasks**", ""]
        md += [f"{i}. {t}" for i, t in enumerate(self.tasks, 1)]
        md += ["", f"**Deliverable:** {self.deliverable.strip()}"]
        if self.tables_used:
            md += ["", "**Tables:** " + ", ".join(f"`{t}`" for t in self.tables_used)]
        if self.expected_columns:
            md += ["", "**Expected output columns:** " + ", ".join(f"`{c}`" for c in self.expected_columns)
                   + (" (ordered)" if self.order_matters else "")]
        if hints_revealed:
            md += ["", "---", "", "**Hints**", ""]
            md += [f"{i}. {h}" for i, h in enumerate(self.hints[:hints_revealed], 1)]
        if show_solution:
            fence = "python" if self.language == "Python" else "sql"
            md += ["", "---", "", "**Reference solution**", "", f"```{fence}", self.reference_solution.strip(), "```",
                   "", self.solution_explanation.strip()]
            if self.additional_artifacts.strip():
                md += ["", "**Additional artifacts**", "", self.additional_artifacts.strip()]
            if self.follow_ups:
                md += ["", "**Interview follow-ups**", ""]
                md += [f"- {q}" for q in self.follow_ups]
        return "\n".join(md)

    def editor_starter(self) -> str:
        c = "#" if self.language == "Python" else "--"
        lines = [f"{c} Challenge: {self.title}", f"{c} {self.category} · {self.difficulty}", f"{c}"]
        lines += [f"{c} {i}. {t}" for i, t in enumerate(self.tasks, 1)]
        if self.language == "Python":
            lines += [f"{c}", f"{c} Available: pd, np, sql(query) -> DataFrame. Assign your answer to `result`.", "",
                      "result = None", ""]
        else:
            lines += ["", ""]
        return "\n".join(lines)

    # ---- persistence ------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Challenge":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


class ChallengeStore:
    """Keeps generated challenges in the app data dir (newest first, capped)."""

    MAX = 300

    def __init__(self, path: Path | None = None):
        self.path = path or (app_data_dir() / "challenges.json")
        self._items: list[Challenge] = []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._items = [Challenge.from_dict(d) for d in raw]
        except (OSError, ValueError, TypeError):
            self._items = []

    def all(self) -> list[Challenge]:
        return list(self._items)

    def recent_titles(self, category: str | None = None, n: int = 25) -> list[str]:
        return [c.title for c in self._items if category is None or c.category == category][:n]

    def get(self, challenge_id: str) -> Challenge | None:
        return next((c for c in self._items if c.id == challenge_id), None)

    def add(self, challenge: Challenge) -> None:
        self._items = [challenge] + [c for c in self._items if c.id != challenge.id]
        del self._items[self.MAX:]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([c.to_dict() for c in self._items], indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

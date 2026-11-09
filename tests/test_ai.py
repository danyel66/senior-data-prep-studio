"""Phase 3: challenge model, providers (against local fake servers), verification/repair, UI flow."""
import json
import threading

import pytest

from conftest import wait_until
from fakes import FakeAnthropic, FakeOllama, sql_challenge
from sdps.ai import AICancelled, AIConfig, AIError, Challenge, ChallengeFormatError, ChallengeGenerator, \
    ChallengeStore, ClaudeProvider, OllamaProvider
from sdps.ai.prompts import build_system_prompt, build_user_prompt
from sdps.db import ConnectionManager


@pytest.fixture
def fake_claude():
    f = FakeAnthropic()
    f.responses = [sql_challenge()]
    yield f
    f.close()


@pytest.fixture
def fake_ollama():
    f = FakeOllama()
    f.responses = [sql_challenge()]
    yield f
    f.close()


@pytest.fixture
def db(tiny_config):
    m = ConnectionManager(tiny_config)
    m.connect()
    yield m
    m.disconnect()


def claude(fake, **cfg):
    config = AIConfig(**cfg)
    return config, ClaudeProvider(config, api_key="test-key", base_url=fake.url)


# ───────────────────────────────────── challenge model ─────────────────────────────────────
def test_challenge_parses_and_renders():
    ch = Challenge.from_model_output(sql_challenge())
    assert ch.language == "SQL" and len(ch.hints) == 3
    md0 = ch.to_markdown()
    assert "Your tasks" in md0 and "Hints" not in md0 and "Reference solution" not in md0
    assert "Use LAG." not in md0
    md1 = ch.to_markdown(hints_revealed=1)
    assert "Aggregate first." in md1 and "Use LAG." not in md1
    assert "```sql" in ch.to_markdown(show_solution=True)
    assert ch.editor_starter().startswith("-- Challenge:")


@pytest.mark.parametrize("bad, fragment", [
    ("not json", "not valid JSON"),
    (json.dumps({"title": "x"}), "missing field"),
    (sql_challenge(difficulty="Junior"), "difficulty"),
    (sql_challenge(tasks="one"), "list of strings"),
    (sql_challenge(reference_solution="  "), "must not be empty"),
])
def test_challenge_rejects_malformed_output(bad, fragment):
    with pytest.raises(ChallengeFormatError, match=fragment):
        Challenge.from_model_output(bad)


def test_store_persists_newest_first(tmp_path):
    store = ChallengeStore(tmp_path / "c.json")
    a = Challenge.from_model_output(sql_challenge(title="A"))
    b = Challenge.from_model_output(sql_challenge(title="B"))
    store.add(a)
    store.add(b)
    reloaded = ChallengeStore(tmp_path / "c.json")
    assert [c.title for c in reloaded.all()] == ["B", "A"]
    assert reloaded.recent_titles("Advanced SQL") == ["B", "A"]
    assert reloaded.get(a.id).title == "A"


def test_prompts_ground_in_schema_and_dialect():
    sys_duck = build_system_prompt("duckdb", "- profile line")
    assert "CREATE TABLE sales.sales_order_header" in sys_duck and "QUALIFY" in sys_duck
    assert "Do not use DuckDB-only syntax" in build_system_prompt("postgres", "")
    user = build_user_prompt("Advanced SQL", "Staff", "Recursive CTEs", ["Old title"])
    assert "Staff" in user and "Recursive CTEs" in user and "- Old title" in user


# ─────────────────────────────────────── providers ────────────────────────────────────────
def test_claude_request_shape(fake_claude):
    _, provider = claude(fake_claude, claude_effort="high")
    progress = []
    out = provider.complete("SYSTEM", "USER", {"type": "object"}, progress.append, threading.Event())
    assert json.loads(out.text)["title"] and out.model == "claude-opus-5-5"
    assert out.output_tokens == 1500 and out.cache_write_tokens == 5000 and out.cost_usd > 0
    req = fake_claude.requests[0]
    body = req["body"]
    assert "test-key" in (req["headers"].get("authorization", "") + req["headers"].get("x-api-key", ""))
    assert body["model"] == "claude-opus-5-5" and body["stream"] is True and body["max_tokens"] == 64000
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in req["headers"]["anthropic-beta"]
    assert body["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["system"] == [{"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}]
    assert any(p.startswith("Thinking:") for p in progress) and any(p.startswith("Writing") for p in progress)


def test_claude_haiku_has_no_fallback(fake_claude):
    _, provider = claude(fake_claude, claude_model="claude-haiku-5-5")
    provider.complete("S", "U", {}, lambda m: None, threading.Event())
    assert "fallbacks" not in fake_claude.requests[0]["body"]


def test_claude_auth_error_points_to_settings(fake_claude):
    fake_claude.status = 401
    _, provider = claude(fake_claude)
    with pytest.raises(AIError) as e:
        provider.complete("S", "U", {}, lambda m: None, threading.Event())
    assert e.value.settings_hint and "rejected" in str(e.value)


def test_ollama_lists_models_and_streams(fake_ollama):
    cfg = AIConfig(provider="ollama", ollama_url=fake_ollama.url, ollama_model="qwen2.5-coder:14b", ollama_num_ctx=20000)
    p = OllamaProvider(cfg)
    assert p.list_models() == ["llama3.1:8b", "qwen2.5-coder:14b"]
    out = p.complete("SYS", "USR", {"type": "object"}, lambda m: None, threading.Event())
    assert json.loads(out.text)["category"] == "Advanced SQL" and out.cost_usd == 0.0
    body = fake_ollama.requests[0]["body"]
    assert body["format"] == {"type": "object"} and body["options"]["num_ctx"] == 20000
    assert body["messages"][0] == {"role": "system", "content": "SYS"}


def test_ollama_errors_are_actionable(fake_ollama):
    missing = OllamaProvider(AIConfig(provider="ollama", ollama_url=fake_ollama.url, ollama_model="nope:1b"))
    with pytest.raises(AIError, match="ollama pull nope:1b"):
        missing.complete("S", "U", {}, lambda m: None, threading.Event())
    offline = OllamaProvider(AIConfig(provider="ollama", ollama_url="http://127.0.0.1:9", ollama_model="x"))
    with pytest.raises(AIError) as e:
        offline.list_models()
    assert e.value.settings_hint


# ───────────────────────────────── generator & verification ─────────────────────────────────
def gen(db, fake, store=None):
    cfg, provider = claude(fake)
    return ChallengeGenerator(db, cfg, store, provider=provider)


def test_generates_verified_sql_challenge(db, fake_claude, tmp_path):
    store = ChallengeStore(tmp_path / "s.json")
    store.add(Challenge.from_model_output(sql_challenge(title="Earlier one")))
    res = gen(db, fake_claude, store).generate("Advanced SQL", "Senior", "Window functions")
    ch = res.challenge
    assert ch.verified and ch.verification_notes == [] and res.attempts == 1
    assert ch.dialect == "duckdb" and ch.provider == "claude" and ch.cost_usd > 0
    system = fake_claude.requests[0]["body"]["system"][0]["text"]
    assert "Dataset scale: tiny" in system and "Order share by territory" in system
    assert "- Earlier one" in fake_claude.requests[0]["body"]["messages"][0]["content"]


def test_bad_column_triggers_repair(db, fake_claude):
    fake_claude.responses = [sql_challenge(reference_solution="SELECT totaldue FROM sales.sales_order_header"),
                             sql_challenge()]
    res = gen(db, fake_claude).generate("Advanced SQL", "Senior")
    assert res.attempts == 2 and res.challenge.verified
    repair = fake_claude.requests[1]["body"]["messages"][0]["content"]
    assert "totaldue" in repair and "Return the corrected challenge" in repair


def test_unknown_table_and_unfixable_solution_is_flagged_not_hidden(db, fake_claude):
    fake_claude.responses = [sql_challenge(tables_used=["sales.invoice"])]
    res = gen(db, fake_claude).generate("Advanced SQL", "Senior")
    assert res.attempts == 3 and not res.challenge.verified
    assert "sales.invoice" in res.challenge.verification_notes[0]


def test_alias_named_like_schema_is_not_a_false_positive(db, fake_claude):
    fake_claude.responses = [sql_challenge(
        tables_used=["person.person"],
        reference_solution="SELECT person.first_name FROM person.person person ORDER BY 1",
        expected_columns=["first_name"])]
    assert gen(db, fake_claude).generate("Advanced SQL", "Senior").challenge.verified


def test_invalid_json_is_repaired(db, fake_claude):
    fake_claude.responses = ['{"title": "trunc', sql_challenge()]
    res = gen(db, fake_claude).generate("Advanced SQL", "Staff")
    assert res.attempts == 2 and res.challenge.verified and res.challenge.difficulty == "Staff"


PY_OK = ("orders = sql(\"SELECT customer_id, total_due FROM sales.sales_order_header\")\n"
         "result = orders.groupby('customer_id', as_index=False)['total_due'].sum()\n")


@pytest.mark.parametrize("code, ok, note", [
    (PY_OK, True, None),
    ("orders = sql(\"SELECT 1\")\nanswer = orders", False, "never assigns"),
    ("result = sql(\"SELECT nope FROM sales.customer\")", False, "sql() query"),
    ("result = (", False, "syntax error"),
])
def test_python_solution_verification(db, fake_claude, code, ok, note):
    fake_claude.responses = [sql_challenge(category="Python / Pandas", reference_solution=code,
                                           tables_used=["sales.sales_order_header"])]
    ch = gen(db, fake_claude).generate("Python / Pandas", "Senior").challenge
    assert ch.verified is ok and ch.language == "Python"
    if note:
        assert note in " ".join(ch.verification_notes)


def test_dbt_sources_are_rendered_and_ref_rejected(db, fake_claude):
    model = ("{{ config(materialized='table') }}\nSELECT c.customer_id, c.territory_id\n"
             "FROM {{ source('sales', 'customer') }} c")
    fake_claude.responses = [sql_challenge(category="Analytics Engineering / dbt", reference_solution=model,
                                           tables_used=["sales.customer"])]
    assert gen(db, fake_claude).generate("Analytics Engineering / dbt", "Staff").challenge.verified
    fake_claude.responses = [sql_challenge(reference_solution="SELECT * FROM {{ ref('stg_orders') }}")]
    ch = gen(db, fake_claude).generate("Analytics Engineering / dbt", "Staff").challenge
    assert not ch.verified and "ref()" in ch.verification_notes[0]


def test_cancel_before_start(db, fake_claude):
    g = gen(db, fake_claude)
    g.cancel()
    with pytest.raises(AICancelled):
        g.generate("Advanced SQL", "Senior")


def test_requires_seeded_database(tmp_path, fake_claude):
    from sdps.db import DatabaseConfig
    m = ConnectionManager(DatabaseConfig(duckdb_path=str(tmp_path / "empty.duckdb")))
    m.connect()
    with pytest.raises(AIError, match="Generate AdventureWorks"):
        gen(m, fake_claude).generate("Advanced SQL", "Senior")
    m.disconnect()


# ───────────────────────────────────────── UI flow ──────────────────────────────────────────
def test_ui_generates_challenge_end_to_end(qapp, tiny_config, fake_ollama):
    from sdps.ui.main_window import MainWindow
    fake_ollama.responses = [sql_challenge(title="UI flow challenge")]
    w = MainWindow(ConnectionManager(tiny_config), prompt_to_seed=False)
    w.ai_config = AIConfig(provider="ollama", ollama_url=fake_ollama.url, ollama_model="qwen2.5-coder:14b")
    w._apply_ai_config()
    w.show()
    wait_until(lambda: w.tables)
    panel = w.prompt_panel
    panel.difficulty_combo.setCurrentText("Staff")
    panel.generate_button.click()
    wait_until(lambda: panel.challenge is not None and panel.challenge.title == "UI flow challenge", timeout=30)
    ch = panel.challenge
    assert ch.verified and ch.difficulty == "Staff"
    assert panel.footer.isVisible() and "verified" in panel.meta.text()
    assert w.editor_panel.editor.toPlainText().startswith("-- Challenge: UI flow challenge")
    panel.next_hint()
    assert panel.hints_revealed == 1 and "Aggregate first." in panel.browser.toMarkdown()
    w._insert_solution()
    assert "lag(revenue)" in w.editor_panel.editor.toPlainText() and panel.solution_shown
    w.editor_panel.editor.undo()
    assert w.editor_panel.editor.toPlainText().startswith("-- Challenge:")
    assert w.challenges.all()[0].title == "UI flow challenge"
    w.close()


def test_ui_shows_actionable_error_when_ollama_offline(qapp, tiny_config):
    from sdps.ui.main_window import MainWindow
    w = MainWindow(ConnectionManager(tiny_config), prompt_to_seed=False)
    w.ai_config = AIConfig(provider="ollama", ollama_url="http://127.0.0.1:9", ollama_model="x")
    wait_until(lambda: w.tables)
    w.generate_challenge("Advanced SQL", "Senior")
    wait_until(lambda: w._generator is None and "✗" in w.prompt_panel.status.text(), timeout=30)
    assert "Open AI Settings" in w.prompt_panel.status.text()
    w.close()


# ───────────────────────────────────────── Phase 3.1 ──────────────────────────────────────────
@pytest.mark.parametrize("category", ["Advanced SQL", "Python / Pandas", "Analytics Engineering / dbt"])
def test_worked_examples_are_valid_and_verified(db, category):
    from sdps.ai.examples import EXAMPLES, example_json
    ch = Challenge.from_model_output(example_json(category))
    assert ch.category == category and ch.difficulty == "Staff" and len(ch.hints) == 3
    known = {t.qualified_name.lower(): t for t in db.introspect()}
    g = ChallengeGenerator(db, AIConfig(), provider=OllamaProvider(AIConfig()))
    assert g.verify(ch, known) == [], EXAMPLES[category]["title"]


def test_fenced_and_escaped_code_from_small_models_still_verifies(db, fake_ollama):
    fenced = "```python\n" + PY_OK + "```"
    escaped = PY_OK.replace("\n", "\\n")
    for code in (fenced, escaped):
        fake_ollama.responses = [sql_challenge(category="Python / Pandas", reference_solution=code,
                                               tables_used=["sales.sales_order_header"])]
        cfg = AIConfig(provider="ollama", ollama_url=fake_ollama.url, ollama_model="qwen2.5-coder:14b")
        res = ChallengeGenerator(db, cfg).generate("Python / Pandas", "Staff")
        assert res.attempts == 1 and res.challenge.verified, res.challenge.verification_notes
        assert not res.challenge.reference_solution.startswith("```")


def test_only_local_models_get_the_worked_example(db, fake_ollama, fake_claude):
    cfg = AIConfig(provider="ollama", ollama_url=fake_ollama.url, ollama_model="qwen2.5-coder:14b")
    ChallengeGenerator(db, cfg).generate("Advanced SQL", "Senior")
    local_user = fake_ollama.requests[0]["body"]["messages"][1]["content"]
    assert "Customers-to-50% by territory" in local_user and "Do NOT reuse" in local_user
    gen(db, fake_claude).generate("Advanced SQL", "Senior")
    assert "Customers-to-50%" not in fake_claude.requests[0]["body"]["messages"][0]["content"]


def test_unverified_reason_is_visible_in_panel(qapp):
    from sdps.ui.widgets.prompt_panel import PromptPanel
    p = PromptPanel()
    ch = Challenge.from_model_output(sql_challenge())
    ch.verified, ch.verification_notes = False, ["Table 'sales.invoice' does not exist."]
    p.show_challenge(ch)
    assert "sales.invoice" in p.status.text() and not p.status.isHidden()
    assert "sales.invoice" in p.meta.toolTip()


def test_window_saved_off_screen_is_pulled_back(qapp, tiny_config):
    from PyQt6.QtGui import QGuiApplication
    from sdps.ui.main_window import MainWindow
    w = MainWindow(ConnectionManager(tiny_config), auto_connect=False, prompt_to_seed=False)
    w.move(50_000, 50_000)
    w._ensure_on_screen()
    area = QGuiApplication.primaryScreen().availableGeometry()
    assert area.intersects(w.frameGeometry())
    w.close()


# ───────────────────────────────────────── 0.4.1: memory ─────────────────────────────────────────
def test_ollama_unloads_model_by_default(fake_ollama):
    cfg = AIConfig(provider="ollama", ollama_url=fake_ollama.url, ollama_model="qwen2.5-coder:14b")
    assert cfg.ollama_num_ctx == 12288 and cfg.ollama_unload_after
    OllamaProvider(cfg).complete("S", "U", {}, lambda m: None, threading.Event())
    assert fake_ollama.requests[-1]["body"]["keep_alive"] == 0
    cfg.ollama_unload_after = False
    OllamaProvider(cfg).complete("S", "U", {}, lambda m: None, threading.Event())
    assert fake_ollama.requests[-1]["body"]["keep_alive"] == "5m"


def test_old_default_context_size_is_migrated():
    old = AIConfig(provider="ollama", ollama_num_ctx=16384)
    old.save()
    assert AIConfig.load().ollama_num_ctx == 12288
    custom = AIConfig(provider="ollama", ollama_num_ctx=24576)
    custom.save()
    assert AIConfig.load().ollama_num_ctx == 24576
    AIConfig().save()


def test_memory_pressure_label(qapp):
    from sdps.ui.widgets.telemetry_panel import TelemetryPanel
    t = TelemetryPanel()
    t._refresh_live_memory()
    assert "app:" in t.live_mem_label.text() and "Mac memory" in t.live_mem_label.text()

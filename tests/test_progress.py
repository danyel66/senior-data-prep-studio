"""Extras: progress store, saved drafts, attempt recording, Progress dialog."""
import pytest

from conftest import wait_until
from fakes import sql_challenge
from sdps.ai import Challenge
from sdps.ai.examples import example_json
from sdps.db import ConnectionManager
from sdps.progress import IN_PROGRESS, NOT_STARTED, SOLVED, ProgressStore


def ch(**kw) -> Challenge:
    return Challenge.from_model_output(sql_challenge(**kw))


# ─────────────────────────────────────────── store ───────────────────────────────────────────
def test_drafts_upsert_and_persist(tmp_path):
    p = ProgressStore(tmp_path / "p.sqlite")
    p.save_draft("c1", "SQL", "SELECT 1")
    p.save_draft("c1", "SQL", "SELECT 2")
    p.close()
    p2 = ProgressStore(tmp_path / "p.sqlite")
    assert p2.get_draft("c1") == ("SQL", "SELECT 2")
    p2.delete_draft("c1")
    assert p2.get_draft("c1") is None


def test_status_lifecycle(tmp_path):
    p = ProgressStore(tmp_path / "p.sqlite")
    p.mark_opened("c")
    assert p.status("c").state == NOT_STARTED            # opening alone isn't progress
    p.record_attempt("c", "SQL", "x", False, "Not quite")
    st = p.status("c")
    assert st.state == IN_PROGRESS and st.attempts == 1 and st.icon == "◐"
    p.record_attempt("c", "SQL", "y", True, "Correct")
    p.record_attempt("c", "SQL", "y", True, "Correct")
    st = p.status("c")
    assert st.state == SOLVED and st.attempts == 3 and st.icon == "★" and "no hints" in st.label
    p.mark_hints("c", 2)
    p.mark_hints("c", 1)                                  # never decreases
    assert p.status("c").hints_used == 2 and p.status("c").icon == "✓"
    assert len(p.attempts("c")) == 3


def test_summary_ranks_weak_topics_first(tmp_path):
    p = ProgressStore(tmp_path / "p.sqlite")
    strong = ch(title="S", topics=["Window functions"])
    weak = ch(title="W", topics=["Recursive CTEs"])
    untouched = ch(title="U", topics=["Gaps and islands"], difficulty="Principal")
    p.record_attempt(strong.id, "SQL", "", True, "Correct")
    for _ in range(3):
        p.record_attempt(weak.id, "SQL", "", False, "Not quite")
    p.mark_solution_viewed(weak.id)
    p.add_time(weak.id, 600)
    s = p.summary([strong, weak, untouched])
    assert (s.challenges, s.attempted, s.solved, s.clean_solves, s.attempts, s.seconds) == (3, 2, 1, 1, 4, 600)
    assert [t.name for t in s.topics] == ["Advanced SQL · Recursive CTEs", "Advanced SQL · Window functions"]
    assert s.topics[0].weakness > 0.6 > s.topics[1].weakness
    assert {g.name for g in s.by_difficulty} == {"Senior", "Principal"}


# ──────────────────────────────────────────── UI ─────────────────────────────────────────────
@pytest.fixture
def window(qapp, tiny_config, tmp_path):
    from sdps.ui.main_window import MainWindow
    w = MainWindow(ConnectionManager(tiny_config), prompt_to_seed=False)
    w.progress_store = ProgressStore(tmp_path / "progress.sqlite")      # isolated per test
    w.show()
    wait_until(lambda: w.tables)
    yield w
    w.close()


def test_drafts_survive_switching_challenges(window):
    a, b = ch(title="Draft A"), ch(title="Draft B")
    window.challenges.add(a)
    window.challenges.add(b)
    window._open_challenge(a)
    window.editor_panel.editor.setPlainText("SELECT 'my work on A'")
    window._open_challenge(b)                       # leaving A saves its draft immediately
    assert window.editor_panel.editor.toPlainText().startswith("-- Challenge: Draft B")
    window._open_challenge(a)
    assert window.editor_panel.editor.toPlainText() == "SELECT 'my work on A'"
    window._reset_code()
    assert window.editor_panel.editor.toPlainText().startswith("-- Challenge: Draft A")
    assert window.progress_store.get_draft(a.id) is None


def test_autosave_after_typing(window):
    a = ch(title="Autosave")
    window._open_challenge(a)
    window.editor_panel.editor.setPlainText("SELECT 42")
    wait_until(lambda: window.progress_store.get_draft(a.id) == ("SQL", "SELECT 42"), timeout=5)


def test_check_records_attempts_and_solve(window):
    challenge = Challenge.from_model_output(example_json("Advanced SQL"))
    window.challenges.add(challenge)
    window._open_challenge(challenge)

    def check(code):
        window.editor_panel.load_text("SQL", code)
        window.editor_panel.check_button.click()
        wait_until(lambda: window._executor is None and window.editor_panel.run_button.isEnabled(), timeout=60)

    check(challenge.reference_solution.replace("WHERE h.status <> 6", "WHERE true"))
    st = window.progress_store.status(challenge.id)
    assert st.state == IN_PROGRESS and st.attempts == 1
    assert "In progress" in window.prompt_panel.progress_label.text()
    check(challenge.reference_solution)
    st = window.progress_store.status(challenge.id)
    assert st.state == SOLVED and st.attempts == 2 and st.icon == "★"
    assert "Solved" in window.prompt_panel.progress_label.text()
    assert window.progress_store.attempts(challenge.id)[0]["passed"] == 1


def test_hints_and_solution_are_remembered(window):
    a = ch(title="Hints")
    window.challenges.add(a)
    window._open_challenge(a)
    window.prompt_panel.next_hint()
    window.prompt_panel.next_hint()
    window._open_challenge(ch(title="Other"))
    window._open_challenge(a)
    assert window.prompt_panel.hints_revealed == 2 and not window.prompt_panel.solution_shown
    window.prompt_panel.show_solution()
    window._open_challenge(a)
    assert window.prompt_panel.solution_shown
    assert window.progress_store.status(a.id).solution_viewed


def test_active_time_only_counts_when_active(window, monkeypatch):
    a = ch(title="Timer")
    window._open_challenge(a)
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    window._count_active_time()
    assert window.progress_store.status(a.id).seconds_active == 15
    window._last_activity -= 600                          # idle for 10 minutes
    window._count_active_time()
    assert window.progress_store.status(a.id).seconds_active == 15


def test_progress_dialog_opens_challenge_and_targets_weak_topic(window):
    from sdps.ui.dialogs import ProgressDialog
    weak = ch(title="Weak one", topics=["Recursive CTEs"])
    window.challenges.add(weak)
    window.progress_store.record_attempt(weak.id, "SQL", "", False, "Not quite")
    dlg = ProgressDialog(window.progress_store, window.challenges.all(), window)
    titles = [dlg.challenge_table.item(r, 1).text() for r in range(dlg.challenge_table.rowCount())]
    assert "Weak one" in titles
    assert dlg.topic_table.item(0, 0).text() == "Advanced SQL · Recursive CTEs"
    picked = []
    dlg.practise_topic.connect(lambda c, t: picked.append((c, t)))
    dlg.topic_table.selectRow(0)
    dlg._practise_selected()
    assert picked == [("Advanced SQL", "Recursive CTEs")]
    window._practise_topic(*picked[0])
    assert window.prompt_panel.topic_combo.currentText() == "Recursive CTEs"
    opened = []
    dlg2 = ProgressDialog(window.progress_store, window.challenges.all(), window)
    dlg2.open_challenge.connect(opened.append)
    row = titles.index("Weak one")
    dlg2._open_selected(dlg2.challenge_table.item(row, 1))
    assert opened == [weak.id]

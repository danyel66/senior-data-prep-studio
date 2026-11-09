"""My Progress: totals, every challenge with its status, and weakest topics to practise next."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, \
    QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget

from ...progress import NOT_STARTED, SOLVED, ProgressStore
from ..theme import C
from ..widgets.telemetry_panel import MetricCard


def fmt_time(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s" if seconds else "—"
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _item(text: str, align_right: bool = False, color: str | None = None, data=None) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if align_right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if color:
        it.setForeground(QColor(color))
    if data is not None:
        it.setData(Qt.ItemDataRole.UserRole, data)
    return it


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.setAlternatingRowColors(True)
    t.setShowGrid(False)
    t.setSortingEnabled(False)
    t.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    return t


class ProgressDialog(QDialog):
    open_challenge = pyqtSignal(str)          # challenge id
    practise_topic = pyqtSignal(str, str)     # (category, topic)

    def __init__(self, progress: ProgressStore, challenges: list, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("My Progress")
        self.resize(940, 620)
        self._progress = progress
        self._challenges = challenges
        summary = progress.summary(challenges)
        self._statuses = progress.statuses([c.id for c in challenges])

        cards = QGridLayout()
        cards.setSpacing(8)
        solved = MetricCard("Solved", f"{summary.solved} / {summary.challenges}")
        solved.set_value(f"{summary.solved} / {summary.challenges}", C.success if summary.solved else None)
        for i, card in enumerate([
            solved,
            MetricCard("Solved without help", str(summary.clean_solves)),
            MetricCard("Answers checked", str(summary.attempts)),
            MetricCard("Active practice time", fmt_time(summary.seconds)),
            MetricCard("Days practised", str(summary.active_days)),
        ]):
            cards.addWidget(card, 0, i)

        # ---- challenges tab ---------------------------------------------------------------------
        self.filter = QComboBox()
        self.filter.addItems(["All challenges", "Solved", "In progress", "Not started"])
        self.filter.currentIndexChanged.connect(self._fill_challenges)
        hint = QLabel("Double-click a challenge to open it with your saved code.")
        hint.setObjectName("Muted")
        top = QHBoxLayout()
        top.addWidget(self.filter)
        top.addWidget(hint)
        top.addStretch()
        self.challenge_table = _table(["Status", "Challenge", "Category", "Difficulty", "Checks", "Time", "Last activity"])
        self.challenge_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.challenge_table.itemDoubleClicked.connect(self._open_selected)
        ch_page = QWidget()
        lay = QVBoxLayout(ch_page)
        lay.addLayout(top)
        lay.addWidget(self.challenge_table)
        self._fill_challenges()

        # ---- weak topics tab --------------------------------------------------------------------
        self.topic_table = _table(["Topic", "Attempted", "Solved", "Solve rate", "Solution viewed", "Checks", "Time",
                                   "Focus"])
        self.topic_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._topics = summary.topics
        for g in summary.topics:
            r = self.topic_table.rowCount()
            self.topic_table.insertRow(r)
            weak = g.weakness >= 0.4
            self.topic_table.setItem(r, 0, _item(g.name, color=C.warning if weak else None))
            self.topic_table.setItem(r, 1, _item(str(g.attempted), True))
            self.topic_table.setItem(r, 2, _item(str(g.solved), True))
            self.topic_table.setItem(r, 3, _item(f"{g.solve_rate:.0%}", True,
                                                 C.success if g.solve_rate >= 0.8 else C.warning if weak else None))
            self.topic_table.setItem(r, 4, _item(str(g.solution_viewed), True))
            self.topic_table.setItem(r, 5, _item(str(g.attempts), True))
            self.topic_table.setItem(r, 6, _item(fmt_time(g.seconds), True))
            self.topic_table.setItem(r, 7, _item("needs work" if weak else "ok", color=C.warning if weak else C.success))
        practise = QPushButton("✦  Practise selected topic")
        practise.setObjectName("Primary")
        practise.clicked.connect(self._practise_selected)
        self.topic_table.itemDoubleClicked.connect(lambda _i: self._practise_selected())
        explain = QLabel("Weakest first. Weakness combines unsolved challenges, viewing the solution, and needing "
                         "many checks." if summary.topics else "Check an answer to start building your topic profile.")
        explain.setObjectName("Muted")
        explain.setWordWrap(True)
        tp_page = QWidget()
        tl = QVBoxLayout(tp_page)
        tl.addWidget(explain)
        tl.addWidget(self.topic_table)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(practise)
        tl.addLayout(row)

        # ---- by category / difficulty ------------------------------------------------------------
        self.group_table = _table(["Group", "Challenges", "Attempted", "Solved", "Solved without help", "Checks", "Time"])
        self.group_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for g in summary.by_category + summary.by_difficulty:
            r = self.group_table.rowCount()
            self.group_table.insertRow(r)
            for col, text in enumerate([g.name, str(g.challenges), str(g.attempted), str(g.solved), str(g.clean_solves),
                                        str(g.attempts), fmt_time(g.seconds)]):
                self.group_table.setItem(r, col, _item(text, col > 0))

        tabs = QTabWidget()
        tabs.addTab(ch_page, "Challenges")
        tabs.addTab(tp_page, "Weak topics")
        tabs.addTab(self.group_table, "By category && difficulty")
        self.tabs = tabs

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(cards)
        layout.addWidget(tabs, 1)
        layout.addWidget(buttons)

    def _fill_challenges(self) -> None:
        wanted = {0: None, 1: SOLVED, 2: "in progress", 3: NOT_STARTED}[self.filter.currentIndex()]
        t = self.challenge_table
        t.setRowCount(0)
        for ch in self._challenges:
            st = self._statuses[ch.id]
            if wanted and st.state != wanted:
                continue
            r = t.rowCount()
            t.insertRow(r)
            color = C.success if st.state == SOLVED else C.warning if st.state != NOT_STARTED else C.text_muted
            t.setItem(r, 0, _item(f"{st.icon}  {st.label}", color=color, data=ch.id))
            t.setItem(r, 1, _item(ch.title, data=ch.id))
            t.setItem(r, 2, _item(ch.category))
            t.setItem(r, 3, _item(ch.difficulty))
            t.setItem(r, 4, _item(str(st.attempts), True))
            t.setItem(r, 5, _item(fmt_time(st.seconds_active), True))
            t.setItem(r, 6, _item((st.last_activity or ch.created_at).replace("T", " ")[:16]))

    def _open_selected(self, item: QTableWidgetItem) -> None:
        cid = self.challenge_table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        if cid:
            self.open_challenge.emit(cid)
            self.accept()

    def _practise_selected(self) -> None:
        row = self.topic_table.currentRow()
        if row < 0 or row >= len(self._topics):
            return
        category, _, topic = self._topics[row].name.partition(" · ")
        self.practise_topic.emit(category, "" if topic == "(untagged)" else topic)
        self.accept()

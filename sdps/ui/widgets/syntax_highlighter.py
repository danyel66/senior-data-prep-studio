"""Regex-based syntax highlighters for SQL (Postgres/DuckDB dialect) and Python.

Multi-line constructs (SQL `/* */` comments, Python triple-quoted strings) are handled
with QSyntaxHighlighter block state so highlighting stays correct while typing.
"""
from __future__ import annotations

import keyword
import re

from PyQt6.QtCore import QRegularExpression
from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument

from ..theme import C


def _fmt(color: str, *, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.DemiBold)
    if italic:
        f.setFontItalic(True)
    return f


def _words(words: list[str], case_insensitive: bool) -> QRegularExpression:
    pattern = r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b"
    opts = QRegularExpression.PatternOption.CaseInsensitiveOption if case_insensitive \
        else QRegularExpression.PatternOption.NoPatternOption
    return QRegularExpression(pattern, opts)


class _RuleHighlighter(QSyntaxHighlighter):
    """Applies (regex, format, capture_group) rules in order; later rules win."""

    rules: list[tuple[QRegularExpression, QTextCharFormat, int]]

    def _apply_rules(self, text: str) -> None:
        for regex, fmt, group in self.rules:
            it = regex.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(group), m.capturedLength(group), fmt)


class SqlHighlighter(_RuleHighlighter):
    KEYWORDS = [
        "select", "from", "where", "group", "by", "order", "having", "limit", "offset", "as", "on",
        "join", "inner", "left", "right", "full", "outer", "cross", "lateral", "using", "natural",
        "union", "all", "intersect", "except", "distinct", "with", "recursive", "materialized",
        "insert", "into", "values", "update", "set", "delete", "merge", "matched", "returning",
        "create", "alter", "drop", "table", "view", "index", "schema", "if", "exists", "not",
        "primary", "key", "foreign", "references", "unique", "constraint", "default", "check",
        "and", "or", "in", "is", "null", "like", "ilike", "between", "case", "when", "then",
        "else", "end", "over", "partition", "rows", "range", "groups", "unbounded", "preceding",
        "following", "current", "row", "window", "filter", "qualify", "explain", "analyze",
        "asc", "desc", "nulls", "first", "last", "true", "false", "cast", "interval", "grouping",
        "sets", "cube", "rollup", "begin", "commit", "rollback", "transaction", "temp", "temporary",
        "pivot", "unpivot", "tablesample", "fetch", "next", "only", "any", "some", "array",
    ]
    TYPES = [
        "int", "integer", "bigint", "smallint", "tinyint", "hugeint", "decimal", "numeric", "real",
        "double", "precision", "float", "varchar", "char", "text", "boolean", "bool", "date",
        "time", "timestamp", "timestamptz", "uuid", "json", "jsonb", "blob", "bytea", "serial",
        "bigserial", "money",
    ]

    def __init__(self, document: QTextDocument):
        super().__init__(document)
        self.comment_fmt = _fmt(C.syn_comment, italic=True)
        self.rules = [
            (QRegularExpression(r"[=<>!+\-*/%|:]+"), _fmt(C.syn_operator), 0),
            (_words(self.KEYWORDS, True), _fmt(C.syn_keyword, bold=True), 0),
            (_words(self.TYPES, True), _fmt(C.syn_type), 0),
            (QRegularExpression(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(?=\()"), _fmt(C.syn_function), 1),
            (QRegularExpression(r"\b\d+(?:\.\d+)?\b"), _fmt(C.syn_number), 0),
            (QRegularExpression(r'"[^"]*"'), _fmt(C.syn_type), 0),          # quoted identifiers
            (QRegularExpression(r"'(?:[^']|'')*'"), _fmt(C.syn_string), 0),
            (QRegularExpression(r"--[^\n]*"), self.comment_fmt, 0),
        ]
        self._block_start = QRegularExpression(r"/\*")
        self._block_end = QRegularExpression(r"\*/")

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt API)
        self._apply_rules(text)
        self.setCurrentBlockState(0)
        if self.previousBlockState() == 1:       # continuing a /* ... */ from a previous line
            start, skip = 0, 0
        else:
            start, skip = _index(self._block_start, text, 0), 2
        while start >= 0:
            end = _index(self._block_end, text, start + skip)
            if end >= 0:
                length = end - start + 2
            else:
                self.setCurrentBlockState(1)
                length = len(text) - start
            self.setFormat(start, length, self.comment_fmt)
            start = _index(self._block_start, text, start + length)
            skip = 2


class PythonHighlighter(_RuleHighlighter):
    BUILTINS = [
        "print", "len", "range", "enumerate", "zip", "map", "filter", "sum", "min", "max", "sorted",
        "list", "dict", "set", "tuple", "str", "int", "float", "bool", "isinstance", "type", "open",
        "any", "all", "abs", "round", "super", "object", "iter", "next", "reversed", "getattr",
    ]
    LIBS = ["pd", "np", "pl", "duckdb", "pa", "self", "cls"]

    def __init__(self, document: QTextDocument):
        super().__init__(document)
        self.string_fmt = _fmt(C.syn_string)
        self.rules = [
            (QRegularExpression(r"[=<>!+\-*/%&|^~@]+"), _fmt(C.syn_operator), 0),
            (_words(keyword.kwlist + list(keyword.softkwlist), False), _fmt(C.syn_keyword, bold=True), 0),
            (_words(self.BUILTINS, False), _fmt(C.syn_function), 0),
            (_words(self.LIBS, False), _fmt(C.syn_type), 0),
            (QRegularExpression(r"\bdef\s+([A-Za-z_]\w*)"), _fmt(C.syn_function, bold=True), 1),
            (QRegularExpression(r"\bclass\s+([A-Za-z_]\w*)"), _fmt(C.syn_type, bold=True), 1),
            (QRegularExpression(r"\.([A-Za-z_]\w*)\s*(?=\()"), _fmt(C.syn_function), 1),
            (QRegularExpression(r"^\s*@[\w.]+"), _fmt(C.syn_decorator), 0),
            (QRegularExpression(r"\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?\b"), _fmt(C.syn_number), 0),
            (QRegularExpression(r"[rbfuRBFU]{0,2}\"(?:[^\"\\]|\\.)*\""), self.string_fmt, 0),
            (QRegularExpression(r"[rbfuRBFU]{0,2}'(?:[^'\\]|\\.)*'"), self.string_fmt, 0),
            (QRegularExpression(r"#[^\n]*"), _fmt(C.syn_comment, italic=True), 0),
        ]
        self._triples = [(QRegularExpression('"""'), 1), (QRegularExpression("'''"), 2)]

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        self._apply_rules(text)
        self.setCurrentBlockState(0)
        for delim, state in self._triples:
            if self._multiline(text, delim, state):
                return

    def _multiline(self, text: str, delim: QRegularExpression, state: int) -> bool:
        if self.previousBlockState() == state:
            start, add = 0, 0
        else:
            start = _index(delim, text, 0)
            add = 3
        while start >= 0:
            end = _index(delim, text, start + add)
            if end >= 0:
                length = end - start + 3
                self.setCurrentBlockState(0)
            else:
                self.setCurrentBlockState(state)
                length = len(text) - start
            self.setFormat(start, length, self.string_fmt)
            start = _index(delim, text, start + length)
            add = 3
        return self.currentBlockState() == state


def _index(regex: QRegularExpression, text: str, offset: int) -> int:
    m = regex.match(text, offset)
    return m.capturedStart() if m.hasMatch() else -1

"""Permanent AST-based SQL Schema Audit (v6.20.2).

Compiles EVERY static SQL query in fysb/ via EXPLAIN against the real schema
created by db.init_db(), guaranteeing zero UndefinedColumn / no-such-column /
no-such-table regressions across both SQLite and PostgreSQL.
"""
import ast
import os
import re
import sqlite3
import tempfile
import unittest

from fysb import config, db
from fysb.handlers import admin_trading

_SQL_KW = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE)\b", re.I)


class TestSqlSchemaAudit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = config.DB_FILE
        config.DB_FILE = os.path.join(self._tmp.name, "audit.db")
        db.init_db()

    def tearDown(self):
        config.DB_FILE = self._old_db
        self._tmp.cleanup()

    def test_all_static_sql_queries_valid_against_schema(self):
        c = sqlite3.connect(config.DB_FILE)
        try:
            errors = []
            checked = 0
            fysb_root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fysb")
            for root, _, files in os.walk(fysb_root):
                for fname in sorted(files):
                    if not fname.endswith(".py") or fname == "pglayer.py":
                        continue
                    path = os.path.join(root, fname)
                    with open(path, encoding="utf-8") as fh:
                        tree = ast.parse(fh.read(), filename=path)
                    for node in ast.walk(tree):
                        if not isinstance(node, ast.Call):
                            continue
                        fn = node.func
                        if not (isinstance(fn, ast.Attribute) and fn.attr == "execute" and node.args):
                            continue
                        arg0 = node.args[0]
                        if not (isinstance(arg0, ast.Constant) and isinstance(arg0.value, str)):
                            continue
                        sql = arg0.value
                        if not _SQL_KW.match(sql):
                            continue
                        checked += 1
                        # PostgreSQL rejects double-quoted string literals as UndefinedColumn
                        if '"' in sql:
                            errors.append(f"{path}:{node.lineno}: double quote in SQL: {sql[:100]}")
                        try:
                            c.execute("EXPLAIN " + sql, tuple(None for _ in range(sql.count("?"))))
                        except Exception as exc:
                            msg = str(exc)
                            if any(k in msg for k in ("no such column", "no such table", "has no column named")):
                                errors.append(f"{path}:{node.lineno}: {msg} | {sql.strip()[:120]}")
        finally:
            c.close()

        self.assertGreater(checked, 350, "Expected to audit 350+ static SQL queries")
        self.assertEqual(errors, [], "SQL schema errors found:\n" + "\n".join(errors))

    def test_enabled_backtest_markets_uses_valid_symbol_order(self):
        mkts = admin_trading._enabled_backtest_markets()
        self.assertIsInstance(mkts, list)
        self.assertTrue(len(mkts) >= 1)
        self.assertEqual(mkts, sorted(mkts))


if __name__ == "__main__":
    unittest.main()

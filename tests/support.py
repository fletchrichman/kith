"""Shared harness: fake Apple data built once per test class, a fresh crm.db per test, and crm.py
run as a subprocess exactly the way Claude and install.sh run it."""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from tests.fixtures import build as fixtures

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRM = os.path.join(ROOT, "crm.py")


class CRMTestCase(unittest.TestCase):
    synced = False  # True: every test starts from a crm.db synced from all fixtures (+ LinkedIn)

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="kith test ")  # a space, like "Application Support"
        cls.fixtures = fixtures.build(os.path.join(cls.tmp, "fixtures"))
        cls.template = None
        if cls.synced:
            cls.template = os.path.join(cls.tmp, "template.db")
            cls.run_crm(cls.base_env(cls.template), "sync", "--linkedin", cls.fixtures["LINKEDIN_CSV"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def base_env(cls, db_path):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CRM_")}
        env.update({k: v for k, v in cls.fixtures.items() if k.startswith("CRM_")})
        env.update(CRM_DB=db_path, CRM_NO_AUTOSYNC="1", PYTHONIOENCODING="utf-8")
        return env

    @staticmethod
    def run_crm(env, *args, cwd=None):
        p = subprocess.run([sys.executable, CRM, *args], env=env, cwd=cwd, capture_output=True, text=True,
                           timeout=120)
        if p.returncode:
            raise AssertionError(f"crm.py {' '.join(args)} exited {p.returncode}\n{p.stdout}\n{p.stderr}")
        return p

    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=self.tmp)
        self.db_path = os.path.join(self.dir, "data dir", "crm.db")
        if self.template:
            os.makedirs(os.path.dirname(self.db_path), mode=0o700)
            shutil.copy(self.template, self.db_path)
        self.env = self.base_env(self.db_path)

    def crm(self, *args, ok=True, env=None):
        """Run crm.py; with ok=False return the CompletedProcess whatever the exit code."""
        env = {**self.env, **(env or {})}
        if ok:
            return self.run_crm(env, *args, cwd=self.dir)
        return subprocess.run([sys.executable, CRM, *args], env=env, cwd=self.dir, capture_output=True, text=True,
                              timeout=120)

    def out(self, *args, env=None):
        return self.crm(*args, env=env).stdout

    def fails(self, *args, env=None):
        p = self.crm(*args, ok=False, env=env)
        self.assertNotEqual(p.returncode, 0, f"crm.py {' '.join(args)} should fail:\n{p.stdout}")
        return p.stdout + p.stderr

    def query(self, sql, *params):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        try:
            return db.execute(sql, params).fetchall()
        finally:
            db.close()

    def scalar(self, sql, *params):
        rows = self.query(sql, *params)
        return rows[0][0] if rows else None

    def pid(self, name):
        rows = self.query("SELECT id FROM people WHERE name=? AND merged_into IS NULL", name)
        self.assertEqual(len(rows), 1, f"expected exactly one live {name!r}")
        return rows[0][0]

    def pids(self, name):
        return [r[0] for r in self.query("SELECT id FROM people WHERE name=? AND merged_into IS NULL ORDER BY id", name)]

    def copy_fixture(self, key, name):
        """A private, writable copy of one fixture database, for tests that break it."""
        dst = os.path.join(self.dir, name)
        shutil.copy(self.fixtures[key], dst)
        return dst

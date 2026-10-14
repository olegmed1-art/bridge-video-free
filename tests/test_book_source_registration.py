"""Synthetic source-registry admission; never connects to a database."""

from uuid import UUID
import unittest

from database.book_source_reconciliation import (
    _register_missing_source, new_book_source_id,
)

SCHOOL = "11111111-1111-1111-1111-111111111111"
FILE = "SyntheticDriveFileId_0123456789"
LOCATOR = "https://drive.google.com/file/d/" + FILE + "/view"
SOURCE = {"locator": LOCATOR, "title": "Synthetic lesson"}


class RegistryCursor:
    def __init__(self):
        self.sources = []
        self.school_status = "active"
        self.unknown_effect = False
        self.one = None
        self.many = None
        self.rowcount = 0
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        self.rowcount = 0
        if "pg_advisory_xact_lock" in sql:
            self.one = (None,)
        elif "pg_catalog.pg_trigger" in sql:
            self.one = (self.unknown_effect,)
        elif "SELECT status FROM public.school" in sql:
            self.one = (self.school_status,) if self.school_status else None
        elif "SELECT source_id,canonical_locator,status,source_type,title" in sql:
            school_id, public_url, alias = params
            assert school_id == SCHOOL and public_url == LOCATOR
            assert alias == "drive:" + FILE
            self.many = [row for row in self.sources if row[1] in (public_url, alias)]
        elif "INSERT INTO public.source" in sql:
            source_id, school_id, title, locator = params
            assert school_id == SCHOOL and title == SOURCE["title"]
            self.sources.append((UUID(source_id), locator, "active", "book", title))
            self.rowcount = 1
        elif "LOCK TABLE public.source" not in sql:
            raise AssertionError(sql)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class BookSourceRegistrationTests(unittest.TestCase):
    def test_new_source_is_one_row_and_exact_replay_is_zero(self):
        cursor = RegistryCursor()
        source_id = new_book_source_id(SCHOOL, LOCATOR)
        identity = {"stored_locator": LOCATOR}
        self.assertEqual(new_book_source_id(SCHOOL, LOCATOR), source_id)
        self.assertEqual(_register_missing_source(cursor, SCHOOL, source_id, SOURCE, identity), 1)
        self.assertEqual(_register_missing_source(cursor, SCHOOL, source_id, SOURCE, identity), 0)
        self.assertEqual(len(cursor.sources), 1)
        self.assertEqual(sum("INSERT INTO public.source" in sql for sql, _ in cursor.statements), 1)

    def test_source_registration_fails_closed(self):
        for condition in ("wrong_id", "wrong_locator", "duplicate_alias",
                          "conflicting_existing_id", "inactive_school", "unknown_trigger"):
            with self.subTest(condition=condition):
                cursor = RegistryCursor()
                source_id = new_book_source_id(SCHOOL, LOCATOR)
                identity = {"stored_locator": LOCATOR}
                if condition == "wrong_id":
                    source_id = "22222222-2222-2222-2222-222222222222"
                elif condition == "wrong_locator":
                    identity["stored_locator"] = "drive:" + FILE
                elif condition == "duplicate_alias":
                    cursor.sources = [
                        (UUID(source_id), LOCATOR, "active", "book", SOURCE["title"]),
                        (UUID(source_id), "drive:" + FILE, "active", "book", SOURCE["title"]),
                    ]
                elif condition == "conflicting_existing_id":
                    cursor.sources = [
                        (UUID("33333333-3333-3333-3333-333333333333"),
                         LOCATOR, "active", "book", SOURCE["title"]),
                    ]
                elif condition == "inactive_school":
                    cursor.school_status = "inactive"
                else:
                    cursor.unknown_effect = True
                with self.assertRaises(ValueError):
                    _register_missing_source(cursor, SCHOOL, source_id, SOURCE, identity)
                self.assertFalse(any("INSERT INTO public.source" in sql
                                     for sql, _ in cursor.statements))

    def test_existing_reviewed_alias_reuses_its_uuid(self):
        cursor = RegistryCursor()
        existing_id = "33333333-3333-3333-3333-333333333333"
        cursor.sources = [(UUID(existing_id), "drive:" + FILE,
                           "active", "book", SOURCE["title"])]
        self.assertEqual(_register_missing_source(cursor, SCHOOL, existing_id, SOURCE,
                         {"stored_locator": "drive:" + FILE}), 0)
        self.assertEqual(len(cursor.sources), 1)


if __name__ == "__main__":
    unittest.main()

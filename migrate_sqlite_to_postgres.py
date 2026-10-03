import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import database


TABLE_COLUMNS = {
    "users": ("id", "email", "password_hash", "is_admin", "created_at"),
    "settings": ("user_id", "study_board"),
    "skills": (
        "id", "user_id", "name", "category", "description", "risk_level", "difficulty",
        "language_code", "initial_score", "accuracy_score", "speed_score", "marking_criteria",
        "last_practiced", "created_at",
    ),
    "practices": (
        "id", "user_id", "skill_id", "scenario", "response", "evaluation_json", "readiness_score",
        "readiness_after", "submission_id", "elapsed_seconds", "created_at",
    ),
}
TIMESTAMP_COLUMNS = {
    "users": ("created_at",),
    "skills": ("last_practiced", "created_at"),
    "practices": ("created_at",),
}
TARGET_TABLES = ("users", "settings", "skills", "practices")


def parse_timestamp(value):
    if value is None or isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def read_source(source_path):
    source_uri = source_path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(source_uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def count_source_rows(source):
    counts = {}
    for table in TARGET_TABLES:
        columns = {row["name"] for row in source.execute(f"PRAGMA table_info({table})")}
        missing = set(TABLE_COLUMNS[table]) - columns
        if missing:
            raise ValueError(f"SQLite {table} table is missing expected columns; run the existing SQLite migrations first.")
        counts[table] = source.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()["total"]
    counts["sessions_not_migrated"] = source.execute("SELECT COUNT(*) AS total FROM sessions").fetchone()["total"]
    return counts


def target_row_counts(target):
    counts = {}
    for table in TARGET_TABLES:
        counts[table] = target.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()["total"]
    return counts


def iter_source_rows(source, table):
    columns = TABLE_COLUMNS[table]
    rows = source.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY {columns[0]}")
    timestamp_columns = set(TIMESTAMP_COLUMNS.get(table, ()))
    for row in rows:
        values = []
        for column in columns:
            value = row[column]
            if column in timestamp_columns:
                value = parse_timestamp(value)
            if table == "users" and column == "is_admin":
                value = bool(value)
            values.append(value)
        yield tuple(values)


def import_source(source_path, apply=False, confirm_empty_target=False):
    source_path = Path(source_path)
    if not source_path.is_file():
        raise FileNotFoundError("The SQLite source database file does not exist.")
    with closing(read_source(source_path)) as source:
        source_counts = count_source_rows(source)
        if not apply:
            return {"mode": "dry_run", "source_rows": source_counts, "sessions_imported": False}
        if not confirm_empty_target:
            raise ValueError("Import requires --confirm-empty-target in addition to --apply.")
        if database.database_backend() != "postgresql":
            raise ValueError("Set DATABASE_URL to the dedicated Neon target before applying the import.")

        database.initialize_db()
        with database.connect_db() as target:
            existing_counts = target_row_counts(target)
            if any(existing_counts.values()):
                raise ValueError("Target contains application rows; refusing to overwrite or merge them.")
            for table in TARGET_TABLES:
                columns = TABLE_COLUMNS[table]
                placeholders = ", ".join("?" for _ in columns)
                statement = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
                target.executemany(statement, iter_source_rows(source, table))

            for table in ("users", "skills", "practices"):
                target.execute(
                    "SELECT setval(pg_get_serial_sequence(?, 'id'), COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM " + table,
                    (table,),
                )
            imported_counts = target_row_counts(target)
            expected_counts = {table: source_counts[table] for table in TARGET_TABLES}
            if imported_counts != expected_counts:
                raise RuntimeError("Imported row counts do not match the SQLite source; transaction rolled back.")

        return {
            "mode": "imported",
            "source_rows": source_counts,
            "rows_imported": imported_counts,
            "sessions_imported": False,
        }


def main():
    parser = argparse.ArgumentParser(description="Safely import Masterify SQLite data into the configured PostgreSQL database.")
    parser.add_argument("--sqlite", default=str(database.DB_PATH), help="source SQLite file (defaults to the local configured SQLite path)")
    parser.add_argument("--apply", action="store_true", help="write to DATABASE_URL; without this flag the command only performs a dry run")
    parser.add_argument("--confirm-empty-target", action="store_true", help="confirm that the PostgreSQL target may receive data only if its application tables are empty")
    args = parser.parse_args()
    try:
        result = import_source(args.sqlite, apply=args.apply, confirm_empty_target=args.confirm_empty_target)
        print(json.dumps(result, indent=2))
    except Exception as error:
        print(f"Migration stopped ({type(error).__name__}). No SQLite source data was changed.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
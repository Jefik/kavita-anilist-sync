"""Per-user state. Nothing here is ever written back to Kavita."""
import json
import os
import sqlite3
import time
from pathlib import Path

SCHEMA = """
create table if not exists settings (key text primary key, value text);
create table if not exists users (
    user_id integer primary key, username text, is_admin integer default 0, api_key text,
    anilist_token text default '', dry_run integer default 1, libraries text default '[]'
);
create table if not exists mapping (
    user_id integer, series_id integer, media_id integer not null, primary key (user_id, series_id)
);
create table if not exists series (
    user_id integer, series_id integer, name text, library_id integer, media_id integer, last_read text,
    synced_volumes integer default 0, synced_chapters integer default 0, pending integer default 0,
    unlinked integer default 0, primary key (user_id, series_id)
);
create table if not exists activity (
    id integer primary key autoincrement, user_id integer, at real, series_id integer, name text, kind text, detail text
);
"""


class Store:
    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / "sync.sqlite"
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        os.chmod(path, 0o600)

    def setting(self, key, default=""):
        row = self.db.execute("select value from settings where key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key, value):
        self.db.execute("insert or replace into settings values (?, ?)", (key, value))
        self.db.commit()

    def users(self):
        return [self._user(r) for r in self.db.execute("select * from users")]

    def user(self, user_id):
        row = self.db.execute("select * from users where user_id = ?", (user_id,)).fetchone()
        return self._user(row) if row else None

    @staticmethod
    def _user(row):
        user = dict(row)
        user["libraries"] = json.loads(user["libraries"])
        user["dry_run"] = bool(user["dry_run"])
        user["is_admin"] = bool(user["is_admin"])
        return user

    def save_user(self, user_id, **fields):
        self.db.execute("insert or ignore into users (user_id) values (?)", (user_id,))
        for key, value in fields.items():
            if key == "libraries":
                value = json.dumps(value)
            self.db.execute(f"update users set {key} = ? where user_id = ?", (value, user_id))
        self.db.commit()

    def log(self, user_id, series_id, name, kind, detail=""):
        self.db.execute(
            "insert into activity (user_id, at, series_id, name, kind, detail) values (?, ?, ?, ?, ?, ?)",
            (user_id, time.time(), series_id, name, kind, detail),
        )
        self.db.execute(
            "delete from activity where user_id = ? and id not in "
            "(select id from activity where user_id = ? order by id desc limit 500)", (user_id, user_id))
        self.db.commit()

    def activity(self, user_id, limit=100):
        return [dict(r) for r in self.db.execute(
            "select at, series_id, name, kind, detail from activity where user_id = ? order by id desc limit ?", (user_id, limit))]

    def last_activity(self, user_id, series_id):
        row = self.db.execute(
            "select at, series_id, name, kind, detail from activity where user_id = ? and series_id = ? order by id desc limit 1",
            (user_id, series_id)).fetchone()
        return dict(row) if row else None

    def mapping(self, user_id, series_id):
        row = self.db.execute("select media_id from mapping where user_id = ? and series_id = ?", (user_id, series_id)).fetchone()
        return row["media_id"] if row else None

    def set_mapping(self, user_id, series_id, media_id):
        if media_id is None:
            self.db.execute("delete from mapping where user_id = ? and series_id = ?", (user_id, series_id))
        else:
            self.db.execute("insert or replace into mapping values (?, ?, ?)", (user_id, series_id, media_id))
        self.db.execute("update series set synced_volumes = 0, synced_chapters = 0 where user_id = ? and series_id = ?", (user_id, series_id))
        self.db.commit()

    def series(self, user_id, series_id):
        row = self.db.execute("select * from series where user_id = ? and series_id = ?", (user_id, series_id)).fetchone()
        return dict(row) if row else None

    def touch_series(self, user_id, series_id, **fields):
        self.db.execute("insert or ignore into series (user_id, series_id) values (?, ?)", (user_id, series_id))
        for key, value in fields.items():
            self.db.execute(f"update series set {key} = ? where user_id = ? and series_id = ?", (value, user_id, series_id))
        self.db.commit()

    def has_baseline(self, user_id):
        return self.db.execute("select 1 from series where user_id = ? and last_read is not null limit 1", (user_id,)).fetchone() is not None

    def pending(self, user_id):
        return [r["series_id"] for r in self.db.execute("select series_id from series where user_id = ? and pending = 1", (user_id,))]

    def unmapped(self, user_id, libraries):
        marks = ",".join("?" * len(libraries)) or "null"
        return [dict(r) for r in self.db.execute(
            f"select series_id, name, library_id from series where user_id = ? and unlinked = 1 "
            f"and library_id in ({marks}) order by name", [user_id, *libraries])]

    def import_legacy(self, user_id):
        """Bring in series, mappings and activity from the single-user version."""
        old = self.dir / "state.sqlite"
        if not old.exists():
            return
        self.db.execute("attach database ? as old", (str(old),))
        self.db.execute("insert or ignore into mapping select ?, series_id, media_id from old.mapping", (user_id,))
        self.db.execute(
            "insert or ignore into series select ?, series_id, name, library_id, media_id, last_read, "
            "synced_volumes, synced_chapters, pending, unlinked from old.series", (user_id,))
        self.db.execute(
            "insert into activity (user_id, at, series_id, name, kind, detail) "
            "select ?, at, series_id, name, kind, detail from old.activity order by id", (user_id,))
        self.db.commit()
        self.db.execute("detach database old")

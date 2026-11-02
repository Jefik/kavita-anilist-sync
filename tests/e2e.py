"""End-to-end run against a real Kavita and tests/mock_anilist.py.

KAVITA_URL, ADMIN_USER, ADMIN_PASSWORD, SECOND_USER, SECOND_PASSWORD and WORK_DIR come from the
environment; SECOND_USER is not an admin. KAVITA_RESTART optionally restarts Kavita.
The Kavita instance must be disposable: reading progress is changed.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

KAVITA = os.environ["KAVITA_URL"]
WORK = os.environ["WORK_DIR"]
DATA = f"{WORK}/app-data"
RESTART_CMD = os.environ.get("KAVITA_RESTART")
APP_PORT, MOCK_PORT = 18091, 18092
APP = f"http://127.0.0.1:{APP_PORT}"
MOCK = f"http://127.0.0.1:{MOCK_PORT}"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []


def check(name, passed, detail=""):
    print(("PASS " if passed else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)
    if not passed:
        failures.append(name)


def http(method, url, body=None, headers=None, raw=False):
    request = urllib.request.Request(url, method=method, headers={"Content-Type": "application/json", **(headers or {})},
                                     data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text, status = response.read().decode(), response.status
    except urllib.error.HTTPError as error:
        text, status = error.read().decode(), error.code
    if raw:
        return status, text
    try:
        return json.loads(text)
    except ValueError:
        return text


def wait(predicate, seconds=10, step=0.2):
    deadline = time.time() + seconds
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return None


class Person:
    def __init__(self, username, password):
        self.username, self.password = username, password
        self.login()

    def login(self):
        self.user = http("POST", f"{KAVITA}/api/account/login", {"username": self.username, "password": self.password})
        self.token = self.user["token"]

    def app(self, method, path, body=None):
        return http(method, f"{APP}/api/{path}", body, {"Authorization": "Bearer " + self.token, "X-Requested-With": "kas"})

    def app_status(self, method, path, body=None):
        return http(method, f"{APP}/api/{path}", body, {"Authorization": "Bearer " + self.token, "X-Requested-With": "kas"}, raw=True)[0]

    def kavita(self, method, path, body=None):
        return http(method, f"{KAVITA}/api/{path}", body, {"Authorization": "Bearer " + self.token})

    def volume_chapter(self, item, index):
        volumes = sorted(self.kavita("GET", f"series/volumes?seriesId={item['id']}"), key=lambda v: v["minNumber"])
        return volumes[index], volumes[index]["chapters"][0]

    def read(self, item, index, pages=None):
        volume, chapter = self.volume_chapter(item, index)
        self.kavita("POST", "reader/progress", {"volumeId": volume["id"], "chapterId": chapter["id"], "seriesId": item["id"],
                                                "libraryId": item["libraryId"], "pageNum": chapter["pages"] - 1 if pages is None else pages})
        return time.time()


admin = Person(os.environ["ADMIN_USER"], os.environ["ADMIN_PASSWORD"])
second = Person(os.environ["SECOND_USER"], os.environ["SECOND_PASSWORD"])
libraries = admin.kavita("GET", "library/libraries")
library = libraries[0]["id"]
series = {s["name"]: s for s in admin.kavita("POST", "series/v2?pageNumber=1&pageSize=50", {"statements": [], "combination": 1, "limitTo": 0})}
shark, liverleaf, manhole = series["サメガール"], series["ミスミソウ"], series["マンホール 新装版"]


def set_link(item, link):
    metadata = admin.kavita("GET", f"series/metadata?seriesId={item['id']}")
    metadata["webLinks"] = link
    admin.kavita("POST", "series/metadata", {"seriesMetadata": metadata})


def saves(token=None):
    return [s for s in http("GET", MOCK + "/__state")["saves"] if not token or s["token"] == token]


def reset_mock(**extra):
    http("POST", MOCK + "/__state", {"entries": {}, "saves": [], "fail": [], **extra})


def start_app():
    env = {**os.environ, "DATA_DIR": DATA, "ANILIST_URL": MOCK + "/", "PORT": str(APP_PORT), "HOST": "127.0.0.1",
           "PYTHONPATH": HERE, "KAVITA_URL": KAVITA, "RETRY_SECONDS": "5"}
    process = subprocess.Popen([sys.executable, "-m", "kavita_anilist_sync"], env=env, stdout=log, stderr=log)
    wait(lambda: _alive(), 15)
    return process


def _alive():
    try:
        return http("GET", APP + "/panel.js", raw=True)[0] == 200
    except OSError:
        return None


def activity(person, kind):
    return [a for a in person.app("GET", "activity") if a["kind"] == kind]


shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA)
log = open(f"{WORK}/app.log", "w")
mock = subprocess.Popen([sys.executable, f"{HERE}/tests/mock_anilist.py", str(MOCK_PORT)], stdout=log, stderr=log)
time.sleep(1)
for person in (admin, second):
    for item in (shark, liverleaf, manhole):
        person.kavita("POST", "reader/mark-unread", {"seriesId": item["id"]})
set_link(shark, "https://anilist.co/manga/108955")
set_link(liverleaf, "https://anilist.co/manga/75721")
set_link(manhole, "")
before = {i["id"]: admin.kavita("GET", f"series/metadata?seriesId={i['id']}") for i in (shark, liverleaf)}

# Legacy single-user data becomes the owner's account
with open(f"{DATA}/config.json", "w") as file:
    json.dump({"kavita_url": KAVITA, "kavita_api_key": admin.user["apiKey"], "anilist_client_id": "12345", "anilist_token": "token-a",
               "libraries": [library], "dry_run": True, "reconcile_hours": 6}, file)
old = sqlite3.connect(f"{DATA}/state.sqlite")
old.executescript("""
create table mapping (series_id integer primary key, media_id integer not null);
create table series (series_id integer primary key, name text, library_id integer, media_id integer, last_read text,
    synced_volumes integer default 0, synced_chapters integer default 0, pending integer default 0, unlinked integer default 0);
create table activity (id integer primary key autoincrement, at real, series_id integer, name text, kind text, detail text);
insert into activity (at, series_id, name, kind, detail) values (1, null, '', 'connected', 'old row');
""")
old.commit()
old.close()
process = start_app()

try:
    # Access
    me = wait(lambda: admin.app("GET", "me"), 5)
    check("old setup moved to its owner", me["anilist_user"] == "anilist-a" and me["dry_run"] and me["libraries"] == [library] and me["client_id"] == "12345", json.dumps(me)[:120])
    check("old activity kept", any(a["detail"] == "old row" for a in admin.app("GET", "activity")))
    check("old secrets file removed", not os.path.exists(f"{DATA}/config.json"))
    check("database is private", oct(os.stat(f"{DATA}/sync.sqlite").st_mode & 0o777) == "0o600")

    for path in ("me", "activity", "libraries", "series", "unmapped"):
        status, _ = http("GET", f"{APP}/api/{path}", raw=True)
        check(f"no session refused: {path}", status == 401)
    status, _ = http("GET", f"{APP}/api/me", headers={"Authorization": "Bearer not.a.token"}, raw=True)
    check("invalid session refused", status == 401)
    forged = "x." + __import__("base64").urlsafe_b64encode(json.dumps({"nameid": "1", "name": "x", "role": ["Admin"]}).encode()).decode().rstrip("=") + ".y"
    status, _ = http("GET", f"{APP}/api/me", headers={"Authorization": "Bearer " + forged}, raw=True)
    check("forged admin session refused", status == 401)
    status, _ = http("GET", f"{APP}/api/me", headers={"Authorization": "Bearer " + admin.token, "Host": "evil.example"}, raw=True)
    check("foreign Host refused", status == 403)
    status, _ = http("PUT", f"{APP}/api/me", {"dry_run": False}, {"Authorization": "Bearer " + admin.token}, raw=True)
    check("write without the app header refused", status == 403)
    status, _ = http("PUT", f"{APP}/api/me", {"dry_run": False}, {"Authorization": "Bearer " + admin.token, "X-Requested-With": "kas", "Origin": "https://evil.example"}, raw=True)
    check("cross-site write refused", status == 403)
    check("panel script is public", http("GET", APP + "/panel.js", raw=True)[0] == 200)

    # Second user starts empty and separate
    state = second.app("GET", "me")
    check("second user starts disconnected", not state["anilist_connected"] and state["dry_run"] and state["libraries"] == [] and not state["is_admin"])
    check("second user sees no activity of the first", second.app("GET", "activity") == [])
    check("non-admin cannot change the client ID", second.app_status("PUT", "admin", {"client_id": "1"}) == 403)
    check("admin can change the client ID", admin.app_status("PUT", "admin", {"client_id": "12345"}) == 200)
    status = admin.app_status("PUT", "admin", {"client_id": "secretlooking"})
    check("secret in the client ID field refused", status == 400)
    served = json.dumps(admin.app("GET", "me")) + json.dumps(second.app("GET", "me"))
    check("secrets never returned", "token-a" not in served and admin.user["apiKey"] not in served)
    second.app("PUT", "me", {"anilist_token": "wrong-token"})
    check("bad AniList token is reported", second.app("GET", "me")["anilist_error"] != "")
    second.app("PUT", "me", {"anilist_token": "token-b", "libraries": [library], "dry_run": False})
    check("second user connects their own account", second.app("GET", "me")["anilist_user"] == "anilist-b")
    check("first user is unchanged", admin.app("GET", "me")["anilist_user"] == "anilist-a" and admin.app("GET", "me")["dry_run"])

    check("live through the admin connection", wait(lambda: admin.app("GET", "me")["realtime"], 15))
    check("baseline recorded for each user", wait(lambda: activity(second, "baseline"), 15) and wait(lambda: activity(admin, "baseline"), 15))
    check("baseline sends nothing", not saves())

    # A dry-run admin and a live non-admin read at once
    admin.read(liverleaf, 0)
    dry = wait(lambda: activity(admin, "dry-run"), 6)
    check("dry run records the update", bool(dry), dry[0]["detail"] if dry else "")
    check("dry run sends nothing", not saves())
    check("the other user's read is not in their activity", not activity(second, "dry-run") and not activity(second, "updated"))

    calls = http("GET", MOCK + "/__state")["calls"]
    second.read(liverleaf, 1, 3)
    time.sleep(4)
    check("page turns send nothing", not saves())
    check("page turns do not query AniList", http("GET", MOCK + "/__state")["calls"] == calls)
    started = second.read(liverleaf, 1)
    saved = wait(lambda: saves("token-b"), 6, 0.05)
    delay = saved[0]["at"] - started if saved else -1
    check("non-admin's finished volume is sent at once", bool(saved) and delay < 3, f"{delay:.2f}s")
    check("highest finished volume sent", bool(saved) and saved[0]["progressVolumes"] == 2, json.dumps(saved[0] if saved else {}))
    check("sent with that user's token only", not saves("token-a"))
    check("activity stays with that user", activity(second, "updated") and not activity(admin, "updated"))

    # Each user's reading is read separately
    admin.app("PUT", "me", {"dry_run": False})
    second.kavita("POST", "reader/mark-unread", {"seriesId": shark["id"]})
    admin.read(shark, 0)
    done = wait(lambda: saves("token-a"), 6)
    check("first user's update uses their token and progress", bool(done) and done[0]["mediaId"] == 108955 and done[0].get("status") == "COMPLETED", json.dumps(done[0] if done else {}))
    check("second user's list stays untouched", not [s for s in saves("token-b") if s["mediaId"] == 108955])

    # Links are per user
    second.read(manhole, 0)
    missing = wait(lambda: [u for u in second.app("GET", "unmapped") if u["series_id"] == manhole["id"]], 6)
    check("series without a link is listed", bool(missing))
    check("the other user's list is not affected", not [u for u in admin.app("GET", "unmapped") if u["series_id"] == manhole["id"]])
    second.app("PUT", f"mapping/{manhole['id']}", {"media": 31415})
    check("link sends progress", wait(lambda: [s for s in saves("token-b") if s["mediaId"] == 31415], 6))
    detail = second.app("GET", f"series/{manhole['id']}")
    check("series page shows both sides", detail["manual_link"] and detail["volumes_read"] == 1 and detail["media"]["id"] == 31415, json.dumps({k: detail[k] for k in ("volumes_read", "manual_link")}))
    check("link is not shared", admin.app("GET", f"series/{manhole['id']}")["media"] is None)
    second.app("PUT", f"series/{manhole['id']}/volumes", {"volumes": 9})
    entry = second.app("GET", f"series/{manhole['id']}")["media"]["mediaListEntry"]
    check("manual edit sets AniList volumes", entry["progressVolumes"] == 9)
    second.app("PUT", f"series/{manhole['id']}/volumes", {"volumes": 1})
    entry = second.app("GET", f"series/{manhole['id']}")["media"]["mediaListEntry"]
    check("manual edit can lower it", entry["progressVolumes"] == 1)
    check("bad volume value refused", second.app_status("PUT", f"series/{manhole['id']}/volumes", {"volumes": -1}) == 400)
    second.app("DELETE", f"mapping/{manhole['id']}")
    check("link can be removed", second.app("GET", f"series/{manhole['id']}")["media"] is None)

    # Rate limit and retry
    reset_mock(fail=[429])
    second.app("PUT", f"mapping/{liverleaf['id']}", {"media": 75721})
    limited = wait(lambda: saves("token-b"), 12)
    check("rate limit waits and retries", bool(limited))
    reset_mock(fail=[500])
    second.app("PUT", f"mapping/{liverleaf['id']}", {"media": 75721})
    failed = wait(lambda: second.app("GET", "me")["pending"] == 1, 6)
    check("failed update is retried", bool(failed) and wait(lambda: saves("token-b"), 15))

    # One series during dry run
    admin.app("PUT", "me", {"dry_run": True})
    reset_mock()
    preview = admin.app("POST", f"series/{liverleaf['id']}/sync", {"apply": False})
    check("one series preview sends nothing", preview["kind"] == "dry-run" and not saves(), preview["detail"])
    sent = admin.app("POST", f"series/{liverleaf['id']}/sync", {"apply": True})
    check("one series send works during dry run", sent["kind"] == "updated" and len(saves()) == 1, sent["detail"])
    again = admin.app("POST", f"series/{liverleaf['id']}/sync", {"apply": True})
    check("sending again changes nothing", again["kind"] == "skipped" and len(saves()) == 1, again["detail"])
    admin.app("PUT", "me", {"dry_run": False})

    # Downtime
    process.terminate(); process.wait()
    reset_mock()
    second.read(liverleaf, 2)
    process = start_app()
    wait(lambda: admin.app("GET", "me")["realtime"], 15)
    check("reads during downtime are caught up", wait(lambda: saves("token-b"), 20))

    # Kavita restart
    if RESTART_CMD:
        subprocess.run(RESTART_CMD, shell=True, check=True, stdout=subprocess.DEVNULL)
        time.sleep(3)
        def state_after_restart():
            try:
                admin.login(); second.login()
                return admin.app("GET", "me")["realtime"]
            except (OSError, KeyError, TypeError):
                return None
        check("reconnects after Kavita returns", wait(state_after_restart, 150, 3))
        reset_mock()
        second.kavita("POST", "reader/mark-unread", {"seriesId": liverleaf["id"]})
        time.sleep(1)
        second.app("PUT", f"mapping/{liverleaf['id']}", {"media": 75721})
        second.read(liverleaf, 0)
        check("still live after reconnect", wait(lambda: saves("token-b"), 8))

    # Past reading
    reset_mock()
    second.app("POST", "backfill", {"apply": False})
    time.sleep(4)
    check("past reading preview sends nothing", not saves() and not second.app("GET", "me")["backfill_running"])
    second.app("POST", "backfill", {"apply": True})
    check("past reading sends", wait(lambda: saves("token-b"), 15))
    check("past reading stays with that user", not saves("token-a"))

    # Disconnect
    second.app("PUT", "me", {"anilist_token": ""})
    check("disconnect removes the account", not second.app("GET", "me")["anilist_connected"])

    # Kavita untouched
    after = {i: admin.kavita("GET", f"series/metadata?seriesId={i}") for i in before}
    check("Kavita metadata unchanged", after == before)
    check("no errors in the log", "Traceback" not in open(f"{WORK}/app.log").read())
finally:
    process.terminate()
    mock.terminate()
    log.close()

print("\nFAILED: " + ", ".join(failures) if failures else "\nALL PASSED")
sys.exit(1 if failures else 0)

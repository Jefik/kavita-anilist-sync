"""One sync per Kavita user, fed by a single admin connection to Kavita's live events."""
import asyncio
import json
import logging
import os
import time

import aiohttp

from .anilist import AniList, AniListError, AuthError
from .kavita import Kavita, KavitaError, anilist_id, user_id_from_token
from .progress import Progress, plan_update, reached_end, read_progress

log = logging.getLogger("kavita-anilist-sync")
BURST_SECONDS = 1.5
RETRY_SECONDS = int(os.environ.get("RETRY_SECONDS", 60))
RECONCILE_SECONDS = 6 * 3600
NETWORK = (aiohttp.ClientError, asyncio.TimeoutError, OSError)


class UserSync:
    def __init__(self, engine, user_id):
        self.engine = engine
        self.store = engine.store
        self.id = user_id
        user = self.user
        self.kavita = Kavita(engine.session, engine.kavita_url, user["api_key"])
        self.anilist = AniList(engine.session, lambda: self.user["anilist_token"])
        self.timers = {}
        self.chapter_pages = {}
        self.locks = {}
        self.reconcile_lock = asyncio.Lock()
        self.viewer = None
        self.anilist_error = ""
        self.backfill_running = False
        self.last_reconcile = 0

    @property
    def user(self):
        return self.store.user(self.id)

    def log(self, series_id, name, kind, detail=""):
        self.store.log(self.id, series_id, name, kind, detail)

    def stop(self):
        for task in self.timers.values():
            task.cancel()
        self.timers = {}

    async def check_anilist(self):
        self.viewer, self.anilist_error = None, ""
        if not self.user["anilist_token"]:
            return
        try:
            self.viewer = await self.anilist.viewer()
        except (AniListError, *NETWORK) as error:
            self.anilist_error = str(error) or type(error).__name__

    async def guarded(self, coroutine):
        try:
            await coroutine
        except (KavitaError, AniListError, *NETWORK) as error:
            log.warning("Background sync for user %s: %s", self.id, error)

    async def on_progress(self, body):
        series_id, chapter_id, pages_read = body.get("seriesId"), body.get("chapterId"), body.get("pagesRead", 0)
        if not series_id or not pages_read or not self.user["libraries"]:
            return
        if not chapter_id:
            self._schedule(series_id)
            return
        if chapter_id not in self.chapter_pages:
            self._remember(await self.kavita.volumes(series_id))
        if reached_end(pages_read, self.chapter_pages.get(chapter_id, 0)):
            self._schedule(series_id)

    def _remember(self, volumes):
        for volume in volumes:
            for chapter in volume.get("chapters", []):
                self.chapter_pages[chapter["id"]] = chapter.get("pages", 0)

    def _schedule(self, series_id):
        timer = self.timers.get(series_id)
        if timer:
            timer.cancel()
        self.timers[series_id] = asyncio.create_task(self._after_burst(series_id))

    async def _after_burst(self, series_id):
        await asyncio.sleep(BURST_SECONDS)
        self.timers.pop(series_id, None)
        await self.guarded(self.sync_series(series_id, "live"))

    async def reconcile(self):
        """Catch reads missed while disconnected. A library's first pass only records a baseline."""
        async with self.reconcile_lock:
            self.last_reconcile = time.monotonic()
            baseline = not self.store.has_baseline(self.id)
            for library_id in self.user["libraries"]:
                for series in await self.kavita.library_series(library_id):
                    series_id, last_read = series["id"], series.get("latestReadDate") or ""
                    known = self.store.series(self.id, series_id) or {}
                    self.store.touch_series(self.id, series_id, name=series["name"], library_id=library_id, last_read=last_read)
                    seen_before = known.get("last_read") is not None
                    if not baseline and seen_before and series.get("pagesRead") and known["last_read"] != last_read:
                        await self.sync_series(series_id, "catch-up")
            if baseline and self.store.has_baseline(self.id):
                self.log(None, "", "baseline", "Recorded current progress; older reads are only sent by Past reading")

    async def backfill(self, apply):
        if self.backfill_running:
            return
        self.backfill_running = True
        try:
            for library_id in self.user["libraries"]:
                for series in await self.kavita.library_series(library_id):
                    self.store.touch_series(self.id, series["id"], name=series["name"], library_id=library_id,
                                            last_read=series.get("latestReadDate") or "")
                    if series.get("pagesRead"):
                        await self.sync_series(series["id"], "past reading", dry_run=not apply, force=True)
        finally:
            self.backfill_running = False

    async def read_series(self):
        found = []
        for library_id in self.user["libraries"]:
            for series in await self.kavita.library_series(library_id):
                if series.get("pagesRead"):
                    found.append({"id": series["id"], "name": series["name"], "pages": series.get("pages", 0),
                                  "pages_read": series["pagesRead"], "last_read": series.get("latestReadDate") or ""})
        return sorted(found, key=lambda s: s["last_read"], reverse=True)

    async def media_id_for(self, series_id):
        return self.store.mapping(self.id, series_id) or anilist_id((await self.kavita.metadata(series_id)).get("webLinks"))

    async def series_detail(self, series_id):
        series = await self.kavita.series(series_id)
        volumes = await self.kavita.volumes(series_id)
        progress = read_progress(volumes, series["name"])
        media_id = await self.media_id_for(series_id)
        detail = {
            "id": series_id, "name": series["name"], "library_id": series["libraryId"],
            "selected": series["libraryId"] in self.user["libraries"],
            "volumes_read": progress.volumes, "manual_link": bool(self.store.mapping(self.id, series_id)),
            "media": await self.anilist.media(media_id) if media_id else None,
        }
        return detail

    async def set_volumes(self, series_id, volumes):
        """An explicit edit from the series page, so unlike automatic updates it may lower progress."""
        series = await self.kavita.series(series_id)
        media_id = await self.media_id_for(series_id)
        if not media_id:
            raise KavitaError("This series has no AniList link")
        await self.anilist.save({"mediaId": media_id, "progressVolumes": volumes})
        self.log(series_id, series["name"], "updated", f"manual edit: volumes set to {volumes}")

    def _lock(self, series_id):
        return self.locks.setdefault(series_id, asyncio.Lock())

    async def sync_series(self, series_id, reason, dry_run=None, force=False):
        async with self._lock(series_id):
            await self._sync(series_id, reason, self.user["dry_run"] if dry_run is None else dry_run, force)

    async def _sync(self, series_id, reason, dry_run, force):
        series = await self.kavita.series(series_id)
        name, library_id = series["name"], series["libraryId"]
        self.store.touch_series(self.id, series_id, name=name, library_id=library_id)
        if library_id not in self.user["libraries"]:
            self.store.touch_series(self.id, series_id, pending=0)
            return

        media_id = await self.media_id_for(series_id)
        self.store.touch_series(self.id, series_id, media_id=media_id, unlinked=0 if media_id else 1)
        if not media_id:
            self.store.touch_series(self.id, series_id, pending=0)
            self.log(series_id, name, "unmapped", "No AniList link")
            return

        volumes = await self.kavita.volumes(series_id)
        self._remember(volumes)
        progress = read_progress(volumes, name)
        known = self.store.series(self.id, series_id)
        if not progress.volumes and not progress.chapters:
            self.store.touch_series(self.id, series_id, pending=0)
            if force:
                self.log(series_id, name, "skipped", f"{reason}: no finished volume yet")
            return
        if not force and progress == Progress(known["synced_volumes"], known["synced_chapters"]):
            self.store.touch_series(self.id, series_id, pending=0)
            return

        try:
            media = await self.anilist.media(media_id)
            plan = plan_update(progress, media)
            if plan is None:
                entry = media.get("mediaListEntry") or {}
                self.log(series_id, name, "skipped", f"{reason}: AniList is {entry.get('status')} "
                         f"at vol {entry.get('progressVolumes') or 0}; Kavita has vol {progress.volumes}")
            elif dry_run:
                self.log(series_id, name, "dry-run", f"{reason}: would send {json.dumps(plan)}")
                self.store.touch_series(self.id, series_id, pending=0)
                return
            else:
                await self.anilist.save(plan)
                self.log(series_id, name, "updated", f"{reason}: {json.dumps(plan)}")
            self.store.touch_series(self.id, series_id, pending=0, synced_volumes=progress.volumes, synced_chapters=progress.chapters)
            self.anilist_error = ""
        except AuthError as error:
            self.anilist_error = str(error)
            self.store.touch_series(self.id, series_id, pending=1)
            self.log(series_id, name, "error", str(error))
        except (AniListError, *NETWORK) as error:
            self.anilist_error = str(error) or type(error).__name__
            self.store.touch_series(self.id, series_id, pending=1)
            self.log(series_id, name, "error", f"{reason}: {self.anilist_error}; will retry")


class Engine:
    def __init__(self, store, kavita_url):
        self.store = store
        self.kavita_url = kavita_url.rstrip("/")
        self.session = None
        self.syncs = {}
        self.tasks = []
        self.realtime = False
        self.stream_error = ""

    async def start(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
        await self._migrate()
        for user in self.store.users():
            sync = self.syncs[user["user_id"]] = UserSync(self, user["user_id"])
            asyncio.create_task(sync.check_anilist())
        self.tasks = [asyncio.create_task(self._listen()), asyncio.create_task(self._periodic())]

    async def stop(self):
        for sync in self.syncs.values():
            sync.stop()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.session.close()

    async def _migrate(self):
        """Adopt the single-user config.json as its owner's account."""
        path = self.store.dir / "config.json"
        if not path.exists():
            return
        config = json.loads(path.read_text())
        kavita = Kavita(self.session, self.kavita_url, config["kavita_api_key"])
        await kavita.login()
        user_id = user_id_from_token(kavita.token)
        self.store.save_user(
            user_id, username=kavita.username, is_admin=1, api_key=config["kavita_api_key"],
            anilist_token=config.get("anilist_token", ""), dry_run=int(config.get("dry_run", True)), libraries=config.get("libraries", []),
        )
        self.store.import_legacy(user_id)
        if config.get("anilist_client_id"):
            self.store.set_setting("anilist_client_id", config["anilist_client_id"])
        path.unlink()
        log.info("Migrated the single-user configuration to %s", kavita.username)

    async def sync_for(self, user_id, username, is_admin, session_token):
        """Called after Kavita validated the session token."""
        known = self.store.user(user_id)
        if not known or not known["api_key"]:
            api_key = await Kavita(self.session, self.kavita_url, "").auth_key(session_token)
            self.store.save_user(user_id, username=username, is_admin=int(is_admin), api_key=api_key)
        elif known["username"] != username or known["is_admin"] != is_admin:
            self.store.save_user(user_id, username=username, is_admin=int(is_admin))
        if user_id not in self.syncs:
            self.syncs[user_id] = UserSync(self, user_id)
        return self.syncs[user_id]

    def _stream_owner(self):
        for user in self.store.users():
            if user["is_admin"] and user["api_key"] and user["user_id"] in self.syncs:
                return self.syncs[user["user_id"]]
        return None

    async def _listen(self):
        """Kavita sends progress events for every user to its connected admins."""
        self.stream_delay = 2
        while True:
            owner = self._stream_owner()
            if owner is None:
                self.stream_error = "Waiting for a Kavita admin to open this panel"
                await asyncio.sleep(5)
                continue
            try:
                await owner.kavita.stream(self._on_event, self._on_connected)
                self.stream_error = "Stream closed"
            except asyncio.CancelledError:
                raise
            except (KavitaError, *NETWORK, KeyError, ValueError) as error:
                self.stream_error = str(error) or type(error).__name__
                log.warning("Kavita stream: %s", self.stream_error)
                owner.kavita.token = None
            self.realtime = False
            await asyncio.sleep(self.stream_delay)
            self.stream_delay = min(self.stream_delay * 2, 300)

    async def _on_connected(self):
        self.realtime, self.stream_error, self.stream_delay = True, "", 2
        for sync in self.syncs.values():
            asyncio.create_task(sync.guarded(sync.reconcile()))

    async def _on_event(self, body):
        sync = self.syncs.get(body.get("userId"))
        if sync:
            await sync.guarded(sync.on_progress(body))

    async def _periodic(self):
        while True:
            await asyncio.sleep(RETRY_SECONDS)
            for sync in list(self.syncs.values()):
                for series_id in self.store.pending(sync.id):
                    await sync.guarded(sync.sync_series(series_id, "retry"))
                if time.monotonic() - sync.last_reconcile >= RECONCILE_SECONDS:
                    await sync.guarded(sync.reconcile())

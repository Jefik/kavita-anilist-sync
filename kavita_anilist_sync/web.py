"""JSON API, authenticated by the caller's own Kavita session."""
import asyncio
import hashlib
import os
import re
import time
from pathlib import Path

import aiohttp
from aiohttp import web

from .anilist import AniListError
from .kavita import Kavita, KavitaError, token_claims

STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
FAILURES = (KavitaError, AniListError, aiohttp.ClientError, asyncio.TimeoutError, OSError, KeyError, ValueError)
SESSION_CACHE_SECONDS = 60


def _hosts():
    extra = os.environ.get("ALLOWED_HOSTS", "")
    return LOCAL_HOSTS | {h.strip().lower() for h in extra.split(",") if h.strip()}


def _host_only(value):
    value = (value or "").lower()
    return value.split("]")[0] + "]" if value.startswith("[") else value.split(":")[0]


def create_app(engine):
    routes = web.RouteTableDef()
    store = engine.store
    sessions = {}

    def bad(message, status=400):
        return web.json_response({"error": message}, status=status)

    async def identify(token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        cached = sessions.get(digest)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        if not await Kavita(engine.session, engine.kavita_url, "").session_valid(token):
            sessions.pop(digest, None)
            return None
        claims = token_claims(token)
        roles = claims.get("role") or []
        roles = [roles] if isinstance(roles, str) else roles
        identity = (int(claims["nameid"]), claims.get("name") or claims.get("unique_name") or "", "Admin" in roles)
        sessions[digest] = (time.monotonic() + SESSION_CACHE_SECONDS, identity)
        for key in [k for k, v in sessions.items() if v[0] <= time.monotonic()]:
            del sessions[key]
        return identity

    @web.middleware
    async def guard(request, handler):
        if _host_only(request.host) not in _hosts():
            return bad("Host not allowed", 403)
        if request.path.startswith("/api/"):
            if request.method != "GET":
                origin = request.headers.get("Origin")
                if request.headers.get("X-Requested-With") != "kas" or (origin and _host_only(origin.split("://")[-1]) != _host_only(request.host)):
                    return bad("Cross-site request refused", 403)
            header = request.headers.get("Authorization", "")
            token = header[7:] if header.startswith("Bearer ") else ""
            if not token:
                return bad("Log in to Kavita", 401)
            try:
                identity = await identify(token)
            except (*FAILURES, IndexError, TypeError):
                return bad("Could not check the Kavita session", 401)
            if not identity:
                return bad("Log in to Kavita", 401)
            try:
                request["sync"] = await engine.sync_for(identity[0], identity[1], identity[2], token)
            except FAILURES as error:
                return bad(str(error) or type(error).__name__, 502)
            request["is_admin"] = identity[2]
        response = await handler(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-cache"
        return response

    def me_payload(sync, is_admin):
        user = sync.user
        return {
            "username": user["username"], "is_admin": is_admin, "realtime": engine.realtime, "stream_error": engine.stream_error,
            "anilist_connected": bool(user["anilist_token"]), "anilist_user": sync.viewer["name"] if sync.viewer else None,
            "anilist_error": sync.anilist_error, "dry_run": user["dry_run"], "libraries": user["libraries"],
            "client_id": store.setting("anilist_client_id"), "backfill_running": sync.backfill_running,
            "pending": len(store.pending(sync.id)),
        }

    @routes.get("/panel.js")
    async def panel(_):
        return web.FileResponse(STATIC / "panel.js")

    @routes.get("/callback")
    async def callback(_):
        return web.FileResponse(STATIC / "callback.html")

    @routes.get("/callback.js")
    async def callback_script(_):
        return web.FileResponse(STATIC / "callback.js")

    @routes.get("/api/me")
    async def me(request):
        sync = request["sync"]
        if sync.user["anilist_token"] and not sync.viewer and not sync.anilist_error:
            await sync.check_anilist()
        return web.json_response(me_payload(sync, request["is_admin"]))

    @routes.put("/api/me")
    async def put_me(request):
        sync, body = request["sync"], await request.json()
        changes = {}
        if "dry_run" in body:
            changes["dry_run"] = int(bool(body["dry_run"]))
        if "libraries" in body:
            changes["libraries"] = [int(i) for i in body["libraries"]]
        if "anilist_token" in body:
            changes["anilist_token"] = str(body["anilist_token"]).strip()
        store.save_user(sync.id, **changes)
        if "anilist_token" in changes:
            await sync.check_anilist()
        if "libraries" in changes:
            asyncio.create_task(sync.guarded(sync.reconcile()))
        return web.json_response(me_payload(sync, request["is_admin"]))

    @routes.put("/api/admin")
    async def put_admin(request):
        if not request["is_admin"]:
            return bad("Only a Kavita admin can change this", 403)
        client_id = str((await request.json()).get("client_id", "")).strip()
        if client_id and not client_id.isdigit():
            return bad("The AniList client ID is a number. Do not use the client secret.")
        store.set_setting("anilist_client_id", client_id)
        return web.json_response({"client_id": client_id})

    @routes.get("/api/libraries")
    async def libraries(request):
        try:
            found = await request["sync"].kavita.libraries()
        except FAILURES as error:
            return bad(str(error) or type(error).__name__, 502)
        return web.json_response([{"id": l["id"], "name": l["name"], "type": l["type"]} for l in found])

    @routes.get("/api/activity")
    async def activity(request):
        return web.json_response(store.activity(request["sync"].id))

    @routes.get("/api/unmapped")
    async def unmapped(request):
        sync = request["sync"]
        return web.json_response(store.unmapped(sync.id, sync.user["libraries"]))

    @routes.get("/api/anilist/search")
    async def search(request):
        try:
            return web.json_response(await request["sync"].anilist.search(request.query.get("q", "")))
        except FAILURES as error:
            return bad(str(error) or type(error).__name__, 502)

    @routes.get("/api/series")
    async def read_series(request):
        try:
            return web.json_response(await request["sync"].read_series())
        except FAILURES as error:
            return bad(str(error) or type(error).__name__, 502)

    @routes.get("/api/series/{series_id:\\d+}")
    async def series_detail(request):
        try:
            return web.json_response(await request["sync"].series_detail(int(request.match_info["series_id"])))
        except FAILURES as error:
            return bad(str(error) or type(error).__name__, 502)

    @routes.post("/api/series/{series_id:\\d+}/sync")
    async def sync_one(request):
        """An explicit send for one series, allowed during dry run so it can be tried safely."""
        sync = request["sync"]
        series_id = int(request.match_info["series_id"])
        apply = bool((await request.json()).get("apply"))
        if apply and not sync.user["anilist_token"]:
            return bad("Connect AniList first")
        try:
            await sync.sync_series(series_id, "manual", dry_run=not apply, force=True)
        except FAILURES as error:
            return bad(str(error) or type(error).__name__, 502)
        return web.json_response(store.last_activity(sync.id, series_id) or {"kind": "skipped", "detail": "Library is not selected"})

    @routes.put("/api/series/{series_id:\\d+}/volumes")
    async def set_volumes(request):
        sync = request["sync"]
        volumes = (await request.json()).get("volumes")
        if not isinstance(volumes, int) or volumes < 0:
            return bad("Enter a whole number")
        if not sync.user["anilist_token"]:
            return bad("Connect AniList first")
        try:
            await sync.set_volumes(int(request.match_info["series_id"]), volumes)
        except FAILURES as error:
            return bad(str(error) or type(error).__name__, 502)
        return web.json_response({"ok": True})

    @routes.put("/api/mapping/{series_id:\\d+}")
    async def put_mapping(request):
        sync = request["sync"]
        match = re.search(r"(\d+)", str((await request.json()).get("media", "")))
        if not match:
            return bad("Enter an AniList ID or URL")
        series_id, media_id = int(request.match_info["series_id"]), int(match.group(1))
        store.set_mapping(sync.id, series_id, media_id)
        store.touch_series(sync.id, series_id, media_id=media_id, unlinked=0)
        asyncio.create_task(sync.guarded(sync.sync_series(series_id, "new link")))
        return web.json_response({"ok": True})

    @routes.delete("/api/mapping/{series_id:\\d+}")
    async def delete_mapping(request):
        sync = request["sync"]
        store.set_mapping(sync.id, int(request.match_info["series_id"]), None)
        return web.json_response({"ok": True})

    @routes.post("/api/backfill")
    async def backfill(request):
        sync = request["sync"]
        apply = bool((await request.json()).get("apply"))
        if apply and not sync.user["anilist_token"]:
            return bad("Connect AniList first")
        if apply and sync.user["dry_run"]:
            return bad("Turn off dry run first")
        asyncio.create_task(sync.guarded(sync.backfill(apply)))
        return web.json_response({"started": True})

    app = web.Application(middlewares=[guard], client_max_size=256 * 1024)
    app.add_routes(routes)

    async def lifecycle(_):
        await engine.start()
        yield
        await engine.stop()

    app.cleanup_ctx.append(lifecycle)
    return app

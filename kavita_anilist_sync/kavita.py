"""Read-only Kavita client plus the SignalR stream that carries reading progress."""
import asyncio
import base64
import json
import re
from urllib.parse import urlencode

import aiohttp

RECORD_END = "\x1e"
ANILIST_LINK = re.compile(r"anilist\.co/manga/(\d+)", re.I)


class KavitaError(Exception):
    pass


def token_claims(token):
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def user_id_from_token(token):
    """The plugin login always answers id 0, so the real ID comes from the JWT."""
    return int(token_claims(token)["nameid"])


def anilist_id(web_links):
    match = ANILIST_LINK.search(web_links or "")
    return int(match.group(1)) if match else None


class Kavita:
    def __init__(self, session, url, api_key):
        self.session = session
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.token = None
        self.user_id = None
        self.username = None

    async def session_valid(self, session_token):
        """Kavita itself checks the token's signature and expiry. This call has no side effects."""
        async with self.session.get(f"{self.url}/api/account/roles", headers={"Authorization": f"Bearer {session_token}"}) as r:
            return r.status == 200

    async def auth_key(self, session_token):
        """The user's own key, so the service reads only what that user may see."""
        async with self.session.get(f"{self.url}/api/account/auth-keys", headers={"Authorization": f"Bearer {session_token}"}) as r:
            if r.status != 200:
                raise KavitaError(f"Could not read the user's key: HTTP {r.status}")
            keys = await r.json(content_type=None)
        for key in keys:
            if key.get("name") == "opds" and key.get("key"):
                return key["key"]
        raise KavitaError("This Kavita account has no API key")

    async def login(self):
        query = urlencode({"apiKey": self.api_key, "pluginName": "kavita-anilist-sync"})
        async with self.session.post(f"{self.url}/api/plugin/authenticate?{query}") as r:
            if r.status != 200:
                raise KavitaError(f"Kavita login failed: HTTP {r.status}")
            user = await r.json(content_type=None)
        self.token = user["token"]
        self.username = user.get("username")
        self.user_id = user_id_from_token(self.token)

    async def _request(self, method, path, body=None):
        if not self.token:
            await self.login()
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self.token}"}
            async with self.session.request(method, f"{self.url}/api/{path}", json=body, headers=headers) as r:
                if r.status == 401 and attempt == 1:
                    await self.login()
                    continue
                if r.status >= 400:
                    raise KavitaError(f"{method} {path}: HTTP {r.status}")
                return await r.json(content_type=None)

    async def libraries(self):
        return await self._request("GET", "library/libraries")

    async def series(self, series_id):
        return await self._request("GET", f"series/{series_id}")

    async def metadata(self, series_id):
        return await self._request("GET", f"series/metadata?seriesId={series_id}")

    async def volumes(self, series_id):
        return await self._request("GET", f"series/volumes?seriesId={series_id}")

    async def library_series(self, library_id):
        found, page = [], 1
        while True:
            batch = await self._request(
                "POST", f"series/v2?pageNumber={page}&pageSize=200",
                {"statements": [{"comparison": 0, "field": 19, "value": str(library_id)}], "combination": 1, "limitTo": 0},
            )
            found.extend(batch)
            if len(batch) < 200:
                return found
            page += 1

    async def negotiate(self):
        if not self.token:
            await self.login()
        headers = {"Authorization": f"Bearer {self.token}"}
        async with self.session.post(f"{self.url}/hubs/messages/negotiate?negotiateVersion=1", headers=headers) as r:
            if r.status == 401:
                self.token = None
                raise KavitaError("Kavita session expired")
            if r.status != 200:
                raise KavitaError(f"SignalR negotiate failed: HTTP {r.status}")
            return await r.json(content_type=None)

    async def stream(self, on_progress, on_connected):
        """Listen until the connection drops. The caller handles reconnecting."""
        negotiated = await self.negotiate()
        connection = negotiated.get("connectionToken") or negotiated["connectionId"]
        query = urlencode({"id": connection, "access_token": self.token})
        ws_url = re.sub(r"^http", "ws", self.url) + f"/hubs/messages?{query}"
        async with self.session.ws_connect(ws_url, heartbeat=None) as ws:
            await ws.send_str(json.dumps({"protocol": "json", "version": 1}) + RECORD_END)
            pinger = asyncio.create_task(_ping(ws))
            try:
                await on_connected()
                async for message in ws:
                    if message.type != aiohttp.WSMsgType.TEXT:
                        break
                    for record in filter(None, message.data.split(RECORD_END)):
                        data = json.loads(record)
                        if data.get("type") == 7:
                            raise KavitaError(data.get("error") or "Kavita closed the stream")
                        if data.get("type") == 1 and data.get("target") == "UserProgressUpdate":
                            for argument in data.get("arguments", []):
                                await on_progress(_lower_keys(argument.get("body") or argument.get("Body") or {}))
            finally:
                pinger.cancel()


async def _ping(ws):
    while True:
        await asyncio.sleep(15)
        await ws.send_str(json.dumps({"type": 6}) + RECORD_END)


def _lower_keys(body):
    return {key[:1].lower() + key[1:]: value for key, value in body.items()}

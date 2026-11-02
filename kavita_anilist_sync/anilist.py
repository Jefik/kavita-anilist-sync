"""Minimal AniList GraphQL client."""
import asyncio
import os

import aiohttp

URL = os.environ.get("ANILIST_URL", "https://graphql.anilist.co")
MEDIA_FIELDS = """id format status chapters volumes siteUrl
title { native romaji english } coverImage { medium }"""
MEDIA = """query ($id: Int) { Media(id: $id, type: MANGA) { %s
mediaListEntry { status progress progressVolumes startedAt { year month day } } } }""" % MEDIA_FIELDS
SEARCH = """query ($q: String) { Page(perPage: 10) { media(search: $q, type: MANGA) { %s } } }""" % MEDIA_FIELDS
VIEWER = "query { Viewer { id name } }"
SAVE = """mutation ($mediaId: Int, $status: MediaListStatus, $progress: Int, $progressVolumes: Int,
$startedAt: FuzzyDateInput, $completedAt: FuzzyDateInput) {
SaveMediaListEntry(mediaId: $mediaId, status: $status, progress: $progress, progressVolumes: $progressVolumes,
startedAt: $startedAt, completedAt: $completedAt) { id status progress progressVolumes } }"""


class AniListError(Exception):
    pass


class AuthError(AniListError):
    pass


class AniList:
    def __init__(self, session, token_getter):
        self.session = session
        self.token = token_getter

    async def _call(self, query, variables=None, retries=5):
        headers = {"Accept": "application/json"}
        if self.token():
            headers["Authorization"] = f"Bearer {self.token()}"
        for _ in range(retries):
            async with self.session.post(URL, json={"query": query, "variables": variables or {}}, headers=headers) as r:
                if r.status == 429:
                    await asyncio.sleep(int(r.headers.get("Retry-After", 60)) + 1)
                    continue
                body = await r.json(content_type=None)
                if r.status in (400, 401) and "invalid token" in str(body).lower():
                    raise AuthError("AniList rejected the token")
                if r.status == 401:
                    raise AuthError("AniList token is missing or invalid")
                if r.status >= 400 or body.get("errors"):
                    raise AniListError(str(body.get("errors") or r.status)[:300])
                return body["data"]
        raise AniListError("AniList rate limit did not clear")

    async def viewer(self):
        return (await self._call(VIEWER))["Viewer"]

    async def media(self, media_id):
        return (await self._call(MEDIA, {"id": media_id}))["Media"]

    async def search(self, text):
        return (await self._call(SEARCH, {"q": text}))["Page"]["media"]

    async def save(self, plan):
        return (await self._call(SAVE, plan))["SaveMediaListEntry"]

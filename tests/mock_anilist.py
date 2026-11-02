"""Local stand-in for AniList, used by tests/e2e.py. Never contacts the real service."""
import json
import sys
import time

from aiohttp import web

state = {"entries": {}, "saves": [], "fail": [], "calls": 0}
USERS = {"token-a": "anilist-a", "token-b": "anilist-b"}
MEDIA = {
    108955: {"status": "FINISHED", "volumes": 1, "chapters": 20, "native": "サメガール"},
    75721: {"status": "FINISHED", "volumes": 3, "chapters": 30, "native": "ミスミソウ"},
    31415: {"status": "RELEASING", "volumes": None, "chapters": None, "native": "マンホール"},
}


def entries_for(token):
    return state["entries"].setdefault(token, {})


def media(media_id, token):
    item = MEDIA[media_id]
    return {
        "id": media_id, "format": "MANGA", "status": item["status"], "chapters": item["chapters"], "volumes": item["volumes"],
        "siteUrl": f"https://anilist.co/manga/{media_id}", "coverImage": {"medium": None},
        "title": {"native": item["native"], "romaji": item["native"], "english": None},
        "mediaListEntry": entries_for(token).get(str(media_id)),
    }


async def graphql(request):
    body = await request.json()
    query, variables = body["query"], body.get("variables", {})
    state["calls"] += 1
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if token and token not in USERS:
        return web.json_response({"errors": [{"message": "Invalid token"}]}, status=400)
    if state["fail"]:
        code = state["fail"].pop(0)
        return web.json_response({"errors": [{"message": "forced"}]}, status=code, headers={"Retry-After": "1"})
    if "Viewer" in query:
        return web.json_response({"data": {"Viewer": {"id": 1, "name": USERS.get(token)}}})
    if "SaveMediaListEntry" in query:
        if not token:
            return web.json_response({"errors": [{"message": "Unauthorized"}]}, status=401)
        media_id = str(variables["mediaId"])
        entry = entries_for(token).setdefault(media_id, {"status": None, "progress": 0, "progressVolumes": 0, "startedAt": None})
        entry.update({k: v for k, v in variables.items() if k != "mediaId"})
        state["saves"].append({"at": time.time(), "token": token, **variables})
        return web.json_response({"data": {"SaveMediaListEntry": {"id": 1, **entry}}})
    if "Page" in query:
        return web.json_response({"data": {"Page": {"media": [media(i, token) for i in MEDIA]}}})
    return web.json_response({"data": {"Media": media(variables["id"], token)}})


async def control(request):
    if request.method == "POST":
        changes = await request.json()
        for key, value in changes.items():
            state[key] = value
    return web.json_response(state)


app = web.Application()
app.router.add_post("/", graphql)
app.router.add_route("*", "/__state", control)
web.run_app(app, host="127.0.0.1", port=int(sys.argv[1]), print=None)

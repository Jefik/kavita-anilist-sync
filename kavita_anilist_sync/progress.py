"""Pure rules: what was read in Kavita and what AniList should receive."""
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

LOOSE_LEAF = -100_000
SPECIAL = 100_000
NUMBER_ONLY = re.compile(r"\((\d+)\)|第?(\d+)[巻第]?|(?:vol\.?|volume)\s*(\d+)|-\s*(\d+)", re.I)


@dataclass(frozen=True)
class Progress:
    volumes: int = 0
    chapters: int = 0


def reached_end(pages_read, pages):
    """The readers save a zero-based page, so opening the last page stores pages - 1."""
    return pages > 0 and pages_read > 0 and pages_read >= pages - 1


def _finished(chapter):
    return reached_end(chapter.get("pagesRead", 0), chapter.get("pages", 0))


def special_number(title, series_name):
    """Volume number of a special whose title is only the series name plus a number."""
    title = unicodedata.normalize("NFKC", title or "").strip()
    name = unicodedata.normalize("NFKC", series_name or "").strip()
    if not name or not title.startswith(name):
        return None
    match = NUMBER_ONLY.fullmatch(title[len(name):].strip())
    return int(match.group(1) or match.group(2) or match.group(3) or match.group(4)) if match else None


def _real_special(chapter):
    title = chapter.get("title") or chapter.get("range") or ""
    return chapter.get("pages", 0) > 1 and not title.startswith(".")


def read_progress(volumes, series_name=""):
    """Highest fully read volume. Chapters only count in series without volumes."""
    top_volume = top_chapter = 0
    specials = []
    for volume in volumes:
        number = int(volume.get("maxNumber", volume.get("minNumber", LOOSE_LEAF)))
        regular = []
        for chapter in volume.get("chapters", []):
            if chapter.get("isSpecial") or number >= SPECIAL:
                specials.append(chapter)
            else:
                regular.append(chapter)
        for chapter in regular:
            value = int(chapter.get("maxNumber", LOOSE_LEAF))
            if value > 0 and _finished(chapter):
                top_chapter = max(top_chapter, value)
        if 0 < number < SPECIAL and regular and all(_finished(c) for c in regular):
            top_volume = max(top_volume, number)

    specials = [c for c in specials if _real_special(c)]
    for chapter in specials:
        value = special_number(chapter.get("title") or chapter.get("range"), series_name)
        if value and _finished(chapter):
            top_volume = max(top_volume, value)
    has_numbers = any(
        (0 < int(v.get("maxNumber", LOOSE_LEAF)) < SPECIAL) or
        any(int(c.get("maxNumber", LOOSE_LEAF)) > 0 for c in v.get("chapters", []))
        for v in volumes
    )
    if not has_numbers and len(specials) == 1 and _finished(specials[0]):
        top_volume = max(top_volume, 1)
    return Progress(top_volume, 0 if top_volume else top_chapter)


def _fuzzy(day):
    return {"year": day.year, "month": day.month, "day": day.day}


def _capped(value, total):
    return min(value, total) if total else value


def plan_update(progress, media, today=None):
    """Variables for SaveMediaListEntry, or None when AniList is already ahead."""
    today = today or date.today()
    entry = media.get("mediaListEntry") or {}
    status = entry.get("status")
    if status in ("COMPLETED", "DROPPED"):
        return None

    old_chapters = entry.get("progress") or 0
    old_volumes = entry.get("progressVolumes") or 0
    chapters = _capped(progress.chapters, media.get("chapters"))
    volumes = _capped(progress.volumes, media.get("volumes"))
    best_chapters = max(chapters, old_chapters)
    best_volumes = max(volumes, old_volumes)
    finished = media.get("status") == "FINISHED" and (
        (media.get("volumes") and best_volumes >= media["volumes"])
        or (media.get("chapters") and best_chapters >= media["chapters"])
    )
    advanced = chapters > old_chapters or volumes > old_volumes
    if not advanced and not (finished and status == "CURRENT"):
        return None

    plan = {"mediaId": media["id"]}
    if chapters > old_chapters:
        plan["progress"] = chapters
    if volumes > old_volumes:
        plan["progressVolumes"] = volumes
    if finished and status != "REPEATING":
        plan["status"] = "COMPLETED"
        plan["completedAt"] = _fuzzy(today)
    elif status in (None, "PLANNING", "PAUSED"):
        plan["status"] = "CURRENT"
    if status != "REPEATING" and not (entry.get("startedAt") or {}).get("year"):
        plan["startedAt"] = _fuzzy(today)
    return plan

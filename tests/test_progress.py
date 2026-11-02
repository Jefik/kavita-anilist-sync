from datetime import date

import base64
import json

from kavita_anilist_sync.kavita import anilist_id, user_id_from_token
from kavita_anilist_sync.progress import Progress, plan_update, read_progress

TODAY = date(2026, 11, 2)
DAY = {"year": 2026, "month": 11, "day": 2}


def chapter(number=-100000, pages=10, read=0, special=False):
    return {"maxNumber": number, "pages": pages, "pagesRead": read, "isSpecial": special}


def volume(number, *chapters):
    return {"maxNumber": number, "chapters": list(chapters)}


def media(entry=None, status="RELEASING", volumes=None, chapters=None):
    return {"id": 7, "status": status, "volumes": volumes, "chapters": chapters, "mediaListEntry": entry}


def test_volume_library_counts_fully_read_volumes():
    assert read_progress([volume(1, chapter(read=10)), volume(2, chapter(read=8))]) == Progress(1, 0)


def test_opening_the_last_page_finishes_a_volume():
    assert read_progress([volume(1, chapter(pages=162, read=161))]) == Progress(1, 0)
    assert read_progress([volume(1, chapter(pages=162, read=160))]) == Progress()


def test_volumes_win_over_chapter_numbers():
    assert read_progress([volume(1, chapter(5, read=10))]) == Progress(1, 0)


def test_chapter_library_uses_loose_leaf_chapters():
    assert read_progress([volume(-100000, chapter(1, read=10), chapter(2, read=10), chapter(3, read=1))]) == Progress(0, 2)


def test_highest_read_wins_when_a_volume_is_missing():
    assert read_progress([volume(2, chapter(read=10)), volume(5, chapter(read=10))]) == Progress(5, 0)


def special(title, pages=10, read=10):
    return {**chapter(pages=pages, read=read, special=True), "title": title}


def test_unnumbered_special_does_not_count_next_to_volumes():
    assert read_progress([volume(100000, special("Art book")), volume(1, chapter(read=0))]) == Progress()


def test_special_named_only_by_a_number_counts_as_that_volume():
    series = [volume(3, chapter(read=10)), volume(100000, special("世界鬼（４）"))]
    assert read_progress(series, "世界鬼") == Progress(4, 0)


def test_typo_in_volume_marker_still_counts():
    assert read_progress([volume(100000, special("富士山さんは思春期 第02第"))], "富士山さんは思春期") == Progress(2, 0)


def test_special_with_other_words_is_not_a_volume():
    series = [volume(1, chapter(read=10)), volume(100000, special("世界鬼 5周年記念"))]
    assert read_progress(series, "世界鬼") == Progress(1, 0)


def test_standalone_book_counts_as_one_volume():
    assert read_progress([volume(-100000, special("誘"))], "誘") == Progress(1, 0)
    assert read_progress([volume(-100000, special("誘", read=3))], "誘") == Progress()


def test_thumbnail_files_are_ignored():
    series = [volume(100000, special(".thumb", pages=1), special(".preview", pages=1), special("Attack on Titan 1", read=0))]
    assert read_progress(series, "Attack on Titan") == Progress()


def test_special_chapter_inside_volume_is_ignored():
    assert read_progress([volume(1, chapter(read=10), chapter(read=0, special=True))]) == Progress(1, 0)


def test_empty_chapter_is_not_finished():
    assert read_progress([volume(1, chapter(pages=0, read=0))]) == Progress()


def test_new_entry_starts_as_current():
    assert plan_update(Progress(1, 0), media(), TODAY) == {"mediaId": 7, "progressVolumes": 1, "status": "CURRENT", "startedAt": DAY}


def test_finished_series_is_completed_even_without_new_progress():
    plan = plan_update(Progress(3, 0), media({"status": "CURRENT", "progressVolumes": 3, "startedAt": {"year": 2020}}, "FINISHED", 3), TODAY)
    assert plan == {"mediaId": 7, "status": "COMPLETED", "completedAt": DAY}


def test_never_lowers_progress():
    assert plan_update(Progress(2, 0), media({"status": "CURRENT", "progressVolumes": 5, "progress": 40}), TODAY) is None


def test_chapters_stay_untouched_when_only_volumes_advance():
    plan = plan_update(Progress(6, 0), media({"status": "CURRENT", "progressVolumes": 5, "progress": 40, "startedAt": {"year": 2020}}), TODAY)
    assert plan == {"mediaId": 7, "progressVolumes": 6}


def test_completed_and_dropped_are_left_alone():
    assert plan_update(Progress(9, 9), media({"status": "COMPLETED"}), TODAY) is None
    assert plan_update(Progress(9, 9), media({"status": "DROPPED"}), TODAY) is None


def test_last_volume_of_finished_series_completes_it():
    plan = plan_update(Progress(3, 0), media({"status": "CURRENT", "progressVolumes": 2, "startedAt": {"year": 2020}}, "FINISHED", 3), TODAY)
    assert plan["status"] == "COMPLETED" and plan["completedAt"] == DAY


def test_releasing_series_never_completes():
    plan = plan_update(Progress(3, 0), media({"status": "CURRENT", "startedAt": {"year": 2020}}, "RELEASING", 3), TODAY)
    assert "status" not in plan


def test_progress_is_capped_to_known_total():
    assert plan_update(Progress(9, 0), media(None, "FINISHED", 3), TODAY)["progressVolumes"] == 3


def test_repeating_keeps_status_and_dates():
    plan = plan_update(Progress(2, 0), media({"status": "REPEATING", "progressVolumes": 1}, "FINISHED", 2), TODAY)
    assert plan == {"mediaId": 7, "progressVolumes": 2}


def test_planning_becomes_current():
    assert plan_update(Progress(1, 0), media({"status": "PLANNING"}), TODAY)["status"] == "CURRENT"


def test_anilist_link_is_found_among_other_links():
    assert anilist_id("https://bgm.tv/subject/1,https://anilist.co/manga/108955/Same") == 108955
    assert anilist_id("https://myanimelist.net/manga/1") is None
    assert anilist_id(None) is None


def test_user_id_comes_from_the_token():
    payload = base64.urlsafe_b64encode(json.dumps({"nameid": "12"}).encode()).decode().rstrip("=")
    assert user_id_from_token(f"header.{payload}.signature") == 12

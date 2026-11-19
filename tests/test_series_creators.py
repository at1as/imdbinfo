from unittest.mock import patch

import pytest

from imdbinfo import GraphQLError, ParseError, get_series_creators
from imdbinfo.locale import get_locale, set_locale
from imdbinfo.parsers import OldCategoryIdToNewCategoryIdObject


def _credit(imdb_id, name, *roles):
    return {"node": {
        "name": {"id": imdb_id, "nameText": {"text": name}},
        "creditedRoles": {"edges": [{"node": {"text": role}} for role in roles]},
    }}


def _page(*edges, has_next=False, cursor=None):
    return {"data": {"title": {"creditsV2": {
        "edges": list(edges),
        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
    }}}}


@pytest.fixture(autouse=True)
def clear_creator_cache():
    get_series_creators.cache_clear()
    yield
    get_series_creators.cache_clear()


def test_series_creators_follow_cursor_and_request_english_roles():
    pages = [
        _page(
            _credit("nm0000001", "Creator One", None, "writer", " Created by "),
            _credit("nm0000002", "Book Author", "based on the book by"),
            has_next=True,
            cursor="second-page",
        ),
        _page(
            _credit("nm0000001", "Creator One", "created for television by"),
            _credit("nm0000003", "Creator Two", "created for television by"),
            _credit("nm0000004", "Character Author", "based on characters created by"),
        ),
    ]
    original_locale = get_locale()
    set_locale("it")
    try:
        with patch("imdbinfo.services.request_graphql_url", side_effect=pages) as request:
            creators = get_series_creators("1234567")
        assert get_locale() == "it"
    finally:
        set_locale(original_locale)

    assert [(creator.imdbId, creator.name) for creator in creators] == [
        ("nm0000001", "Creator One"),
        ("nm0000003", "Creator Two"),
    ]
    assert [creator.job for creator in creators] == ["Creator", "Creator"]
    assert [call.args[2]["variables"]["after"] for call in request.call_args_list] == [
        None, "second-page",
    ]
    for call in request.call_args_list:
        headers, title_id, payload, _ = call.args
        assert title_id == "tt1234567"
        assert headers["Accept-Language"].startswith("en-US")
        assert headers["x-imdb-user-language"] == "en-US"
        assert payload["variables"]["category"] == OldCategoryIdToNewCategoryIdObject["writer"]
        assert call.kwargs["timeout"] == 15


def test_series_creators_return_empty_list_without_explicit_roles():
    page = _page(_credit("nm0000001", "Episode Writer", None, "writer"))
    with patch("imdbinfo.services.request_graphql_url", return_value=page):
        assert get_series_creators("tt1234567") == []


@pytest.mark.parametrize("cursor", [None, "second-page"])
def test_series_creators_reject_missing_or_repeated_cursor(cursor):
    pages = [
        _page(has_next=True, cursor="second-page"),
        _page(has_next=True, cursor=cursor),
    ]
    with patch("imdbinfo.services.request_graphql_url", side_effect=pages):
        with pytest.raises(ParseError, match="Missing or repeated writer credits cursor"):
            get_series_creators("tt1234567")


def test_series_creators_reject_invalid_page():
    with patch("imdbinfo.services.request_graphql_url", return_value={"data": {"title": None}}):
        with pytest.raises(ParseError, match="Invalid writer credits page"):
            get_series_creators("tt1234567")


def test_series_creators_retry_after_failed_continuation():
    first = _page(_credit("nm0000001", "Creator One", "created by"),
                  has_next=True, cursor="second-page")
    second = _page(_credit("nm0000002", "Creator Two", "created by"))
    pages = [first, TimeoutError("timed out"), first, second]
    with patch("imdbinfo.services.request_graphql_url", side_effect=pages) as request:
        with pytest.raises(GraphQLError, match="Could not fetch writer credits"):
            get_series_creators("tt1234567")
        assert [person.name for person in get_series_creators("tt1234567")] == [
            "Creator One", "Creator Two",
        ]
        assert request.call_count == 4

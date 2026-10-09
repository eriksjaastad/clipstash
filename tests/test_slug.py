"""Tests for helper.slug (web-safe still names)."""

from __future__ import annotations

from helper.slug import MAX_SLUG_LEN, default_slug, slugify


def test_slugify_lowercases_and_dashes_spaces():
    assert slugify("How I edit Thumbnails") == "how-i-edit-thumbnails"


def test_slugify_strips_punctuation_and_collapses_runs():
    assert slugify("  Hello!!! --- world ???  ") == "hello-world"


def test_slugify_folds_accents_to_ascii():
    assert slugify("Café résumé") == "cafe-resume"


def test_slugify_drops_scripts_that_cannot_fold_to_ascii():
    # CJK has no ASCII fold: the slug must never come back empty.
    assert slugify("你好世界", fallback="youtube-01jabc12") == "youtube-01jabc12"


def test_slugify_empty_and_whitespace_use_fallback():
    assert slugify("") == "still"
    assert slugify("   ") == "still"
    assert slugify(None) == "still"


def test_slugify_punctuation_only_uses_fallback():
    assert slugify("!!!") == "still"
    assert slugify("!!!", fallback="generic-01jabc12") == "generic-01jabc12"


def test_slugify_caps_length_and_strips_trailing_dash():
    long_title = "word " * 50
    slug = slugify(long_title)
    assert len(slug) <= MAX_SLUG_LEN
    assert not slug.endswith("-")


def test_slugify_custom_max_length():
    assert slugify("a b c d e f", max_length=5) == "a-b-c"


def test_default_slug_prefers_title():
    assert default_slug("How I edit", "youtube", "01JABC12DEFGHJKMNPQRSTVWXYZ") == "how-i-edit"


def test_default_slug_falls_back_to_site_and_short_id():
    assert (
        default_slug("!!!", "tiktok", "01JABC12DEFGHJKMNPQRSTVWXYZ")
        == "tiktok-01jabc12"
    )


def test_default_slug_never_returns_still_for_empty_site_id():
    assert default_slug("", "", "") == "still"

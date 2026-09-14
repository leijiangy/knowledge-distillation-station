# -*- coding: utf-8 -*-
from datetime import datetime, timezone
from itertools import count
from uuid import UUID

import pytest

from core.content_versions import (
    BareContentRepository,
    ContentVersionError,
    GitRefConflict,
    HistoricalVersion,
    OffsetError,
    article_digest,
    build_manifest,
    canonical_json_bytes,
    codepoint_range_to_utf16,
    codepoint_to_utf16,
    inherit_manifest,
    manifest_sha256,
    unchanged_blocks,
    utf16_range_to_codepoints,
    utf16_to_codepoint,
    validate_manifest,
)


META = {"url": "https://example.test/a", "title": "题目", "images": [], "summary": "主旨"}


def ids():
    serial = count(1)
    return lambda: UUID(int=next(serial))


def texts(content, manifest):
    return [content[row["text_start"] : row["text_end"]] for row in manifest["segments"]]


def id_by_text(content, manifest):
    return {text: row["segment_id"] for text, row in zip(texts(content, manifest), manifest["segments"])}


def test_utf16_codepoint_round_trip_and_surrogate_rejection():
    text = "甲😀乙𝄞丙"
    assert [codepoint_to_utf16(text, i) for i in range(len(text) + 1)] == [0, 1, 3, 4, 6, 7]
    assert [utf16_to_codepoint(text, i) for i in (0, 1, 3, 4, 6, 7)] == list(range(6))
    with pytest.raises(OffsetError):
        utf16_to_codepoint(text, 2)
    with pytest.raises(OffsetError):
        utf16_to_codepoint(text, True)
    assert codepoint_range_to_utf16(text, 1, 4) == (1, 6)
    assert utf16_range_to_codepoints(text, 1, 6) == (1, 4)


def test_manifest_is_canonical_and_covers_crlf_without_trailing_newline():
    content = "  甲😀\r\n\r\n乙  "
    manifest = build_manifest(content, [8], uuid_factory=ids())
    validate_manifest(content, manifest)
    assert texts(content, manifest) == ["甲😀", "乙"]
    assert manifest["segments"][0]["raw_start"] == 0
    assert manifest["segments"][-1]["raw_end"] == len(content)
    assert canonical_json_bytes(manifest) == canonical_json_bytes(manifest)
    assert len(manifest_sha256(manifest)) == 64


def test_manifest_rejects_bool_duplicate_cut_empty_segment_and_bad_hash():
    for cuts in ([True], [1, 1], [1, 2]):
        with pytest.raises(ContentVersionError):
            build_manifest("a b", cuts, uuid_factory=ids())
    with pytest.raises(ContentVersionError):
        build_manifest("   ", [], uuid_factory=ids())
    manifest = build_manifest("abc", [], uuid_factory=ids())
    manifest["segments"][0]["text_sha256"] = "0" * 64
    with pytest.raises(ContentVersionError):
        validate_manifest("abc", manifest)


def test_unchanged_blocks_refines_inside_replaced_long_line():
    old = "前半段保持完全不变，" * 8 + "旧后半"
    new = "前半段保持完全不变，" * 8 + "新后半"
    blocks = unchanged_blocks(old, new)
    prefix = len("前半段保持完全不变，" * 8)
    assert any(a == 0 and b == 0 and size >= prefix for a, b, size in blocks)


def test_exact_content_keeps_manifest_even_if_suggested_cuts_change():
    content = "甲段\n乙段"
    old = build_manifest(content, [3], uuid_factory=ids())
    got = inherit_manifest(content, old, content, [], block_provider=unchanged_blocks, uuid_factory=ids())
    assert got == old


def test_edit_keeps_only_unchanged_segment_identity():
    old_content = "A段\nB段\nC段"
    old = build_manifest(old_content, [3, 6], uuid_factory=ids())
    new_content = "A段\nB已修改\nD段"
    got = inherit_manifest(old_content, old, new_content, [3, 8], block_provider=unchanged_blocks, uuid_factory=ids())
    old_ids, new_ids = id_by_text(old_content, old), id_by_text(new_content, got)
    assert new_ids["A段"] == old_ids["A段"]
    assert new_ids["B已修改"] not in old_ids.values()
    assert new_ids["D段"] not in old_ids.values()


def test_unique_complete_paragraph_move_inherits_id():
    old_content = "甲段\n乙段\n丙段"
    old = build_manifest(old_content, [3, 6], uuid_factory=ids())
    new_content = "丙段\n甲段\n乙段"
    got = inherit_manifest(old_content, old, new_content, [3, 6], block_provider=unchanged_blocks, uuid_factory=ids())
    assert id_by_text(new_content, got)["丙段"] == id_by_text(old_content, old)["丙段"]


def test_repeated_text_deletion_is_ambiguous_and_gets_new_id():
    old_content = "重复\n重复\n唯一"
    old = build_manifest(old_content, [3, 6], uuid_factory=ids())
    new_content = "重复\n唯一"
    got = inherit_manifest(old_content, old, new_content, [3], block_provider=unchanged_blocks, uuid_factory=ids())
    assert id_by_text(new_content, got)["重复"] not in {
        row["segment_id"] for row in old["segments"][:2]
    }


def test_repeated_text_unchanged_count_and_monotonic_mapping_keeps_each_id():
    content = "重复\n重复\n唯一"
    old = build_manifest(content, [3, 6], uuid_factory=ids())
    new_content = "前言\n重复\n重复\n唯一"
    got = inherit_manifest(content, old, new_content, [3, 6, 9], block_provider=unchanged_blocks, uuid_factory=ids())
    old_repeat = [r["segment_id"] for r in old["segments"][:2]]
    new_repeat = [r["segment_id"] for r in got["segments"] if new_content[r["text_start"]:r["text_end"]] == "重复"]
    assert new_repeat == old_repeat


def test_unique_historical_paragraph_restores_deleted_identity():
    v1_content = "甲段\n乙段"
    v1 = build_manifest(v1_content, [3], uuid_factory=ids())
    v2_content = "甲段"
    v2 = inherit_manifest(v1_content, v1, v2_content, [], block_provider=unchanged_blocks, uuid_factory=ids())
    v3_content = "甲段\n乙段"
    v3 = inherit_manifest(
        v2_content,
        v2,
        v3_content,
        [3],
        block_provider=unchanged_blocks,
        historical_versions=[HistoricalVersion(v1_content, v1)],
        uuid_factory=ids(),
    )
    assert id_by_text(v3_content, v3)["乙段"] == id_by_text(v1_content, v1)["乙段"]


def test_historical_restore_precedes_suggested_segmentation():
    factory = ids()
    v1_content = "甲段\n乙段\n丙段"
    v1 = build_manifest(v1_content, [3, 6], uuid_factory=factory)
    v2_content = "甲段"
    v2 = inherit_manifest(
        v1_content, v1, v2_content, [], block_provider=unchanged_blocks, uuid_factory=factory,
    )
    restored = inherit_manifest(
        v2_content, v2, v1_content, [], block_provider=unchanged_blocks,
        historical_versions=[HistoricalVersion(v1_content, v1)], uuid_factory=factory,
    )
    assert id_by_text(v1_content, restored) == id_by_text(v1_content, v1)


def test_repeated_ancestor_is_ambiguity_evidence_for_history_restore():
    factory = ids()
    v1 = build_manifest("重复", [], uuid_factory=factory)
    old_content = "重复\n重复"
    old = build_manifest(old_content, [3], uuid_factory=factory)
    got = inherit_manifest(
        old_content, old, "重复", [], block_provider=unchanged_blocks,
        historical_versions=[HistoricalVersion("重复", v1)], uuid_factory=factory,
    )
    assert got["segments"][0]["segment_id"] not in {
        v1["segments"][0]["segment_id"], *(row["segment_id"] for row in old["segments"])
    }


def test_multiple_historical_ids_for_same_text_do_not_restore():
    text = "甲段\n乙段"
    factory = ids()
    h1 = build_manifest(text, [3], uuid_factory=factory)
    h2 = build_manifest(text, [3], uuid_factory=factory)
    current_text = "甲段"
    current = build_manifest(current_text, [], uuid_factory=factory)
    got = inherit_manifest(
        current_text,
        current,
        text,
        [3],
        block_provider=unchanged_blocks,
        historical_versions=[HistoricalVersion(text, h1), HistoricalVersion(text, h2)],
        uuid_factory=factory,
    )
    assert id_by_text(text, got)["乙段"] not in {
        id_by_text(text, h1)["乙段"], id_by_text(text, h2)["乙段"]
    }


def test_bare_git_writes_exact_three_files_and_replays_same_candidate(tmp_path):
    repo = BareContentRepository(tmp_path / "content.git")
    repo.initialize()
    content = " 甲😀\r\n乙"
    manifest = build_manifest(content, [5], uuid_factory=ids())
    update_id = "11111111-1111-1111-1111-111111111111"
    meta = META
    instant = datetime(2026, 9, 15, tzinfo=timezone.utc)
    commit = repo.create_version(
        article_key="article:1", update_id=update_id, content=content,
        meta=meta, manifest=manifest, committed_at=instant,
    )
    replay = repo.create_version(
        article_key="article:1", update_id=update_id, content=content,
        meta=meta, manifest=manifest, committed_at=instant,
    )
    assert replay == commit
    stored = repo.read_version(commit)
    assert stored.content == content
    assert stored.meta["article_key"] == "article:1"
    assert stored.manifest == manifest
    assert stored.parent is None
    assert repo.get_ref(repo.candidate_ref("article:1", update_id)) == commit
    assert article_digest("article:1") in repo.candidate_ref("article:1", update_id)


def test_git_myers_hunks_preserve_crlf_and_refine_changed_line(tmp_path):
    repo = BareContentRepository(tmp_path / "content.git")
    repo.initialize()
    old = "甲😀不变\r\n很长前缀保持" + "甲" * 100 + "旧尾"
    new = "甲😀不变\r\n很长前缀保持" + "甲" * 100 + "新尾"
    blocks = repo.git_unchanged_blocks(old, new)
    prefix = len(old) - len("旧尾")
    assert any(a == 0 and b == 0 and size >= prefix for a, b, size in blocks)
    old_manifest = build_manifest(old, [6], uuid_factory=ids())
    inherited = inherit_manifest(
        old, old_manifest, new, [6], uuid_factory=ids(),
        block_provider=repo.git_unchanged_blocks,
    )
    assert inherited["segments"][0]["segment_id"] == old_manifest["segments"][0]["segment_id"]


def test_git_hunk_refinement_keeps_internal_segment_of_one_long_line(tmp_path):
    repo = BareContentRepository(tmp_path / "content.git")
    repo.initialize()
    prefix = "".join(chr(0x4E00 + index) for index in range(100))
    old, new = prefix + "旧后半", prefix + "新后半"
    cut = len(prefix) - 20
    old_manifest = build_manifest(old, [cut], uuid_factory=ids())
    got = inherit_manifest(
        old, old_manifest, new, [cut], block_provider=repo.git_unchanged_blocks,
        uuid_factory=ids(),
    )
    assert got["segments"][0]["segment_id"] == old_manifest["segments"][0]["segment_id"]


def test_git_reader_rejects_revision_expression(tmp_path):
    repo = BareContentRepository(tmp_path / "content.git")
    repo.initialize()
    with pytest.raises(ContentVersionError):
        repo.read_version("HEAD")


def test_bare_git_parent_chain_and_compare_and_swap_branch(tmp_path):
    repo = BareContentRepository(tmp_path / "content.git")
    repo.initialize()
    instant = "2026-09-15T00:00:00+00:00"
    first_manifest = build_manifest("甲", [], uuid_factory=ids())
    first = repo.create_version(
        article_key="a", update_id="10000000-0000-0000-0000-000000000001",
        content="甲", meta=META, manifest=first_manifest, committed_at=instant,
    )
    second_manifest = inherit_manifest(
        "甲", first_manifest, "甲\n乙", [2],
        block_provider=repo.git_unchanged_blocks, uuid_factory=ids(),
    )
    second = repo.create_version(
        article_key="a", update_id="10000000-0000-0000-0000-000000000002",
        content="甲\n乙", meta=META, manifest=second_manifest, parent=first, committed_at=instant,
    )
    assert repo.read_version(second).parent == first
    repo.update_branch("a", first, None)
    repo.update_branch("a", second, first)
    with pytest.raises(GitRefConflict):
        repo.update_branch("a", first, first)


def test_immutable_candidate_ref_rejects_changed_payload(tmp_path):
    repo = BareContentRepository(tmp_path / "content.git")
    repo.initialize()
    update_id = "20000000-0000-0000-0000-000000000001"
    instant = "2026-09-15T00:00:00+00:00"
    repo.create_version(
        article_key="a", update_id=update_id, content="甲", meta=META,
        manifest=build_manifest("甲", [], uuid_factory=ids()), committed_at=instant,
    )
    with pytest.raises(GitRefConflict):
        repo.create_version(
            article_key="a", update_id=update_id, content="乙", meta=META,
            manifest=build_manifest("乙", [], uuid_factory=ids()), committed_at=instant,
        )


def test_meta_cannot_embed_its_own_commit(tmp_path):
    repo = BareContentRepository(tmp_path / "content.git")
    repo.initialize()
    with pytest.raises(ContentVersionError):
        repo.create_version(
            article_key="a", update_id="30000000-0000-0000-0000-000000000001",
            content="甲", meta={**META, "commit": "bad"},
            manifest=build_manifest("甲", [], uuid_factory=ids()),
            committed_at="2026-09-15T00:00:00+00:00",
        )


def test_initialize_probes_object_and_ref_io(tmp_path):
    repo = BareContentRepository(tmp_path / "content.git")
    repo.initialize()
    refs = repo._run(["for-each-ref", "refs/kd-storage-probes"]).stdout
    assert refs == b""
    assert repo._run(["count-objects", "-v"]).returncode == 0


def test_commit_time_must_be_fixed_timezone_aware_value(tmp_path):
    repo = BareContentRepository(tmp_path / "content.git")
    repo.initialize()
    with pytest.raises(ContentVersionError):
        repo.create_version(
            article_key="a", update_id="40000000-0000-0000-0000-000000000001",
            content="甲", meta=META,
            manifest=build_manifest("甲", [], uuid_factory=ids()), committed_at="now",
        )

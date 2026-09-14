# -*- coding: utf-8 -*-
"""文章内容版本的纯函数和裸 Git 存储适配。

本模块刻意不接数据库：它只负责字符坐标、确定性分段清单、跨版本段身份
继承，以及不可变 Git 候选提交。数据库发布/CAS 由上层事务实现。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import os
import re
import subprocess
from typing import Callable, Iterable, Mapping, Sequence
from uuid import UUID, uuid4


class ContentVersionError(ValueError):
    """内容版本输入违反不变量。"""


class OffsetError(ContentVersionError):
    """字符坐标无效，或 UTF-16 坐标落在代理对中间。"""


class GitStorageError(RuntimeError):
    """Git 对象读取或写入失败。"""


class GitRefConflict(GitStorageError):
    """不可变引用已经指向另一候选提交。"""


@dataclass(frozen=True)
class HistoricalVersion:
    content: str
    manifest: Mapping[str, object]


@dataclass(frozen=True)
class StoredVersion:
    commit: str
    content: str
    meta: dict
    manifest: dict
    parent: str | None


def _index(value: object, *, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OffsetError(f"{name} 必须是整数")
    if value < 0 or value > maximum:
        raise OffsetError(f"{name} 超出范围")
    return value


def codepoint_to_utf16(text: str, offset: int) -> int:
    """把 Python/数据库的 Unicode 码点下标转换成 DOM UTF-16 下标。"""
    offset = _index(offset, name="offset", maximum=len(text))
    return len(text[:offset].encode("utf-16-le")) // 2


def utf16_to_codepoint(text: str, offset: int) -> int:
    """把 DOM UTF-16 下标转换成码点下标，拒绝代理对中间位置。"""
    maximum = len(text.encode("utf-16-le")) // 2
    offset = _index(offset, name="offset", maximum=maximum)
    units = 0
    for index, char in enumerate(text):
        if units == offset:
            return index
        width = 2 if ord(char) > 0xFFFF else 1
        if units < offset < units + width:
            raise OffsetError("UTF-16 offset 落在代理对中间")
        units += width
    if units == offset:
        return len(text)
    raise OffsetError("offset 超出范围")


def utf16_range_to_codepoints(text: str, start: int, end: int) -> tuple[int, int]:
    a = utf16_to_codepoint(text, start)
    b = utf16_to_codepoint(text, end)
    if a >= b:
        raise OffsetError("选区必须满足 start < end")
    return a, b


def codepoint_range_to_utf16(text: str, start: int, end: int) -> tuple[int, int]:
    a = _index(start, name="start", maximum=len(text))
    b = _index(end, name="end", maximum=len(text))
    if a >= b:
        raise OffsetError("选区必须满足 start < end")
    return codepoint_to_utf16(text, a), codepoint_to_utf16(text, b)


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def article_digest(article_key: str) -> str:
    if not isinstance(article_key, str) or not article_key:
        raise ContentVersionError("article_key 不能为空")
    return text_sha256(article_key)


def _validated_cuts(content: str, cuts: Sequence[int]) -> list[int]:
    result: list[int] = []
    previous = 0
    for cut in cuts:
        cut = _index(cut, name="cut", maximum=len(content))
        if cut <= previous or cut >= len(content):
            raise ContentVersionError("cuts 必须严格递增并位于正文内部")
        result.append(cut)
        previous = cut
    return result


def _trimmed_interval(content: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and content[start].isspace():
        start += 1
    while end > start and content[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None


def build_manifest(
    content: str,
    cuts: Sequence[int],
    segment_ids: Sequence[str] | None = None,
    *,
    uuid_factory: Callable[[], object] = uuid4,
) -> dict:
    """从码点 cuts 构造覆盖全文的确定性清单。

    空展示段被拒绝，避免后续节点锚到不可见文本。调用方应先把纯空白间隙
    并入相邻段，再传入 cuts。
    """
    if not isinstance(content, str):
        raise ContentVersionError("content 必须是字符串")
    if not content or not content.strip():
        raise ContentVersionError("正文不能是空白")
    checked = _validated_cuts(content, cuts)
    bounds = [0, *checked, len(content)]
    count = len(bounds) - 1
    if segment_ids is not None and len(segment_ids) != count:
        raise ContentVersionError("segment_ids 数量必须与段数一致")
    segments = []
    seen: set[str] = set()
    for i, (raw_start, raw_end) in enumerate(zip(bounds, bounds[1:])):
        trimmed = _trimmed_interval(content, raw_start, raw_end)
        if trimmed is None:
            raise ContentVersionError("清单不能包含空展示段")
        text_start, text_end = trimmed
        raw_id = segment_ids[i] if segment_ids is not None else str(uuid_factory())
        try:
            segment_id = str(UUID(str(raw_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ContentVersionError("segment_id 必须是 UUID") from exc
        if segment_id in seen:
            raise ContentVersionError("本版 segment_id 必须唯一")
        seen.add(segment_id)
        segments.append(
            {
                "segment_id": segment_id,
                "raw_start": raw_start,
                "raw_end": raw_end,
                "text_start": text_start,
                "text_end": text_end,
                "text_sha256": text_sha256(content[text_start:text_end]),
            }
        )
    manifest = {"schema_version": 1, "segments": segments}
    validate_manifest(content, manifest)
    return manifest


def validate_manifest(content: str, manifest: Mapping[str, object]) -> None:
    if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != 1:
        raise ContentVersionError("不支持的 manifest schema_version")
    raw_segments = manifest.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ContentVersionError("manifest 必须包含至少一个段")
    cursor = 0
    seen: set[str] = set()
    for raw in raw_segments:
        if not isinstance(raw, Mapping):
            raise ContentVersionError("segment 必须是对象")
        try:
            segment_id = str(UUID(str(raw["segment_id"])))
            raw_start = _index(raw["raw_start"], name="raw_start", maximum=len(content))
            raw_end = _index(raw["raw_end"], name="raw_end", maximum=len(content))
            text_start = _index(raw["text_start"], name="text_start", maximum=len(content))
            text_end = _index(raw["text_end"], name="text_end", maximum=len(content))
        except KeyError as exc:
            raise ContentVersionError(f"manifest 缺少字段 {exc.args[0]}") from exc
        if segment_id in seen:
            raise ContentVersionError("本版 segment_id 必须唯一")
        seen.add(segment_id)
        if raw_start != cursor or not (raw_start <= text_start < text_end <= raw_end):
            raise ContentVersionError("segment 范围不连续或 text 不在 raw 内")
        if content[raw_start:raw_end].strip() != content[text_start:text_end]:
            raise ContentVersionError("text 范围必须等于 raw 去首尾空白")
        if raw.get("text_sha256") != text_sha256(content[text_start:text_end]):
            raise ContentVersionError("segment 文本指纹不匹配")
        cursor = raw_end
    if cursor != len(content):
        raise ContentVersionError("raw 范围必须完整覆盖正文")


def manifest_bytes(manifest: Mapping[str, object]) -> bytes:
    return canonical_json_bytes(manifest)


def manifest_sha256(manifest: Mapping[str, object]) -> str:
    return hashlib.sha256(manifest_bytes(manifest)).hexdigest()


def _line_parts(text: str) -> tuple[list[str], list[int]]:
    # splitlines() 对无末尾换行有易错边界；显式按 LF 建码点前缀表。
    lines: list[str] = []
    starts = [0]
    cursor = 0
    while True:
        newline = text.find("\n", cursor)
        if newline < 0:
            if cursor < len(text):
                lines.append(text[cursor:])
                starts.append(len(text))
            break
        lines.append(text[cursor : newline + 1])
        cursor = newline + 1
        starts.append(cursor)
        if cursor == len(text):
            break
    return lines, starts


def unchanged_blocks(old: str, new: str) -> list[tuple[int, int, int]]:
    """返回 ``(旧起点, 新起点, 长度)`` 的原位单调未变码点块。

    先按 LF 做 Myers 等价的单调行匹配，再在替换 hunk 内使用关闭
    autojunk 的 SequenceMatcher 精化。移动匹配由后续唯一自然段规则处理。
    """
    old_lines, old_starts = _line_parts(old)
    new_lines, new_starts = _line_parts(new)
    matcher = SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    blocks: list[tuple[int, int, int]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old_start, old_end = old_starts[i1], old_starts[i2]
        new_start, new_end = new_starts[j1], new_starts[j2]
        if tag == "equal":
            if old_end > old_start:
                blocks.append((old_start, new_start, old_end - old_start))
            continue
        if tag != "replace":
            continue
        old_hunk, new_hunk = old[old_start:old_end], new[new_start:new_end]
        refined = SequenceMatcher(None, old_hunk, new_hunk, autojunk=False)
        for block in refined.get_matching_blocks():
            if block.size:
                blocks.append((old_start + block.a, new_start + block.b, block.size))
    blocks.sort()
    merged: list[tuple[int, int, int]] = []
    for old_start, new_start, size in blocks:
        if merged:
            a, b, n = merged[-1]
            if a + n == old_start and b + n == new_start:
                merged[-1] = (a, b, n + size)
                continue
        merged.append((old_start, new_start, size))
    return merged


_HUNK_HEADER = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",
    re.MULTILINE,
)


def _git_hunk_blocks(
    old: str, new: str, diff_output: str
) -> list[tuple[int, int, int]]:
    """从 Git ``--unified=0`` hunk 生成未变块，并精化替换 hunk。"""
    _old_lines, old_starts = _line_parts(old)
    _new_lines, new_starts = _line_parts(new)
    hunks = []
    for match in _HUNK_HEADER.finditer(diff_output):
        old_line, old_count = int(match.group(1)), int(match.group(2) or 1)
        new_line, new_count = int(match.group(3)), int(match.group(4) or 1)
        old_start = old_starts[old_line if old_count == 0 else old_line - 1]
        old_end = old_starts[old_line if old_count == 0 else old_line - 1 + old_count]
        new_start = new_starts[new_line if new_count == 0 else new_line - 1]
        new_end = new_starts[new_line if new_count == 0 else new_line - 1 + new_count]
        hunks.append((old_start, old_end, new_start, new_end))
    if old != new and not hunks:
        raise GitStorageError("Git diff 未返回可解析的 hunk")

    blocks: list[tuple[int, int, int]] = []
    old_cursor = new_cursor = 0
    for old_start, old_end, new_start, new_end in hunks:
        old_gap, new_gap = old[old_cursor:old_start], new[new_cursor:new_start]
        if old_gap != new_gap:
            raise GitStorageError("Git hunk 间的未变文本不一致")
        if old_gap:
            blocks.append((old_cursor, new_cursor, len(old_gap)))
        refined = SequenceMatcher(
            None, old[old_start:old_end], new[new_start:new_end], autojunk=False
        )
        for block in refined.get_matching_blocks():
            if block.size:
                blocks.append((old_start + block.a, new_start + block.b, block.size))
        old_cursor, new_cursor = old_end, new_end
    if old[old_cursor:] != new[new_cursor:]:
        raise GitStorageError("Git 最后一个 hunk 后的未变文本不一致")
    if old_cursor < len(old):
        blocks.append((old_cursor, new_cursor, len(old) - old_cursor))
    blocks.sort()
    merged: list[tuple[int, int, int]] = []
    for old_start, new_start, size in blocks:
        if merged:
            a, b, n = merged[-1]
            if a + n == old_start and b + n == new_start:
                merged[-1] = (a, b, n + size)
                continue
        merged.append((old_start, new_start, size))
    return merged


def _segment_rows(content: str, manifest: Mapping[str, object]) -> list[dict]:
    validate_manifest(content, manifest)
    return [dict(row) for row in manifest["segments"]]  # type: ignore[index]


def _occurrences(haystack: str, needle: str) -> list[int]:
    if not needle:
        return []
    result: list[int] = []
    cursor = 0
    while True:
        at = haystack.find(needle, cursor)
        if at < 0:
            return result
        result.append(at)
        cursor = at + 1


def _natural_paragraph(text: str, start: int, end: int) -> bool:
    line_start = text.rfind("\n", 0, start) + 1
    newline = text.find("\n", end)
    line_end = len(text) if newline < 0 else newline
    return not text[line_start:start].strip() and not text[end:line_end].strip()


def _mapped_interval(row: Mapping[str, object], blocks: Sequence[tuple[int, int, int]]):
    start, end = int(row["text_start"]), int(row["text_end"])
    for old_start, new_start, size in blocks:
        if old_start <= start and end <= old_start + size:
            shift = new_start - old_start
            return start + shift, end + shift
    return None


def _cancel_overlaps(candidates: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
    conflicted: set[str] = set()
    items = list(candidates.items())
    for i, (left_id, (a, b)) in enumerate(items):
        for right_id, (c, d) in items[i + 1 :]:
            if max(a, c) < min(b, d):
                conflicted.update((left_id, right_id))
    return {key: value for key, value in candidates.items() if key not in conflicted}


def _gap_intervals(content: str, start: int, end: int, cuts: Sequence[int]):
    bounds = [start, *(cut for cut in cuts if start < cut < end), end]
    for left, right in zip(bounds, bounds[1:]):
        trimmed = _trimmed_interval(content, left, right)
        if trimmed is not None:
            yield trimmed


def _fresh_segment_id(factory: Callable[[], object], forbidden: set[str]) -> str:
    """取得未被当前或历史清单占用的 UUID，防御可注入工厂发生碰撞。"""
    for _ in range(1024):
        try:
            candidate = str(UUID(str(factory())))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ContentVersionError("uuid_factory 必须返回 UUID") from exc
        if candidate not in forbidden:
            forbidden.add(candidate)
            return candidate
    raise ContentVersionError("无法生成未占用的 segment_id")


def inherit_manifest(
    old_content: str,
    old_manifest: Mapping[str, object],
    new_content: str,
    suggested_cuts: Sequence[int],
    *,
    block_provider: Callable[[str, str], list[tuple[int, int, int]]],
    historical_versions: Iterable[HistoricalVersion] = (),
    uuid_factory: Callable[[], object] = uuid4,
) -> dict:
    """构造新版清单，并按附录 B.4 的保守规则继承段 ID。"""
    old_rows = _segment_rows(old_content, old_manifest)
    cuts = _validated_cuts(new_content, suggested_cuts)
    if old_content == new_content:
        # 标题/图片/主旨变化不得改变正文清单，也不消耗新 UUID。
        result = json.loads(json.dumps(old_manifest))
        validate_manifest(new_content, result)
        return result
    if not new_content or not new_content.strip():
        raise ContentVersionError("正文不能是空白")

    blocks = block_provider(old_content, new_content)
    by_text: dict[str, list[dict]] = {}
    direct: dict[str, tuple[int, int]] = {}
    for row in old_rows:
        text = old_content[int(row["text_start"]) : int(row["text_end"])]
        by_text.setdefault(text, []).append(row)
        mapped = _mapped_interval(row, blocks)
        if mapped and new_content[mapped[0] : mapped[1]] == text:
            direct[str(row["segment_id"])] = mapped

    accepted: dict[str, tuple[int, int]] = {}
    for text, rows in by_text.items():
        ids = {str(row["segment_id"]) for row in rows}
        old_hits, new_hits = _occurrences(old_content, text), _occurrences(new_content, text)
        if len(old_hits) <= 1 and len(new_hits) <= 1:
            accepted.update({key: value for key, value in direct.items() if key in ids})
        elif (
            len(old_hits) == len(new_hits) == len(rows)
            and ids.issubset(direct)
            and len(set(direct[key] for key in ids)) == len(ids)
        ):
            accepted.update({key: direct[key] for key in ids})

    accepted = _cancel_overlaps(accepted)
    occupied = list(accepted.values())
    # 唯一、完整自然段移动；内部截出的长段不会通过自然边界检查。
    for text, rows in by_text.items():
        if len(rows) != 1:
            continue
        row = rows[0]
        segment_id = str(row["segment_id"])
        if segment_id in accepted:
            continue
        old_hits, new_hits = _occurrences(old_content, text), _occurrences(new_content, text)
        if len(old_hits) != 1 or len(new_hits) != 1:
            continue
        start, end = new_hits[0], new_hits[0] + len(text)
        if not _natural_paragraph(old_content, old_hits[0], old_hits[0] + len(text)):
            continue
        if not _natural_paragraph(new_content, start, end):
            continue
        if any(max(start, a) < min(end, b) for a, b in occupied):
            continue
        accepted[segment_id] = (start, end)
        occupied.append((start, end))

    accepted = _cancel_overlaps(accepted)
    occupied = list(accepted.values())

    # 历史恢复必须先于建议切段，否则一个建议大段可能吞掉多个可恢复的历史段。
    histories = list(historical_versions)
    ancestry = [HistoricalVersion(old_content, old_manifest), *histories]
    historical_by_text: dict[str, list[tuple[str, HistoricalVersion, int, int]]] = {}
    for version in ancestry:
        for row in _segment_rows(version.content, version.manifest):
            a, b = int(row["text_start"]), int(row["text_end"])
            historical_by_text.setdefault(version.content[a:b], []).append(
                (str(row["segment_id"]), version, a, b)
            )
    historical_candidates: dict[str, tuple[int, int]] = {}
    for text, occurrences in historical_by_text.items():
        distinct_ids = {segment_id for segment_id, _version, _a, _b in occurrences}
        if len(distinct_ids) != 1 or next(iter(distinct_ids)) in accepted:
            continue
        new_hits = _occurrences(new_content, text)
        if len(new_hits) != 1:
            continue
        # 任一祖先含多个同文出现位置就构成歧义证据，不能跳过该版本。
        source_versions = {id(version): version for _sid, version, _a, _b in occurrences}.values()
        if any(len(_occurrences(version.content, text)) != 1 for version in source_versions):
            continue
        if any(not _natural_paragraph(version.content, a, b) for _sid, version, a, b in occurrences):
            continue
        start, end = new_hits[0], new_hits[0] + len(text)
        if not _natural_paragraph(new_content, start, end):
            continue
        if any(max(start, a) < min(end, b) for a, b in occupied):
            continue
        historical_candidates[next(iter(distinct_ids))] = (start, end)
    historical_candidates = _cancel_overlaps(historical_candidates)
    accepted.update(historical_candidates)
    preserved = sorted((start, end, segment_id) for segment_id, (start, end) in accepted.items())

    # 保留展示区间覆盖建议切点；只在未保留间隙采用建议 cuts。
    display: list[tuple[int, int, str | None]] = []
    cursor = 0
    for start, end, segment_id in preserved:
        if cursor < start:
            display.extend((a, b, None) for a, b in _gap_intervals(new_content, cursor, start, cuts))
        display.append((start, end, segment_id))
        cursor = end
    if cursor < len(new_content):
        display.extend((a, b, None) for a, b in _gap_intervals(new_content, cursor, len(new_content), cuts))
    if not display:
        raise ContentVersionError("新清单不能没有展示段")

    # 如果建议切点落在纯空白中，间隙切片可能产生相邻/重叠展示，先做断言。
    for left, right in zip(display, display[1:]):
        if left[1] > right[0]:
            raise ContentVersionError("保留段与建议分段发生重叠")

    used_ids = {segment_id for _, _, segment_id in display if segment_id}
    forbidden_ids = {str(row["segment_id"]) for row in old_rows}
    for version in histories:
        for row in _segment_rows(version.content, version.manifest):
            forbidden_ids.add(str(row["segment_id"]))
    forbidden_ids.update(used_ids)

    ids: list[str] = []
    for start, end, inherited_id in display:
        if inherited_id:
            ids.append(inherited_id)
            continue
        chosen = _fresh_segment_id(uuid_factory, forbidden_ids)
        chosen = str(UUID(str(chosen)))
        ids.append(chosen)
        used_ids.add(chosen)
        forbidden_ids.add(chosen)

    # raw 分界放在后一展示段起点：文首空白归首段，段间空白归前段。
    raw_cuts = [start for start, _end, _id in display[1:]]
    result = build_manifest(new_content, raw_cuts, ids)
    for expected, row in zip(display, result["segments"]):
        if new_content[int(row["text_start"]) : int(row["text_end"])] != new_content[expected[0] : expected[1]]:
            raise ContentVersionError("重算 raw 后展示文字发生变化")
    return result


class BareContentRepository:
    """通过 Git plumbing 命令写入独立裸仓库；所有命令均不经 shell。"""

    FILES = ("content.txt", "meta.json", "segments.json")

    def __init__(self, path: str | os.PathLike[str], *, git_bin: str = "git"):
        self.path = Path(path)
        self.git_bin = git_bin

    def _run(
        self,
        args: Sequence[str],
        *,
        input_bytes: bytes | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        command = [self.git_bin, f"--git-dir={self.path}", *args]
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        try:
            result = subprocess.run(
                command,
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=merged_env,
                check=False,
            )
        except OSError as exc:
            raise GitStorageError(f"无法执行 Git: {exc}") from exc
        if check and result.returncode:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            raise GitStorageError(message or f"Git 命令失败: {args[0]}")
        return result

    def initialize(self) -> None:
        if not self.path.exists():
            result = subprocess.run(
                [self.git_bin, "init", "--bare", str(self.path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if result.returncode:
                raise GitStorageError(result.stderr.decode("utf-8", errors="replace").strip())
        self._run(["rev-parse", "--is-bare-repository"])
        for key, value in (("core.fsync", "all"), ("core.fsyncMethod", "fsync"), ("gc.auto", "0")):
            self._run(["config", key, value])
        # Mounted object storage must prove the operations Git relies on before
        # the worker accepts writes: durable object I/O plus lock/ref rename.
        probe = f"knowledge-distiller-storage-probe:{uuid4()}".encode("ascii")
        oid = self._oid(probe)
        restored = self._run(["cat-file", "blob", oid]).stdout
        if restored != probe:
            raise GitStorageError("Git存储自检失败：写入后的对象内容不一致")
        ref = f"refs/kd-storage-probes/{uuid4()}"
        self._run(["update-ref", ref, oid, self._zero_oid()])
        observed = self._run(["rev-parse", "--verify", ref]).stdout.decode().strip()
        if observed != oid:
            raise GitStorageError("Git存储自检失败：引用写入后无法读取")
        self._run(["update-ref", "-d", ref, oid])

    def _oid(self, data: bytes) -> str:
        return self._run(["hash-object", "-w", "--stdin"], input_bytes=data).stdout.decode().strip()

    def _expected_oid(self, data: bytes) -> str:
        return self._run(["hash-object", "--stdin"], input_bytes=data).stdout.decode().strip()

    def _object_format(self) -> str:
        result = self._run(["rev-parse", "--show-object-format"], check=False)
        return result.stdout.decode().strip() if result.returncode == 0 else "sha1"

    def _zero_oid(self) -> str:
        return "0" * (64 if self._object_format() == "sha256" else 40)

    def _validate_oid(self, oid: str, *, name: str = "commit") -> str:
        length = len(self._zero_oid())
        if not isinstance(oid, str) or not re.fullmatch(rf"[0-9a-f]{{{length}}}", oid):
            raise ContentVersionError(f"{name} 必须是完整小写 Git 对象 ID")
        return oid

    def git_unchanged_blocks(self, old: str, new: str) -> list[tuple[int, int, int]]:
        """用规范固定的 Git Myers 参数取 hunk，再以 difflib 精化。"""
        old_oid = self._oid(old.encode("utf-8"))
        new_oid = self._oid(new.encode("utf-8"))
        result = self._run(
            [
                "diff", "--unified=0", "--diff-algorithm=myers", "--no-indent-heuristic",
                "--no-renames", "--no-color", "--no-ext-diff", "--no-textconv", "--text",
                old_oid, new_oid,
            ],
            check=False,
        )
        if result.returncode not in (0, 1):
            message = result.stderr.decode("utf-8", errors="replace").strip()
            raise GitStorageError(message or "Git diff 失败")
        return _git_hunk_blocks(old, new, result.stdout.decode("utf-8", errors="strict"))

    @staticmethod
    def candidate_ref(article_key: str, update_id: str) -> str:
        try:
            update = str(UUID(str(update_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ContentVersionError("update_id 必须是 UUID") from exc
        return f"refs/tags/articles/{article_digest(article_key)}/{update}"

    @staticmethod
    def branch_ref(article_key: str) -> str:
        return f"refs/heads/articles/{article_digest(article_key)}"

    def get_ref(self, ref: str) -> str | None:
        result = self._run(["rev-parse", "--verify", ref], check=False)
        return result.stdout.decode().strip() if result.returncode == 0 else None

    def _commit_parent(self, commit: str) -> str | None:
        line = self._run(["rev-list", "--parents", "-n", "1", commit]).stdout.decode().strip()
        parts = line.split()
        return parts[1] if len(parts) > 1 else None

    def _tree_blob_oids(self, commit: str) -> dict[str, str]:
        lines = self._run(["ls-tree", commit]).stdout.decode("utf-8").splitlines()
        entries: dict[str, str] = {}
        for line in lines:
            try:
                left, name = line.split("\t", 1)
                mode, kind, oid = left.split(" ")
            except ValueError as exc:
                raise GitStorageError("Git tree 输出格式无效") from exc
            if mode != "100644" or kind != "blob" or name not in self.FILES or name in entries:
                raise GitStorageError("Git 版本必须且只能包含三个普通 blob")
            entries[name] = oid
        if tuple(sorted(entries)) != self.FILES:
            raise GitStorageError("Git 版本必须且只能包含固定三个文件")
        return entries

    def read_version(self, commit: str) -> StoredVersion:
        commit = self._validate_oid(commit)
        self._tree_blob_oids(commit)
        files: dict[str, bytes] = {}
        for name in self.FILES:
            result = self._run(["show", f"{commit}:{name}"], check=False)
            if result.returncode:
                raise GitStorageError(f"提交 {commit} 缺少 {name}")
            files[name] = result.stdout
        try:
            content = files["content.txt"].decode("utf-8")
            meta = json.loads(files["meta.json"].decode("utf-8"))
            manifest = json.loads(files["segments.json"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitStorageError("Git 版本文件不是合法 UTF-8/JSON") from exc
        validate_manifest(content, manifest)
        return StoredVersion(commit, content, meta, manifest, self._commit_parent(commit))

    def create_version(
        self,
        *,
        article_key: str,
        update_id: str,
        content: str,
        meta: Mapping[str, object],
        manifest: Mapping[str, object],
        parent: str | None = None,
        committed_at: datetime | str,
    ) -> str:
        validate_manifest(content, manifest)
        ref = self.candidate_ref(article_key, update_id)
        checked_meta = dict(meta)
        checked_meta.setdefault("schema_version", 1)
        checked_meta.setdefault("article_key", article_key)
        checked_meta.setdefault("update_id", str(UUID(str(update_id))))
        if type(checked_meta.get("schema_version")) is not int or checked_meta.get("schema_version") != 1:
            raise ContentVersionError("不支持的 meta schema_version")
        if checked_meta.get("article_key") != article_key or checked_meta.get("update_id") != str(UUID(str(update_id))):
            raise ContentVersionError("meta 与候选身份不一致")
        forbidden = {"commit", "commit_hash", "candidate_commit"}.intersection(checked_meta)
        if forbidden:
            raise ContentVersionError("meta 不能包含自身 commit")
        missing = {"url", "title", "images", "summary"}.difference(checked_meta)
        if missing:
            raise ContentVersionError(f"meta 缺少字段: {','.join(sorted(missing))}")
        if not all(isinstance(checked_meta[key], str) for key in ("url", "title", "summary")):
            raise ContentVersionError("meta 的 url/title/summary 必须是字符串")
        if not isinstance(checked_meta["images"], list):
            raise ContentVersionError("meta 的 images 必须是数组")

        if parent is not None:
            parent = self._validate_oid(parent, name="parent")
        blobs = {
            "content.txt": content.encode("utf-8"),
            "meta.json": canonical_json_bytes(checked_meta),
            "segments.json": manifest_bytes(manifest),
        }

        existing = self.get_ref(ref)
        if existing is not None:
            expected_oids = {name: self._expected_oid(data) for name, data in blobs.items()}
            if (
                self._tree_blob_oids(existing) == expected_oids
                and self._commit_parent(existing) == parent
            ):
                return existing
            raise GitRefConflict("GIT_CANDIDATE_CONFLICT")

        tree_input = b"".join(
            f"100644 blob {self._oid(blobs[name])}\t{name}\n".encode("ascii") for name in self.FILES
        )
        tree = self._run(["mktree"], input_bytes=tree_input).stdout.decode().strip()
        instant = committed_at
        if isinstance(instant, datetime):
            if instant.tzinfo is None:
                raise ContentVersionError("committed_at 必须带时区")
            date = instant.isoformat()
        elif isinstance(instant, str) and instant:
            try:
                parsed = datetime.fromisoformat(instant.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ContentVersionError("committed_at 必须是 ISO 8601 时间") from exc
            if parsed.tzinfo is None:
                raise ContentVersionError("committed_at 必须带时区")
            date = parsed.isoformat()
        else:
            raise ContentVersionError("committed_at 无效")
        env = {
            "GIT_AUTHOR_NAME": "Knowledge Distiller",
            "GIT_AUTHOR_EMAIL": "content@localhost",
            "GIT_COMMITTER_NAME": "Knowledge Distiller",
            "GIT_COMMITTER_EMAIL": "content@localhost",
            "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_DATE": date,
        }
        args = ["commit-tree", tree]
        if parent:
            args.extend(["-p", parent])
        commit = self._run(args, input_bytes=f"content update {update_id}\n".encode(), env=env).stdout.decode().strip()

        created = self._run(["update-ref", ref, commit, self._zero_oid()], check=False)
        if created.returncode:
            existing = self.get_ref(ref)
            if existing == commit:
                return commit
            raise GitRefConflict("GIT_CANDIDATE_CONFLICT")
        return commit

    def update_branch(self, article_key: str, commit: str, expected: str | None) -> None:
        commit = self._validate_oid(commit)
        if expected is not None:
            expected = self._validate_oid(expected, name="expected")
        old = expected or self._zero_oid()
        result = self._run(["update-ref", self.branch_ref(article_key), commit, old], check=False)
        if result.returncode:
            raise GitRefConflict("文章分支基线已变化")

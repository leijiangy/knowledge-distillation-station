# -*- coding: utf-8 -*-
"""自测：题目规整（丢弃残缺题）与作答记录的折叠"""
import pytest

from core.ai import _normalize_questions
from core.reading_store import collapse_attempts, load_questions


def judgment(stem="陈述", answer=True, explanation="解析", **kw) -> dict:
    return {"kind": "judgment", "stem": stem, "answer": answer,
            "explanation": explanation, **kw}


def choice(answer=1, options=None, **kw) -> dict:
    return {"kind": "choice", "stem": "题干",
            "options": options or ["A", "B", "C", "D"],
            "answer": answer, "explanation": "解析", **kw}


# ---------- 题目规整 ----------

def test_judgment_kept():
    qs = _normalize_questions([judgment()], 5)["questions"]
    assert len(qs) == 1
    assert qs[0]["answer"] is True


def test_judgment_string_answer_coerced():
    qs = _normalize_questions([judgment(answer="对")], 5)["questions"]
    assert qs[0]["answer"] is True


def test_judgment_non_bool_answer_dropped():
    """answer 是数字、字符串乱写等一律丢弃——宁可少出一题，不出错题"""
    assert _normalize_questions([judgment(answer="不确定")], 5)["questions"] == []


def test_choice_kept():
    qs = _normalize_questions([choice(answer=2)], 5)["questions"]
    assert qs[0]["answer"] == 2
    assert len(qs[0]["options"]) == 4


def test_choice_wrong_option_count_dropped():
    assert _normalize_questions([choice(options=["A", "B", "C"])], 5)["questions"] == []


def test_choice_out_of_range_answer_dropped():
    assert _normalize_questions([choice(answer=4)], 5)["questions"] == []
    assert _normalize_questions([choice(answer=-1)], 5)["questions"] == []


def test_choice_bool_answer_dropped():
    """True 在 Python 里是 int，但不能当成下标 1"""
    assert _normalize_questions([choice(answer=True)], 5)["questions"] == []


def test_missing_explanation_dropped():
    assert _normalize_questions([judgment(explanation="")], 5)["questions"] == []


def test_missing_stem_dropped():
    assert _normalize_questions([judgment(stem="  ")], 5)["questions"] == []


def test_unknown_kind_dropped():
    assert _normalize_questions([judgment(kind="fill")], 5)["questions"] == []


def test_unknown_focus_falls_back_to_knowledge():
    qs = _normalize_questions([judgment(focus="记忆")], 5)["questions"]
    assert qs[0]["focus"] == "knowledge"


def test_mixed_list_keeps_only_valid():
    raw = [judgment(), choice(options=["A"]), choice(), "不是字典", judgment(answer="？")]
    qs = _normalize_questions(raw, 5)["questions"]
    assert len(qs) == 2


def test_count_limit_respected():
    raw = [judgment(stem=f"第{i}题") for i in range(10)]
    assert len(_normalize_questions(raw, 3)["questions"]) == 3


def test_empty_input():
    assert _normalize_questions(None, 5)["questions"] == []
    assert _normalize_questions([], 5)["questions"] == []


# ---------- 题目读取（容忍损坏） ----------

def test_load_questions_from_text():
    assert load_questions({"questions": '[{"kind": "judgment"}]'}) == [{"kind": "judgment"}]


def test_load_questions_from_list_passthrough():
    assert load_questions({"questions": [{"kind": "choice"}]}) == [{"kind": "choice"}]


def test_load_questions_broken_text_is_empty():
    assert load_questions({"questions": "{不是数组}"}) == []
    assert load_questions({"questions": "no json here"}) == []
    assert load_questions({}) == []
    assert load_questions(None) == []


# ---------- 作答记录折叠 ----------

def attempt(idx: int, at: int, correct: bool, chosen: str = "") -> dict:
    return {"q_index": idx, "at": at, "correct": correct, "chosen": chosen}


def test_latest_attempt_wins():
    got = collapse_attempts([attempt(0, 100, False), attempt(0, 200, True)])
    assert got[0]["correct"] is True


def test_same_timestamp_later_row_wins():
    """同一秒内的两次作答（时间戳相同）也要取后写的那条"""
    got = collapse_attempts([attempt(0, 100, False), attempt(0, 100, True)])
    assert got[0]["correct"] is True


def test_distinct_questions_kept_separately():
    got = collapse_attempts([attempt(0, 100, True), attempt(1, 200, False)])
    assert set(got) == {0, 1}


def test_bad_index_ignored():
    got = collapse_attempts([attempt(0, 100, True), {"at": 1}, {"q_index": "x"}])
    assert set(got) == {0}


def test_empty_attempts():
    assert collapse_attempts([]) == {}
    assert collapse_attempts(None) == {}


def test_quiz_public_hides_answers():
    """给前端的题面绝不能带答案与解析"""
    from main import _quiz_public

    questions = [{"kind": "judgment", "focus": "knowledge", "stem": "陈述",
                  "answer": True, "explanation": "因为如此"},
                 {"kind": "choice", "focus": "concept", "stem": "问题",
                  "options": ["A", "B", "C", "D"], "answer": 2, "explanation": "因为如此"}]
    out = _quiz_public(questions, {})
    assert out[0] == {"index": 0, "kind": "judgment", "focus": "knowledge", "stem": "陈述"}
    assert out[1]["options"] == ["A", "B", "C", "D"]
    for item in out:
        assert "answer" not in item
        assert "explanation" not in item


@pytest.mark.parametrize("row", [
    {"kind": "judgment", "stem": "s"},                       # 没有 answer
    {"kind": "choice", "stem": "s", "options": ["A"]},        # 选项数不对
])
def test_quiz_public_survives_bad_rows(row):
    """残缺的题（字段不全）仍可展示题面——答案本来就不下发"""
    from main import _quiz_public

    out = _quiz_public([row], {})
    assert len(out) == 1
    assert "answer" not in out[0] and "explanation" not in out[0]


@pytest.mark.parametrize("row", ["坏数据", None, 42, ["嵌套"]])
def test_quiz_public_drops_non_dict_rows(row):
    """不是字典的条目直接丢掉，不能让整个列表崩"""
    from main import _quiz_public

    assert _quiz_public([row], {}) == []


def test_quiz_public_marks_answered():
    from main import _quiz_public

    out = _quiz_public([{"kind": "judgment", "stem": "s"}], {0: {"correct": True, "chosen": "对"}})
    assert out[0]["done"] is True and out[0]["correct"] is True


# ---------- 复习：薄弱点收集 ----------

from core.reading_store import collect_weak_points


def jq(stem="陈述", answer=True):
    return {"kind": "judgment", "stem": stem, "answer": answer}


def cq(stem="问题", answer=1, options=None):
    return {"kind": "choice", "stem": stem, "options": options or ["A", "B", "C", "D"],
            "answer": answer}


def done(correct: bool, chosen: str = "x"):
    return {"correct": correct, "chosen": chosen, "at": 1}


def test_correct_answer_is_not_weak():
    """这轮答对了说明会了，不进薄弱点"""
    assert collect_weak_points([jq()], {0: done(True)}) == []


def test_wrong_answer_is_weak():
    got = collect_weak_points([jq()], {0: done(False, "错")})
    assert len(got) == 1
    assert got[0]["skipped"] is False
    assert got[0]["answer"] == "对"


def test_skipped_question_is_weak():
    """跳过 = 用户不懂，没作答记录的题也算薄弱点"""
    got = collect_weak_points([jq()], {})
    assert len(got) == 1 and got[0]["skipped"] is True


def test_mixed_keeps_only_weak():
    questions = [jq(stem="a"), cq(stem="b"), jq(stem="c")]
    attempts = {0: done(True), 1: done(False, "A")}      # c 跳过
    got = collect_weak_points(questions, attempts)
    assert [w["stem"] for w in got] == ["b", "c"]


def test_choice_weak_point_shows_option_text_not_index():
    got = collect_weak_points([cq(answer=2)], {0: done(False, "A")})
    assert got[0]["answer"] == "C"        # 不是下标 2


def test_chosen_preserved():
    got = collect_weak_points([cq()], {0: done(False, "我选的这个")})
    assert got[0]["chosen"] == "我选的这个"


def test_judgment_answer_text():
    assert collect_weak_points([jq(answer=True)], {})[0]["answer"] == "对"
    assert collect_weak_points([jq(answer=False)], {})[0]["answer"] == "错"


def test_bad_question_skipped():
    got = collect_weak_points(["坏数据", None], {})
    assert got == []


def test_empty_inputs():
    assert collect_weak_points([], {}) == []
    assert collect_weak_points(None, None) == []


def test_bad_choice_answer_index():
    """答案下标越界时不给文字，但题仍记为薄弱点"""
    got = collect_weak_points([{"kind": "choice", "stem": "s",
                                "options": ["A"], "answer": 9}], {})
    assert len(got) == 1 and got[0]["answer"] == ""

# -*- coding: utf-8 -*-
"""OAuth state 校验逻辑测试（四态：missing / mismatch / verified / unverified）"""
from core.oauth import check_state

STATE = "a-secure-random-state"


def test_missing_expected_rejects():
    """未发起过登录（session.state 缺失）→ 必须拒绝"""
    assert check_state("whatever", None) == "missing"
    assert check_state(None, None) == "missing"


def test_mismatch_rejects():
    assert check_state("attacker-state", STATE) == "mismatch"
    assert check_state("", STATE) == "unverified"  # 空串按未回传处理（官方容忍路径）
    assert check_state("A" * 24, STATE) == "mismatch"


def test_verified_passes():
    assert check_state(STATE, STATE) == "verified"


def test_unverified_tolerated_but_marked():
    """官方实测回调可能不回传 state：容忍（仅联调），调用方需标记未验证"""
    assert check_state(None, STATE) == "unverified"

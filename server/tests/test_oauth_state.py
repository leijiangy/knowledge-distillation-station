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


def test_local_path_only_accepts_site_relative_paths():
    """授权后落回的地址必须是站内相对路径，防止开放重定向"""
    from core.oauth import local_path
    assert local_path("/reading.html?url=https%3A%2F%2Fzhuanlan.zhihu.com%2Fp%2F1") \
        == "/reading.html?url=https%3A%2F%2Fzhuanlan.zhihu.com%2Fp%2F1"
    assert local_path("/?saved=x") == "/?saved=x"
    # 反斜杠用例用 chr(92) 拼：避免各层转义把它变成退格字符
    backslash_path = "/a" + chr(92) + "b"
    for bad in ("//evil.com", "https://evil.com", "http://evil.com/x", backslash_path,
                "javascript:alert(1)", "", None, "reading.html"):
        assert local_path(bad) == "", f"不应接受：{bad!r}"
    assert len(local_path("/" + "a" * 500)) == 300

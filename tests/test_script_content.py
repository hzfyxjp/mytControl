"""脚本文案本地配置：解析与缺字段（不请求 get.php / get2.php）。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.database import Database
from app.xhs_ext import (
    ExtApiError,
    ScriptContent,
    comment_lines_from_local,
    get2_from_local,
)


def test_comment_lines_keep_spaces_and_need_three_parts():
    parts = comment_lines_from_local("  a  |  b  |c")
    assert parts == ["  a  ", "  b  ", "c"]
    with pytest.raises(ExtApiError, match="不足 3 段"):
        comment_lines_from_local("只有一段|两段")


def test_comment_lines_allow_emoji():
    parts = comment_lines_from_local("💛嘻|💕哈|💙嘿")
    assert parts[0] == "💛嘻"
    assert len(parts) >= 3


def test_get2_from_local_preserves_spaces_and_emoji():
    cfg = ScriptContent(
        join_title="  uid  ",
        edit_text="💛 |  移步  |尾",
        edit_tags=" tag ",
    )
    payload = get2_from_local(cfg)
    assert payload["title"] == "  uid  "
    assert payload["text"] == "💛 |  移步  |尾"
    assert payload["tags"] == " tag "


def test_get2_empty_title_is_empty_string():
    payload = get2_from_local(ScriptContent())
    assert payload["title"] == ""
    assert payload["text"] == ""
    assert payload["tags"] == ""


def test_database_script_content_roundtrip_keeps_spaces_and_emoji():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.sqlite3")
        try:
            db.set_script_content(
                comment_content=" a | b | c ",
                join_title="  id1  ",
                edit_text="💛❤️ |  空格  |尾",
                edit_tags="  tags  ",
            )
            got = db.get_script_content()
            assert got["comment_content"] == " a | b | c "
            assert got["join_title"] == "  id1  "
            assert got["edit_text"] == "💛❤️ |  空格  |尾"
            assert got["edit_tags"] == "  tags  "
        finally:
            db.close()


def test_script_content_from_dict():
    cfg = ScriptContent.from_dict(
        {
            "comment_content": "1|2|3",
            "join_title": "t",
            "edit_text": "x",
            "edit_tags": "g",
        }
    )
    assert cfg.to_dict()["join_title"] == "t"

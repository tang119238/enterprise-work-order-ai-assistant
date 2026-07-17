from pathlib import Path

from app.knowledge.loader import load_policy_directory


def test_loader_keeps_document_and_section_identity(tmp_path: Path) -> None:
    policy = tmp_path / "policy.md"
    policy.write_text(
        "<!-- document_id: rework-policy -->\n"
        "# 返工规则\n"
        "## 3.2 返工链路\n"
        "返工单必须关联根工单。",
        encoding="utf-8",
    )

    chunks = load_policy_directory(tmp_path)

    assert chunks[0].document_id == "rework-policy"
    assert chunks[0].title == "返工规则"
    assert chunks[0].section == "3.2 返工链路"
    assert chunks[0].text == "返工单必须关联根工单。"


def test_loader_rejects_policy_without_document_id(tmp_path: Path) -> None:
    (tmp_path / "invalid.md").write_text("# 缺少编号\n## 规则\n内容", encoding="utf-8")

    try:
        load_policy_directory(tmp_path)
    except ValueError as error:
        assert "document_id" in str(error)
    else:
        raise AssertionError("missing document_id must be rejected")


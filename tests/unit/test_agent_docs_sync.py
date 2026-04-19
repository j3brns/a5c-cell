from pathlib import Path


def test_agent_docs_point_to_claude():
    repo_root = Path(__file__).resolve().parents[2]
    agents = (repo_root / "AGENTS.md").read_text(encoding="utf-8")
    gemini = (repo_root / "GEMINI.md").read_text(encoding="utf-8")
    claude = (repo_root / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Read [CLAUDE.md](CLAUDE.md)" in agents
    assert "Read [CLAUDE.md](CLAUDE.md)" in gemini
    assert agents == gemini
    assert agents != claude


def test_ai_context_directory_is_retired():
    repo_root = Path(__file__).resolve().parents[2]
    assert not (repo_root / "ai-context").exists()


def test_gitlab_issue_template_matches_canonical_issue_sections():
    repo_root = Path(__file__).resolve().parents[2]
    template = (repo_root / ".gitlab" / "issue_templates" / "Task.md").read_text(encoding="utf-8")

    assert "Apply labels: type:task, status:not-started." in template
    assert "Seq:" in template
    assert "Depends on: none" in template
    assert "## Problem" in template
    assert "## Scope" in template
    assert "## Acceptance Criteria" in template
    assert "## Test Plan" in template
    assert "## Definition of Done" in template

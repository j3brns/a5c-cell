from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_codex_flow_module():
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "codex_flow_cli", repo_root / "scripts" / "codex_flow" / "cli.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


codex_flow = _load_codex_flow_module()


def test_default_board_has_expected_lanes():
    board = codex_flow.default_board()

    assert board["lanes"] == ["backlog", "next", "doing", "blocked", "done"]
    assert board["cards"] == []
    assert board["next_id"] == 1


def test_append_card_persists_roundtrip(tmp_path):
    root = tmp_path
    board = codex_flow.default_board()
    card = codex_flow.Card(
        id=codex_flow.next_card_id(board),
        title="split bridge auth",
        lane="next",
        owner="codex",
        role="implement",
        issue=388,
        worktree_path=None,
        branch=None,
        note="",
        updated_at=codex_flow.iso_now(),
    )

    codex_flow.append_card(board, card)
    codex_flow.save_board(root, board)
    loaded = codex_flow.load_board(root)
    cards = codex_flow.board_cards(loaded)

    assert len(cards) == 1
    assert cards[0].title == "split bridge auth"
    assert cards[0].lane == "next"


def test_find_and_replace_card_updates_lane(tmp_path):
    root = tmp_path
    board = codex_flow.default_board()
    card = codex_flow.Card(
        id=1,
        title="review authoriser",
        lane="backlog",
        owner=None,
        role=None,
        issue=None,
        worktree_path=None,
        branch=None,
        note="",
        updated_at=codex_flow.iso_now(),
    )
    codex_flow.append_card(board, card)

    index, existing = codex_flow.find_card(board, 1)
    updated = codex_flow.Card(
        id=existing.id,
        title=existing.title,
        lane="doing",
        owner="gemini",
        role="review",
        issue=existing.issue,
        worktree_path=existing.worktree_path,
        branch=existing.branch,
        note="active",
        updated_at=codex_flow.iso_now(),
    )
    codex_flow.replace_card(board, index, updated)
    codex_flow.save_board(root, board)

    loaded_card = codex_flow.board_cards(codex_flow.load_board(root))[0]
    assert loaded_card.lane == "doing"
    assert loaded_card.owner == "gemini"
    assert loaded_card.role == "review"


def test_notes_path_is_scoped_under_local_state(tmp_path):
    root = tmp_path
    path = codex_flow.notes_path(root, 7)

    assert path == root / ".codex-flow" / "notes" / "card-7.md"


def test_import_github_issues_adds_only_missing_issue_cards():
    board = codex_flow.default_board()
    codex_flow.append_card(
        board,
        codex_flow.Card(
            id=1,
            title="existing",
            lane="backlog",
            owner=None,
            role=None,
            issue=388,
            worktree_path=None,
            branch=None,
            note="",
            updated_at=codex_flow.iso_now(),
        ),
    )

    added = codex_flow.import_github_issues(
        board,
        [
            {"number": 388, "title": "existing"},
            {"number": 389, "title": "new issue"},
        ],
        lane="backlog",
    )

    cards = codex_flow.board_cards(board)
    assert added == 1
    assert [card.issue for card in cards] == [388, 389]

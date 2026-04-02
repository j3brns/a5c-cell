import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOCAL_STATE_DIR = Path(".codex-flow")
BOARD_FILE = LOCAL_STATE_DIR / "board.json"
NOTES_DIR = LOCAL_STATE_DIR / "notes"
DEFAULT_LANES = ["backlog", "next", "doing", "blocked", "done"]
DEFAULT_OWNERS = ("codex", "gemini", "manual")
DEFAULT_ROLES = ("implement", "review", "plan")
DEFAULT_IMPORT_LIMIT = 200


@dataclass(slots=True)
class Card:
    id: int
    title: str
    lane: str
    owner: str | None
    role: str | None
    issue: int | None
    worktree_path: str | None
    branch: str | None
    note: str
    updated_at: str


def repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip()).resolve()


def iso_now() -> str:
    return datetime.now(UTC).isoformat()


def load_json_file(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def default_board() -> dict[str, Any]:
    return {
        "lanes": list(DEFAULT_LANES),
        "cards": [],
        "next_id": 1,
        "updated_at": iso_now(),
    }


def load_board(root: Path) -> dict[str, Any]:
    board = load_json_file(root / BOARD_FILE, default=default_board())
    if not isinstance(board, dict):
        return default_board()
    lanes = board.get("lanes")
    cards = board.get("cards")
    next_id = board.get("next_id")
    if not isinstance(lanes, list) or not isinstance(cards, list) or not isinstance(next_id, int):
        return default_board()
    return board


def save_board(root: Path, board: dict[str, Any]) -> None:
    board["updated_at"] = iso_now()
    save_json_file(root / BOARD_FILE, board)


def normalize_card(payload: dict[str, Any]) -> Card:
    return Card(
        id=int(payload["id"]),
        title=str(payload.get("title") or ""),
        lane=str(payload.get("lane") or "backlog"),
        owner=str(payload["owner"]) if payload.get("owner") is not None else None,
        role=str(payload["role"]) if payload.get("role") is not None else None,
        issue=int(payload["issue"]) if payload.get("issue") is not None else None,
        worktree_path=(
            str(payload["worktree_path"]) if payload.get("worktree_path") is not None else None
        ),
        branch=str(payload["branch"]) if payload.get("branch") is not None else None,
        note=str(payload.get("note") or ""),
        updated_at=str(payload.get("updated_at") or ""),
    )


def card_to_payload(card: Card) -> dict[str, Any]:
    return {
        "id": card.id,
        "title": card.title,
        "lane": card.lane,
        "owner": card.owner,
        "role": card.role,
        "issue": card.issue,
        "worktree_path": card.worktree_path,
        "branch": card.branch,
        "note": card.note,
        "updated_at": card.updated_at,
    }


def board_cards(board: dict[str, Any]) -> list[Card]:
    return [normalize_card(card) for card in board.get("cards", []) if isinstance(card, dict)]


def ensure_lane(board: dict[str, Any], lane: str) -> None:
    lanes = board.get("lanes", [])
    if lane not in lanes:
        raise SystemExit(f"Unknown lane '{lane}'. Valid lanes: {', '.join(lanes)}")


def find_card(board: dict[str, Any], card_id: int) -> tuple[int, Card]:
    for idx, payload in enumerate(board.get("cards", [])):
        if isinstance(payload, dict) and int(payload.get("id", -1)) == card_id:
            return idx, normalize_card(payload)
    raise SystemExit(f"Card {card_id} was not found.")


def next_card_id(board: dict[str, Any]) -> int:
    current = int(board.get("next_id", 1))
    board["next_id"] = current + 1
    return current


def append_card(board: dict[str, Any], card: Card) -> None:
    board.setdefault("cards", []).append(card_to_payload(card))


def replace_card(board: dict[str, Any], index: int, card: Card) -> None:
    board["cards"][index] = card_to_payload(card)


def notes_path(root: Path, card_id: int) -> Path:
    return root / NOTES_DIR / f"card-{card_id}.md"


def detect_git_context(path: Path) -> tuple[str | None, str | None]:
    try:
        root_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return None, None
    worktree_path = Path(root_result.stdout.strip()).resolve()
    branch = branch_result.stdout.strip()
    return str(worktree_path), branch


def github_repo_slug(root: Path) -> str:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    raw = result.stdout.strip()
    if raw.endswith(".git"):
        raw = raw[:-4]
    if raw.startswith("git@github.com:"):
        return raw.split(":", 1)[1]
    if "github.com/" in raw:
        return raw.split("github.com/", 1)[1]
    raise SystemExit(f"Could not derive GitHub repo slug from origin URL: {raw}")


def fetch_github_issues(root: Path, *, state: str, limit: int) -> list[dict[str, Any]]:
    repo = github_repo_slug(root)
    command = [
        "gh",
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        state,
        "--limit",
        str(limit),
        "--json",
        "number,title,state,labels",
    ]
    result = subprocess.run(command, cwd=root, check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    if not isinstance(payload, list):
        raise SystemExit("Unexpected gh issue list payload.")
    return [item for item in payload if isinstance(item, dict)]


def import_github_issues(
    board: dict[str, Any],
    issues: list[dict[str, Any]],
    *,
    lane: str,
) -> int:
    ensure_lane(board, lane)
    existing_issue_numbers = {
        card.issue for card in board_cards(board) if card.issue is not None
    }
    added = 0
    for issue in issues:
        issue_number = issue.get("number")
        title = issue.get("title")
        if not isinstance(issue_number, int) or not isinstance(title, str):
            continue
        if issue_number in existing_issue_numbers:
            continue
        card = Card(
            id=next_card_id(board),
            title=title,
            lane=lane,
            owner=None,
            role=None,
            issue=issue_number,
            worktree_path=None,
            branch=None,
            note="imported from GitHub issue",
            updated_at=iso_now(),
        )
        append_card(board, card)
        existing_issue_numbers.add(issue_number)
        added += 1
    return added


def cmd_init(args: argparse.Namespace) -> int:
    root = repo_root()
    board_path = root / BOARD_FILE
    if board_path.exists() and not args.force:
        raise SystemExit(f"{board_path} already exists. Use --force to overwrite it.")
    save_board(root, default_board())
    print(board_path)
    return 0


def cmd_board(_args: argparse.Namespace) -> int:
    root = repo_root()
    board = load_board(root)
    cards = board_cards(board)
    if not cards:
        print("Kanban board is empty.")
        return 0

    print("Codex Flow Kanban")
    for lane in board.get("lanes", DEFAULT_LANES):
        lane_cards = [card for card in cards if card.lane == lane]
        print(f"\n[{lane}]")
        if not lane_cards:
            print("  -")
            continue
        for card in lane_cards:
            owner = card.owner or "-"
            role = card.role or "-"
            issue = f" issue=#{card.issue}" if card.issue is not None else ""
            print(f"  {card.id}. {card.title} owner={owner} role={role}{issue}")
            if card.worktree_path:
                print(f"     wt={card.worktree_path}")
            if card.note:
                print(f"     note={card.note}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    root = repo_root()
    board = load_board(root)
    ensure_lane(board, args.lane)
    card = Card(
        id=next_card_id(board),
        title=args.title,
        lane=args.lane,
        owner=args.owner,
        role=args.role,
        issue=args.issue,
        worktree_path=args.worktree,
        branch=args.branch,
        note=args.note or "",
        updated_at=iso_now(),
    )
    append_card(board, card)
    save_board(root, board)
    print(card.id)
    return 0


def cmd_move(args: argparse.Namespace) -> int:
    root = repo_root()
    board = load_board(root)
    ensure_lane(board, args.lane)
    index, card = find_card(board, args.card)
    updated = Card(
        id=card.id,
        title=card.title,
        lane=args.lane,
        owner=card.owner,
        role=card.role,
        issue=card.issue,
        worktree_path=card.worktree_path,
        branch=card.branch,
        note=args.note if args.note is not None else card.note,
        updated_at=iso_now(),
    )
    replace_card(board, index, updated)
    save_board(root, board)
    return 0


def cmd_assign(args: argparse.Namespace) -> int:
    root = repo_root()
    board = load_board(root)
    index, card = find_card(board, args.card)
    updated = Card(
        id=card.id,
        title=card.title,
        lane=card.lane,
        owner=args.owner,
        role=args.role,
        issue=card.issue,
        worktree_path=card.worktree_path,
        branch=card.branch,
        note=args.note if args.note is not None else card.note,
        updated_at=iso_now(),
    )
    replace_card(board, index, updated)
    save_board(root, board)
    return 0


def cmd_attach(args: argparse.Namespace) -> int:
    root = repo_root()
    board = load_board(root)
    index, card = find_card(board, args.card)

    worktree_path = args.path
    branch = args.branch
    if worktree_path is None or branch is None:
        detected_path, detected_branch = detect_git_context(Path.cwd())
        if worktree_path is None:
            worktree_path = detected_path
        if branch is None:
            branch = detected_branch

    updated = Card(
        id=card.id,
        title=card.title,
        lane=card.lane,
        owner=card.owner,
        role=card.role,
        issue=args.issue if args.issue is not None else card.issue,
        worktree_path=worktree_path,
        branch=branch,
        note=args.note if args.note is not None else card.note,
        updated_at=iso_now(),
    )
    replace_card(board, index, updated)
    save_board(root, board)
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    root = repo_root()
    board = load_board(root)
    _index, card = find_card(board, args.card)
    path = notes_path(root, card.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            (
                f"# Card {card.id}: {card.title}\n\n"
                f"Lane: {card.lane}\n"
                f"Owner: {card.owner or '-'}\n"
                f"Role: {card.role or '-'}\n"
                f"Issue: {card.issue or '-'}\n"
                f"Worktree: {card.worktree_path or '-'}\n\n"
                "## Context\n\n"
                "## Next step\n\n"
                "## Handoff\n"
            ),
            encoding="utf-8",
        )
    print(path)
    return 0


def cmd_import_github(args: argparse.Namespace) -> int:
    root = repo_root()
    board = load_board(root)
    issues = fetch_github_issues(root, state=args.state, limit=args.limit)
    added = import_github_issues(board, issues, lane=args.lane)
    save_board(root, board)
    print(f"Imported {added} issues into lane '{args.lane}'.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.codex_flow",
        description="Separate local kanban for Codex/Gemini work without issue-label coupling.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Create a fresh local kanban board.")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    board = subparsers.add_parser("board", help="Print the local kanban board.")
    board.set_defaults(func=cmd_board)

    add = subparsers.add_parser("add", help="Add a new card to the local board.")
    add.add_argument("title")
    add.add_argument("--lane", default="backlog")
    add.add_argument("--owner", choices=DEFAULT_OWNERS)
    add.add_argument("--role", choices=DEFAULT_ROLES)
    add.add_argument("--issue", type=int)
    add.add_argument("--worktree")
    add.add_argument("--branch")
    add.add_argument("--note")
    add.set_defaults(func=cmd_add)

    move = subparsers.add_parser("move", help="Move a card to another lane.")
    move.add_argument("card", type=int)
    move.add_argument("--lane", required=True)
    move.add_argument("--note")
    move.set_defaults(func=cmd_move)

    assign = subparsers.add_parser("assign", help="Assign a card owner and role.")
    assign.add_argument("card", type=int)
    assign.add_argument("--owner", required=True, choices=DEFAULT_OWNERS)
    assign.add_argument("--role", required=True, choices=DEFAULT_ROLES)
    assign.add_argument("--note")
    assign.set_defaults(func=cmd_assign)

    attach = subparsers.add_parser(
        "attach",
        help="Attach optional issue/worktree metadata to a card.",
    )
    attach.add_argument("card", type=int)
    attach.add_argument("--issue", type=int)
    attach.add_argument("--path")
    attach.add_argument("--branch")
    attach.add_argument("--note")
    attach.set_defaults(func=cmd_attach)

    note = subparsers.add_parser("note", help="Create or print a per-card note file.")
    note.add_argument("card", type=int)
    note.set_defaults(func=cmd_note)

    import_github = subparsers.add_parser(
        "import-github",
        help="Import GitHub issues into the local board as cards.",
    )
    import_github.add_argument("--state", default="open", choices=("open", "closed", "all"))
    import_github.add_argument("--limit", type=int, default=DEFAULT_IMPORT_LIMIT)
    import_github.add_argument("--lane", default="backlog")
    import_github.set_defaults(func=cmd_import_github)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))

import sys
from scripts.issue_tool.cli import main
from scripts.issue_tool.shared import CliError

if __name__ == "__main__":
    try:
        sys.exit(main())
    except CliError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)

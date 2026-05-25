# UBS Static Analysis Pre-Check

UBS (Ultimate Bug Scanner) is available as an optional local pre-check for likely
bug patterns before a change enters the normal repository validation path.

This repository does not run the upstream UBS installer. The upstream installer can
modify shell configuration, git hooks, and agent guidance; that is too broad for a
repository validation target. Instead, `make ensure-ubs` downloads the pinned UBS
runner into `.build/tools/ubs/`, verifies its SHA-256 checksum, and runs it from
there.

## Pin

| Field | Value |
|-------|-------|
| Upstream | `Dicklesworthstone/ultimate_bug_scanner` |
| Version | `5.2.76` |
| Tag | `v5.2.76` |
| Runner SHA-256 | `c53f88c9265410feaa418684370d87f680c98e6b0096a97aa6cf9da2810b7b97` |
| Installed path | `.build/tools/ubs/5.2.76/ubs` |

## Commands

```bash
make ensure-ubs        # Download and verify the pinned runner
make validate-ubs      # Scan the current diff
make validate-ubs-full # Scan the full codebase
```

`validate-ubs` emits JSON because that avoids adding the separate TOON encoder to
the platform toolchain. It is intentionally not part of `validate-local` or
`validate-pre-push` yet. It should be used as an agent/local pre-check while the
repository builds a false-positive baseline. Once the signal is understood, the team
can decide whether to make UBS blocking, warning-only in CI, or limited to changed
files.

## Alternatives Considered

| Tool | Fit | Decision |
|------|-----|----------|
| Ruff and Pyright | Already in `make validate-local`; strong Python linting and type checking. | Keep as the mandatory Python correctness baseline. |
| ESLint and TypeScript checks | Already cover SPA TypeScript syntax, lint, and type contracts. | Keep as the mandatory frontend baseline. |
| Semgrep | Mature multi-language static analysis with pre-commit and CI support. | Good candidate for security/custom policy rules, but requires rule-set selection and baseline work before becoming a gate. |
| CodeQL CLI | Deep semantic code scanning with SARIF output and GitHub code scanning integration. | Better suited to CI/nightly or GitHub code scanning than a fast local pre-check. |
| UBS | Multi-language bug-pattern scanner with diff/staged modes and compact agent-friendly output. | Add as pinned advisory local pre-check first. |

## Operating Rules

- Do not curl-pipe the upstream installer from repository validation.
- Do not let UBS rewrite git hooks, shell startup files, or agent instruction files.
- Keep the runner version and checksum pinned in `scripts/ensure_ubs.py`.
- Run with `UBS_NO_AUTO_UPDATE=1` and a repo-local `XDG_DATA_HOME` so scans do not
  depend on mutable workstation state.
- Treat findings as review input until the repository has a checked baseline.

## References

Checked on 2026-05-25:

- UBS upstream repository: <https://github.com/Dicklesworthstone/ultimate_bug_scanner>
- UBS release `v5.2.76`: <https://github.com/Dicklesworthstone/ultimate_bug_scanner/releases/tag/v5.2.76>
- Semgrep pre-commit documentation: <https://semgrep.dev/docs/extensions/pre-commit>
- CodeQL CLI overview: <https://docs.github.com/code-security/codeql-cli/getting-started-with-the-codeql-cli/about-the-codeql-cli>

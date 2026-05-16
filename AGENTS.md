# Assistant instructions pointer

Pointer only.

Read [CLAUDE.md](CLAUDE.md) for the authoritative rules for AI coding assistants.
This file exists so tools that look for assistant instruction files can redirect to the source of truth.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

Keep this file as a stable pointer stub.
Do not duplicate or fork generated GitNexus context here.

<!-- gitnexus:end -->
After committing code changes, the GitNexus index becomes stale. Prefer the
embedding-safe refresh target:

```bash
make gitnexus-refresh
```

If running manually and the index previously included embeddings, preserve them by adding
`--embeddings`:

```bash
npx gitnexus analyze --embeddings
```

To check whether embeddings exist, inspect `.gitnexus/meta.json` — the `stats.embeddings` field shows the count (0 means no embeddings). **Running analyze without `--embeddings` preserves existing embeddings but will not generate new ones for changed files. Pass `--drop-embeddings` if you explicitly want to clear them.**

> Claude Code users: A PostToolUse hook detects staleness after `git commit` and `git merge` and notifies the agent to run `analyze` — the hook does not run analyze itself, to avoid blocking the agent for up to 120s and risking KuzuDB corruption on timeout.

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

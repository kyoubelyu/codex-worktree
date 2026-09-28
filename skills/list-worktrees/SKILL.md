---
name: list-worktrees
description: List Git worktrees, their branches, and the current working tree in Codex CLI.
---

Run the bundled `../../scripts/worktree.py list` helper, resolving the script path relative to this SKILL.md:

```text
python3 <plugin-root>/scripts/worktree.py list
```

Use the JSON result to show each path and branch, marking the main/current worktree and any detached, locked, or prunable entries. Do not create, switch, prune, or remove anything. If the current directory is not in a Git repository, ask which repository to inspect, then pass `--cwd <repo>` before `list`.

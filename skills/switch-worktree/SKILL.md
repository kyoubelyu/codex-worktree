---
name: switch-worktree
description: Switch the current Codex CLI conversation to an existing Git worktree or repository without losing its history.
---

Use the bundled helper at `../../scripts/worktree.py`, relative to this SKILL.md. Resolve its absolute path before executing it.

Take the target path from the request. If the user gives only a branch name, list the current repository's worktrees and resolve that name to a unique path. Ask only when the target is missing or ambiguous.

```text
python3 <plugin-root>/scripts/worktree.py switch <target-path>
python3 <plugin-root>/scripts/worktree.py list
```

Quote the target safely. Relative paths resolve against the live conversation's working directory. The target must already be a non-bare Git working tree; it may be in another repository. This command does not create branches, merge files, stash changes, or copy uncommitted work.

The helper uses CODEX_THREAD_ID and the existing local Codex daemon. It changes only cwd through App Server, verifies the result, and preserves the same thread and conversation history. Never substitute a shell `cd`, guess the most recent session, change permissions, or edit transcript files. Report connection or unsupported-mode errors as failures.

After a successful switch, report the destination and end this turn. The change applies to subsequent turns; do not continue editing in the old turn. If already in the destination, report that no change was needed.

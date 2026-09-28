---
name: remove-worktree
description: Safely remove a clean, unused linked Git worktree while preserving its branch.
---

Run the bundled helper at `../../scripts/worktree.py`, relative to this SKILL.md, for the worktree the user explicitly asks to remove:

```text
python3 <plugin-root>/scripts/worktree.py remove <target-path>
```

Resolve a branch-only request with `list` first; ask if the target is ambiguous. Quote paths safely. The helper refuses the main/current worktree, a locked worktree, directories with modified/untracked/ignored files, and worktrees used by loaded sessions on the connected Codex server. It preserves the branch. Do not bypass a refusal with `--force`, `rm -rf`, branch deletion, or automatic stashing. Report the refusal and let the user decide how to handle its cause. Removing a worktree does not authorize removing its branch or another session's files.

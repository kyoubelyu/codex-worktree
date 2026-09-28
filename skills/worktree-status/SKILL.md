---
name: worktree-status
description: Show the current Git worktree, branch, uncommitted-change status, and live Codex session directory.
---

Run the bundled helper, resolving `../../scripts/worktree.py` relative to this SKILL.md:

```text
python3 <plugin-root>/scripts/worktree.py status
```

Report the Git root, branch (or detached HEAD), whether there are changes, and the live session cwd when available. If shell cwd and live session cwd differ, state that explicitly; do not claim a switch has completed merely because a shell used `cd`. A session_unavailable result means Git status is available but the live session connection could not be verified. Do not modify anything.

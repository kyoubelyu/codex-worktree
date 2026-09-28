---
name: create-worktree
description: Create a Git worktree and move the current Codex CLI conversation into it while preserving history.
---

Use the bundled helper at `../../scripts/worktree.py`, relative to this SKILL.md. Resolve its absolute path before executing it; the plugin can live in a versioned cache.

Take the branch, optional target path, and optional base ref from the user's request. If the branch/task is missing, ask for it. Default to creating and switching; omit `--switch` when the user only wants creation. Creation starts from committed HEAD, or the requested `--base`; it does not move uncommitted changes.

```text
python3 <plugin-root>/scripts/worktree.py create <branch> --switch
python3 <plugin-root>/scripts/worktree.py create <branch> --path <path> --base <ref> --switch
python3 <plugin-root>/scripts/worktree.py create <branch> --existing --switch
```

Quote paths and branch arguments safely for the active shell. Existing branches require `--existing`; never reset them. Without `--path`, the helper chooses a sibling `<repo>-worktrees/<branch-with-slashes-replaced>` directory. If no Git repository is selected, ask for its location and pass the global `--cwd <repo>` before `create`.

The helper obtains the current conversation from CODEX_THREAD_ID. Do not guess a session ID, use `--last`, or copy/delete transcript files. Switching requires the normal local Codex daemon; surface any connection or unsupported-mode error instead of reporting a successful switch.

Read the JSON result. If creation succeeds but switching fails, report the created path and switch error; preserve the worktree. If switching succeeds, report its path/branch and end this turn: the new working directory takes effect on subsequent turns. Do not continue editing or testing inside the old turn. Conversation history stays in the same thread.

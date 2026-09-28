#!/usr/bin/env python3
"""Git worktree operations and in-session Codex directory switching."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from codex_rpc import CodexClient, RpcError


class WorktreeError(RuntimeError):
    pass


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items()
           if key not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"}}
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args], capture_output=True, timeout=30,
            env={**env, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeError(f"Cannot run Git: {exc}") from exc
    if check and result.returncode:
        raise WorktreeError(result.stderr.decode(errors="replace").strip() or "Git command failed")
    return result


def git_text(cwd: Path, *args: str) -> str:
    return git(cwd, *args).stdout.decode(errors="surrogateescape").strip()


def directory(value: str | Path, base: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def repository(cwd: Path) -> Path:
    if not cwd.is_dir():
        raise WorktreeError(f"Directory does not exist: {cwd}")
    inside = git(cwd, "rev-parse", "--is-inside-work-tree", check=False)
    if inside.returncode or inside.stdout.strip() != b"true":
        raise WorktreeError(f"Not a non-bare Git working tree: {cwd}")
    return Path(git_text(cwd, "rev-parse", "--show-toplevel")).resolve()


def list_worktrees(cwd: Path) -> list[dict]:
    root = repository(cwd)
    raw = git(root, "worktree", "list", "--porcelain", "-z").stdout
    entries: list[dict] = []
    current: dict = {}
    for field in raw.split(b"\0"):
        if not field:
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = field.partition(b" ")
        key = key.decode("ascii")
        text = os.fsdecode(value)
        if key == "worktree":
            current["path"] = str(Path(text).resolve())
        elif key == "branch":
            current["branch"] = text.removeprefix("refs/heads/")
        else:
            current[key] = text if value else True
    if current:
        entries.append(current)
    for index, entry in enumerate(entries):
        entry["main"] = index == 0
        entry["current"] = entry["path"] == str(root)
    return entries


def status(cwd: Path) -> dict:
    root = repository(cwd)
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    changes = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=normal").stdout
    return {
        "cwd": str(cwd), "root": str(root),
        "branch": os.fsdecode(branch.stdout).strip() if branch.returncode == 0 else None,
        "dirty": bool(changes), "worktrees": list_worktrees(root),
    }


def create_worktree(cwd: Path, branch: str, path: str | None = None,
                    base: str = "HEAD", existing: bool = False) -> dict:
    root = repository(cwd)
    if branch.startswith("-") or branch.startswith("@{-"):
        raise WorktreeError("Provide a literal branch name, not an option or checkout shorthand")
    git(root, "check-ref-format", "--branch", branch)
    if base.startswith("-"):
        raise WorktreeError("The base ref cannot be an option")
    requested = Path(path).expanduser() if path else root.parent / (root.name + "-worktrees") / branch.replace("/", "-")
    requested = requested if requested.is_absolute() else cwd / requested
    if requested.exists() or requested.is_symlink():
        raise WorktreeError(f"Target already exists; use switch-worktree instead: {requested}")
    target = requested.resolve()
    if target.exists():
        raise WorktreeError(f"Target already exists; use switch-worktree instead: {target}")
    if target == root or root in target.parents:
        raise WorktreeError("Choose a worktree path outside the source working tree")
    exists = git(root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0
    if exists and not existing:
        raise WorktreeError(f"Branch {branch!r} exists. Use --existing to check it out without resetting it")
    if existing and not exists:
        raise WorktreeError(f"Local branch {branch!r} does not exist")
    if not existing:
        # Resolve before creating anything; --end-of-options prevents ref injection.
        base = git_text(root, "rev-parse", "--verify", "--end-of-options", base + "^{commit}")
    args = ["worktree", "add"]
    if not existing:
        args += ["-b", branch]
    args += ["--", str(target), branch if existing else base]
    git(root, *args)
    return {"created": True, "path": str(target), "branch": branch,
            "source": str(root), "switched": False}


def current_thread_id(explicit: str | None = None) -> str:
    thread_id = explicit or os.environ.get("CODEX_THREAD_ID")
    if not thread_id:
        raise WorktreeError("Run switching inside Codex CLI, or supply --thread-id. No session was changed")
    return thread_id


def read_live_thread(client: CodexClient, thread_id: str) -> dict:
    thread = client.request("thread/read", {"threadId": thread_id, "includeTurns": False})["thread"]
    if thread.get("status", {}).get("type") not in {"idle", "active"}:
        raise WorktreeError(
            "This conversation is not loaded on the connected Codex server. "
            "Use the normal local CLI daemon (not --no-daemon), or select its socket with "
            "WORKTREE_CODEX_SOCKET. No session was changed"
        )
    if thread.get("ephemeral"):
        # Ephemeral threads have no rollout to resume; their metadata is live.
        return thread
    # Stored thread.cwd can lag a settings update until the next turn. Joining
    # an already-loaded thread, without overrides, returns the live cwd at the
    # top level. Do not use the nested thread.cwd from this response either.
    runtime = client.request("thread/resume", {"threadId": thread_id, "excludeTurns": True})
    return {**thread, "stored_cwd": thread["cwd"], "cwd": runtime["cwd"]}


def switch_worktree(client: CodexClient, thread_id: str, target: Path) -> dict:
    root = repository(target)
    before = read_live_thread(client, thread_id)
    previous = str(Path(before["cwd"]).resolve())
    if previous == str(target):
        return {"switched": False, "already_current": True, "thread_id": thread_id,
                "path": str(target), "root": str(root)}
    # Only change cwd. Never relax approval, permissions, sandbox, or model settings.
    client.request("thread/settings/update", {"threadId": thread_id, "cwd": str(target)})
    after = read_live_thread(client, thread_id)
    actual = str(Path(after["cwd"]).resolve())
    if actual != str(target):
        raise WorktreeError(f"Codex did not confirm the switch; reported cwd: {actual}")
    return {
        "switched": True, "thread_id": thread_id, "previous": previous,
        "path": actual, "root": str(root), "history_preserved": True,
        "effective": "subsequent_turns", "current_turn_must_end": True,
    }


def loaded_worktree_users(client: CodexClient, target: Path) -> list[str]:
    users = []
    cursor = None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        result = client.request("thread/loaded/list", params)
        for thread_id in result.get("data", []):
            thread = read_live_thread(client, thread_id)
            # Protect both the current turn's old directory and the next turn's
            # new directory when another conversation is in the middle of a switch.
            paths = {Path(thread[key]).resolve() for key in ("cwd", "stored_cwd") if key in thread}
            if any(cwd == target or target in cwd.parents for cwd in paths):
                users.append(thread_id)
        cursor = result.get("nextCursor")
        if not cursor:
            return users


def remove_worktree(cwd: Path, target: Path, client: CodexClient | None = None) -> dict:
    root = repository(cwd)
    entries = list_worktrees(root)
    entry = next((x for x in entries if x["path"] == str(target)), None)
    if not entry:
        raise WorktreeError("Target must be the exact path of a worktree registered to this repository")
    if entry.get("main") or target == root or target in cwd.parents:
        raise WorktreeError("Cannot remove the main or current working tree. Switch away first")
    if entry.get("locked"):
        raise WorktreeError("The worktree is locked; unlock it explicitly outside this command first")
    if not target.is_dir():
        raise WorktreeError("Worktree directory is missing; inspect it with git worktree list")
    dirty = git(target, "status", "--porcelain=v1", "-z", "--untracked-files=normal", "--ignored").stdout
    if dirty:
        raise WorktreeError("Worktree contains modified, untracked, or ignored files. Nothing was removed")
    if client:
        users = loaded_worktree_users(client, target)
        if users:
            raise WorktreeError("Worktree is in use by loaded Codex sessions: " + ", ".join(users))
    git(root, "worktree", "remove", "--", str(target))
    return {"removed": True, "path": str(target), "branch_preserved": entry.get("branch")}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--cwd", default=None, help="Source directory (defaults to this shell's cwd)")
    result.add_argument("--socket", help="Existing Codex App Server Unix socket")
    result.add_argument("--thread-id", help="Defaults to CODEX_THREAD_ID; never guess the latest session")
    commands = result.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Create a worktree; optionally switch this conversation")
    create.add_argument("branch")
    create.add_argument("--path")
    create.add_argument("--base", default="HEAD")
    create.add_argument("--existing", action="store_true")
    create.add_argument("--switch", action="store_true")
    switch = commands.add_parser("switch", help="Keep this thread's history and change its working directory")
    switch.add_argument("path")
    commands.add_parser("list", help="List registered worktrees")
    commands.add_parser("status", help="Show Git status and, when available, the live session directory")
    remove = commands.add_parser("remove", help="Remove a clean, unused linked worktree; preserve its branch")
    remove.add_argument("path")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    cwd = directory(args.cwd or Path.cwd(), Path.cwd())
    try:
        if args.command == "list":
            result = {"worktrees": list_worktrees(cwd)}
        elif args.command == "status":
            result = status(cwd)
            if args.thread_id or os.environ.get("CODEX_THREAD_ID"):
                try:
                    with CodexClient(args.socket) as client:
                        thread = read_live_thread(client, current_thread_id(args.thread_id))
                        result["session"] = {"thread_id": thread["id"], "cwd": thread["cwd"],
                                             "status": thread["status"]}
                except (OSError, RpcError, WorktreeError) as exc:
                    result["session_unavailable"] = str(exc)
        elif args.command == "create":
            if args.switch:
                thread_id = current_thread_id(args.thread_id)
                with CodexClient(args.socket) as client:
                    # Fail before creating anything if this CLI cannot be switched.
                    before = read_live_thread(client, thread_id)
                    cwd = directory(args.cwd or before["cwd"], cwd)
                    result = create_worktree(cwd, args.branch, args.path, args.base, args.existing)
                    try:
                        result["session"] = switch_worktree(client, thread_id, Path(result["path"]))
                        result["switched"] = result["session"]["switched"]
                    except (OSError, RpcError, WorktreeError) as exc:
                        result["switch_error"] = str(exc)
                        # Preserve a successfully created worktree on a later RPC failure.
                        print(json.dumps(result, ensure_ascii=True))
                        return 1
            else:
                result = create_worktree(cwd, args.branch, args.path, args.base, args.existing)
        elif args.command == "switch":
            with CodexClient(args.socket) as client:
                thread_id = current_thread_id(args.thread_id)
                before = read_live_thread(client, thread_id)
                source = directory(args.cwd or before["cwd"], cwd)
                result = switch_worktree(client, thread_id, directory(args.path, source))
        else:
            # In Codex, check other loaded sessions before removing their workspace.
            if args.thread_id or os.environ.get("CODEX_THREAD_ID"):
                with CodexClient(args.socket) as client:
                    before = read_live_thread(client, current_thread_id(args.thread_id))
                    cwd = directory(args.cwd or before["cwd"], cwd)
                    result = remove_worktree(cwd, directory(args.path, cwd), client)
            else:
                result = remove_worktree(cwd, directory(args.path, cwd))
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (WorktreeError, RpcError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

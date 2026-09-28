# Worktree for Codex CLI

Create and switch Git worktrees inside Codex CLI while keeping the same
conversation. You can also list worktrees, check their status, and remove clean,
unused worktrees.

## Commands inside Codex

Type `$worktree` and select a skill, or enter its full name with your request:

```text
$worktree:create-worktree feature/login
$worktree:create-worktree feature/login at ../login-worktree from main
$worktree:switch-worktree ../login-worktree
$worktree:list-worktrees
$worktree:worktree-status
$worktree:remove-worktree ../finished-worktree
```

These commands are plugin skills, invoked with `$`. Codex CLI 0.157.1 does not
accept `/create-worktree` or `/worktree:create-worktree` as plugin slash commands.
The plugin does not patch Codex CLI or install global user skills.

Creation defaults to a new branch from committed HEAD and a directory beside the
repository. For example, branch `feature/login` goes in
`<repo>-worktrees/feature-login`. Specify another path or base ref in your
request. Ask to use an existing branch or to create without switching when
needed. Uncommitted changes stay in the source working tree.

A successful switch applies to the **next turn**. Codex reports the destination
and ends the command turn; send your next task or `continue` to work there. The
same thread is retained. The next turn loads the destination's project
instructions. No transcript files are copied or deleted.

Removal preserves the branch and refuses the main/current worktree, locked
worktrees, modified/untracked/ignored files, and worktrees used by loaded sessions
on the connected local Codex server. Sessions in other Codex servers and external
editors are outside that check. There is no force-removal option.

## Install

```bash
codex plugin marketplace add kyoubelyu/codex-worktree
codex plugin add worktree@codex-worktree
```

Start a new Codex CLI conversation after installation or an update so the skills
are loaded.

## Requirements

- Codex CLI **0.157.1**, the tested version. App Server interfaces can change;
  rerun the integration test before relying on a different release.
- Python 3.11+ and Git. The helper needs no third-party Python packages or API key.
- For in-session switching: the normal local Codex daemon on a Unix platform.
  Tested on Linux. Windows, `--no-daemon`, and remote WebSocket CLI sessions are
  not supported for switching. Git-only operations can still run locally.
- A custom local Unix server socket can be selected with
  `WORKTREE_CODEX_SOCKET=/absolute/socket/path`.

The repository includes a marketplace entry, `plugin.json`,
`.codex-plugin/plugin.json`, five skills, and two Python scripts. It has no hooks
or MCP server.

The helper connects only to the existing local App Server. It identifies the
current conversation using `CODEX_THREAD_ID` and updates only its `cwd`; it does
not select the most recent session, start another server, or relax permissions.
Normal Codex tool permissions still apply. If creation succeeds but switching
fails, the new worktree is preserved and the error is reported.

## Development and checks

```bash
python3 -m unittest discover -s tests -v
WORKTREE_LIVE_TEST=1 python3 -m unittest discover -s tests -p test_live_codex.py -v
```

Install the plugin before running the opt-in integration test. The test starts
an isolated local Codex App Server, creates temporary repositories, and uses
disposable conversations with a local fake model endpoint. It verifies switching
during an active turn, conversation continuity, the next turn's directory/project
instructions, and unchanged approval/sandbox settings. It also drives the actual
CLI to check the skill picker, terminal title, and a prompt after switching.
Test conversations and the test server are removed afterward. It makes no paid
model requests and does not modify an existing user conversation.

For manual debugging, run `python3 scripts/worktree.py --help`. Global options
such as `--cwd`, `--socket`, and `--thread-id` go before the subcommand. Supplying
a thread ID explicitly is intended for testing; skill invocations use the
current thread automatically.

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import worktree as wt


class GitFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-worktree-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        wt.git(self.repo, "init", "-q", "-b", "main")
        wt.git(self.repo, "config", "user.name", "Worktree Test")
        wt.git(self.repo, "config", "user.email", "worktree@example.invalid")
        (self.repo / "tracked.txt").write_text("original\n")
        wt.git(self.repo, "add", "tracked.txt")
        wt.git(self.repo, "commit", "-qm", "test fixture")

    def create(self, branch="feature", **kwargs):
        return wt.create_worktree(self.repo, branch, **kwargs)


class GitTests(GitFixture):
    def test_create_list_preserves_source_and_handles_spaces_unicode_newlines(self):
        path = self.base / "working tree 测试\nnext line"
        (self.repo / "tracked.txt").write_text("unsaved source edits\n")
        result = self.create(path=str(path))
        self.assertEqual(result["path"], str(path))
        self.assertEqual((path / "tracked.txt").read_text(), "original\n")
        self.assertEqual((self.repo / "tracked.txt").read_text(), "unsaved source edits\n")
        entries = wt.list_worktrees(self.repo)
        self.assertEqual(entries[1]["path"], str(path))
        self.assertEqual(entries[1]["branch"], "feature")
        self.assertTrue(entries[0]["main"])

    def test_default_path_sits_outside_repo(self):
        result = self.create("feature/login")
        self.assertEqual(result["path"], str(self.base / "repo-worktrees" / "feature-login"))

    def test_existing_branch_requires_explicit_flag_and_never_resets(self):
        wt.git(self.repo, "branch", "existing")
        before = wt.git_text(self.repo, "rev-parse", "existing")
        with self.assertRaises(wt.WorktreeError):
            self.create("existing")
        result = self.create("existing", existing=True)
        self.assertEqual(wt.git_text(Path(result["path"]), "rev-parse", "HEAD"), before)

    def test_existing_directory_and_branch_conflict_leave_data_intact(self):
        target = self.base / "occupied"
        target.mkdir()
        (target / "keep").write_text("keep")
        with self.assertRaises(wt.WorktreeError):
            self.create(path=str(target))
        self.assertEqual((target / "keep").read_text(), "keep")
        self.create()
        with self.assertRaises(wt.WorktreeError):
            self.create(path=str(self.base / "second"), existing=True)
        self.assertFalse((self.base / "second").exists())

    def test_dangling_symlink_is_not_overwritten(self):
        target = self.base / "dangling"
        target.symlink_to(self.base / "absent", target_is_directory=True)
        with self.assertRaises(wt.WorktreeError):
            self.create(path=str(target))
        self.assertTrue(target.is_symlink())
        self.assertFalse((self.base / "absent").exists())

    def test_invalid_refs_and_nested_targets_do_not_create_worktrees(self):
        for name in ["--detach", "bad..name", "@{-1}"]:
            with self.subTest(name=name), self.assertRaises(wt.WorktreeError):
                self.create(name)
        for base in ["missing-ref", "--help"]:
            with self.subTest(base=base), self.assertRaises(wt.WorktreeError):
                self.create(base=base)
        with self.assertRaises(wt.WorktreeError):
            self.create(path=str(self.repo / "nested"))
        self.assertEqual(len(wt.list_worktrees(self.repo)), 1)

    def test_remove_clean_worktree_preserves_branch(self):
        target = Path(self.create()["path"])
        result = wt.remove_worktree(self.repo, target)
        self.assertTrue(result["removed"])
        self.assertFalse(target.exists())
        self.assertEqual(wt.git_text(self.repo, "rev-parse", "feature"), wt.git_text(self.repo, "rev-parse", "HEAD"))

    def test_remove_refuses_main_current_nested_and_locked(self):
        target = Path(self.create()["path"])
        for cwd, path in [(self.repo, self.repo), (target, target), (self.repo, target / "subdir")]:
            with self.subTest(cwd=cwd, path=path), self.assertRaises(wt.WorktreeError):
                wt.remove_worktree(cwd, path)
        wt.git(self.repo, "worktree", "lock", str(target))
        with self.assertRaises(wt.WorktreeError):
            wt.remove_worktree(self.repo, target)
        self.assertTrue(target.exists())

    def test_remove_refuses_modified_untracked_and_ignored_files(self):
        target = Path(self.create()["path"])
        tracked = target / "tracked.txt"
        tracked.write_text("changed")
        with self.assertRaises(wt.WorktreeError):
            wt.remove_worktree(self.repo, target)
        wt.git(target, "restore", "tracked.txt")
        untracked = target / "private.txt"
        untracked.write_text("keep")
        with self.assertRaises(wt.WorktreeError):
            wt.remove_worktree(self.repo, target)
        exclude = Path(wt.git_text(target, "rev-parse", "--git-path", "info/exclude"))
        with exclude.open("a") as f:
            f.write("\nprivate.txt\n")
        with self.assertRaises(wt.WorktreeError):
            wt.remove_worktree(self.repo, target)
        self.assertEqual(untracked.read_text(), "keep")

    def test_bare_and_non_git_directories_are_rejected(self):
        with self.assertRaises(wt.WorktreeError):
            wt.repository(self.base)
        bare = self.base / "bare.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
        with self.assertRaises(wt.WorktreeError):
            wt.repository(bare)

    def test_detached_status_and_symlink_target(self):
        target = Path(self.create()["path"])
        wt.git(target, "checkout", "--detach", "-q")
        self.assertIsNone(wt.status(target)["branch"])
        alias = self.base / "alias"
        alias.symlink_to(target, target_is_directory=True)
        self.assertEqual(wt.directory(alias, self.repo), target)


class FakeClient:
    def __init__(self, cwd, state="active", confirm=True):
        self.cwd = str(cwd)
        self.stored_cwd = str(cwd)
        self.state = state
        self.confirm = confirm
        self.calls = []

    def request(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "cwd": self.stored_cwd, "status": {"type": self.state}}}
        if method == "thread/resume":
            return {"cwd": self.cwd}
        if method == "thread/settings/update":
            if self.confirm:
                self.cwd = params["cwd"]
            return {}
        if method == "thread/loaded/list":
            return {"data": ["busy-thread"], "nextCursor": None}
        raise AssertionError(method)


class SwitchTests(GitFixture):
    def test_switch_only_changes_cwd_and_keeps_thread_id(self):
        target = Path(self.create()["path"])
        client = FakeClient(self.repo)
        result = wt.switch_worktree(client, "same-thread", target)
        self.assertTrue(result["history_preserved"])
        self.assertEqual(result["thread_id"], "same-thread")
        self.assertIn(("thread/settings/update", {"threadId": "same-thread", "cwd": str(target)}), client.calls)
        self.assertEqual(len(client.calls), 5)

    def test_switch_same_path_is_noop(self):
        client = FakeClient(self.repo)
        result = wt.switch_worktree(client, "same-thread", self.repo)
        self.assertTrue(result["already_current"])
        self.assertEqual(len(client.calls), 2)

    def test_invalid_target_and_unloaded_thread_do_not_mutate(self):
        client = FakeClient(self.repo, state="notLoaded")
        with self.assertRaises(wt.WorktreeError):
            wt.switch_worktree(client, "same-thread", self.base)
        self.assertEqual(client.calls, [])
        with self.assertRaises(wt.WorktreeError):
            wt.switch_worktree(client, "same-thread", self.repo)
        self.assertFalse(any(m == "thread/settings/update" for m, _ in client.calls))

    def test_unconfirmed_switch_reports_failure(self):
        target = Path(self.create()["path"])
        with self.assertRaises(wt.WorktreeError):
            wt.switch_worktree(FakeClient(self.repo, confirm=False), "same-thread", target)

    def test_remove_refuses_worktree_with_other_loaded_session(self):
        target = Path(self.create()["path"])
        with self.assertRaises(wt.WorktreeError):
            wt.remove_worktree(self.repo, target, FakeClient(target))
        self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()

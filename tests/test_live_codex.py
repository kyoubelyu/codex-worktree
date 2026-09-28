"""Opt-in integration test: real local Codex, temporary repos, fake local model.

WORKTREE_LIVE_TEST=1 python3 -m unittest discover -s tests -p test_live_codex.py -v
No paid model calls. Never modifies an existing user conversation.
"""

import json
import fcntl
import os
from pathlib import Path
import pty
import queue
import re
import select
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import tty
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from codex_rpc import CodexClient
import worktree as wt


@unittest.skipUnless(os.environ.get("WORKTREE_LIVE_TEST") == "1", "opt-in local Codex integration")
class LiveCodexTests(unittest.TestCase):
    def _check_cli(self, client, source, target, requests):
        # Start through the actual CLI so its startup events and skill loading
        # follow the same path as a user's session (not an API-created thread).
        master, slave = pty.openpty()
        tty.setraw(slave)
        fcntl.ioctl(slave, 0x5414, struct.pack("HHHH", 45, 180, 0, 0))
        def attach_terminal():
            os.setsid()
            fcntl.ioctl(0, 0x540e, 0)
        args = ["codex", "--remote", "unix://" + client.path, "-C", str(source),
                "-m", "worktree-test", "-s", "read-only"]
        process = subprocess.Popen(args, stdin=slave, stdout=slave, stderr=slave,
            preexec_fn=attach_terminal, env={**os.environ, "TERM": "xterm-256color"})
        os.close(slave)
        ansi = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]")
        titles = []
        thread_id = None
        def collect(seconds):
            deadline, output = time.monotonic() + seconds, bytearray()
            while time.monotonic() < deadline:
                if select.select([master], [], [], .1)[0]:
                    try:
                        part = os.read(master, 65536)
                    except OSError:
                        break
                    output.extend(part)
                    if b"\x1b[6n" in part:
                        os.write(master, b"\x1b[1;1R")
                elif process.poll() is not None:
                    break
            decoded = output.decode(errors="replace")
            titles.extend(re.findall(r"\x1b\]0;([^\x07]*)\x07", decoded))
            return ansi.sub("", decoded)
        try:
            output = collect(3)
            if "Folder access" in output:
                os.write(master, b"\r")
                output += collect(3)
            self.assertIsNone(process.poll(), output[-3000:])
            os.write(master, b"$worktree")
            menu = ""
            for _ in range(10):
                menu += collect(1)
                if "create-worktree" in menu:
                    break
            self.assertIn("create-worktree", menu, output[-1000:] + menu)
            self.assertIn("switch-worktree", menu)
            os.write(master, b"\x1b")
            collect(.2)
            os.write(master, b"\x03")
            collect(.2)
            os.write(master, b"CLI_FIRST_TURN_MARKER")
            collect(.3)  # End the TUI paste burst before submitting Enter.
            os.write(master, b"\r")
            submitted = ""
            for _ in range(15):
                submitted += collect(1)
                if not requests.empty():
                    break
            self.assertFalse(requests.empty(), submitted[-4000:])
            first = requests.get_nowait()
            self.assertIn("CLI_FIRST_TURN_MARKER", json.dumps(first))
            collect(2)
            loaded = client.request("thread/loaded/list", {"limit": 100})
            for candidate in loaded["data"]:
                info = client.request("thread/read", {"threadId": candidate, "includeTurns": True})["thread"]
                if info["cwd"] == str(source) and "CLI_FIRST_TURN_MARKER" in json.dumps(info):
                    thread_id = candidate
                    break
            self.assertIsNotNone(thread_id, "No matching CLI-created test session")
            self._wait_idle(client, thread_id)
            changed = wt.switch_worktree(client, thread_id, target)
            self.assertTrue(changed["switched"])
            collect(2)
            self.assertTrue(any("verified-destination" in t for t in titles))
            os.write(master, b"CLI_SECOND_TURN_MARKER")
            collect(.3)
            os.write(master, b"\r")
            submitted = ""
            for _ in range(15):
                submitted += collect(1)
                if not requests.empty():
                    break
            self.assertFalse(requests.empty(), submitted[-4000:])
            second = json.dumps(requests.get_nowait())
            self.assertIn("CLI_FIRST_TURN_MARKER", second)
            self.assertIn("CLI_SECOND_TURN_MARKER", second)
            self.assertIn(str(target), second)
            self._wait_idle(client, thread_id)
        finally:
            if process.poll() is None:
                os.write(master, b"\x03\x03")
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=3)
            os.close(master)
            if thread_id:
                client.request("thread/delete", {"threadId": thread_id})

    def test_active_switch_next_turn_history_instructions_and_permissions(self):
        requests = queue.Queue()
        release = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                metadata = json.loads(body.get("client_metadata", {}).get("x-codex-turn-metadata", "{}"))
                is_title = metadata.get("thread_source") == "thread_title"
                if not is_title:
                    requests.put(body)
                release.wait(25)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                answer = '{"title":"Test worktree switch"}' if is_title else "WORKTREE_TEST_DONE"
                item = {"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": answer, "annotations": []}]}
                events = [
                    {"type": "response.created", "response": {"id": "resp_test", "object": "response", "status": "in_progress", "output": []}},
                    {"type": "response.output_item.added", "output_index": 0, "item": {**item, "status": "in_progress", "content": []}},
                    {"type": "response.content_part.added", "item_id": "msg_test", "output_index": 0, "content_index": 0,
                     "part": {"type": "output_text", "text": "", "annotations": []}},
                    {"type": "response.output_text.delta", "item_id": "msg_test", "output_index": 0, "content_index": 0, "delta": answer},
                    {"type": "response.output_item.done", "output_index": 0, "item": item},
                    {"type": "response.completed", "response": {"id": "resp_test", "object": "response", "status": "completed", "output": [item],
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}},
                ]
                for event in events:
                    self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                    self.wfile.flush()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(release.set)
        tmp = self.enterContext(tempfile.TemporaryDirectory(prefix="codex-worktree-live-"))
        root = Path(tmp)
        source, target = root / "source", root / "target"
        for path in [source, target]:
            path.mkdir()
            wt.git(path, "init", "-q")
        (source / "AGENTS.md").write_text("SOURCE_INSTRUCTION_MARKER: use the source rules.\n")
        (target / "AGENTS.md").write_text("TARGET_INSTRUCTION_MARKER: use the target rules.\n")
        home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        config_path = home / "config.toml"
        config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
        overrides = {f"mcp_servers.{k}.enabled": False for k in config.get("mcp_servers", {})}
        overrides.update({
            "features.apps": False,
            "model_providers.worktree_test": {
                "name": "Worktree local test", "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                "wire_api": "responses", "requires_openai_auth": False,
                "supports_websockets": False,
            },
        })
        socket_path = root / "server.sock"
        server_args = ["codex", "app-server", "--listen", "unix://" + str(socket_path),
                       "-c", 'model_provider="worktree_test"', "-c", 'model="worktree-test"']
        for key, value in overrides.items():
            values = {key + "." + k: v for k, v in value.items()} if isinstance(value, dict) else {key: value}
            for dotted, literal in values.items():
                server_args += ["-c", dotted + "=" + json.dumps(literal)]
        codex_server = subprocess.Popen(server_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self._stop_server, codex_server)
        deadline = time.monotonic() + 15
        while not socket_path.exists() and time.monotonic() < deadline:
            if codex_server.poll() is not None:
                self.fail("Isolated Codex app-server exited during startup")
            time.sleep(.1)
        with CodexClient(str(socket_path), timeout=20) as client:
            result = client.request("thread/start", {
                "cwd": str(source), "modelProvider": "worktree_test", "model": "worktree-test",
                "ephemeral": False, "sandbox": "workspace-write", "approvalPolicy": "on-request",
                "config": overrides,
            })
            thread_id = result["thread"]["id"]
            self.addCleanup(self._close_thread, thread_id, str(socket_path))
            client.request("turn/start", {"threadId": thread_id,
                "input": [{"type": "text", "text": "FIRST_TURN_HISTORY_MARKER"}]})
            first_body = requests.get(timeout=20)
            self.assertIn("SOURCE_INSTRUCTION_MARKER", json.dumps(first_body))
            changed = wt.switch_worktree(client, thread_id, target)
            self.assertEqual(changed["thread_id"], thread_id)
            release.set()
            self._wait_idle(client, thread_id)
            notifications = [x for x in client.notifications if x.get("method") == "thread/settings/updated"]
            settings = notifications[-1]["params"]["threadSettings"]
            self.assertEqual(settings["approvalPolicy"], "on-request")
            self.assertEqual(settings["sandboxPolicy"]["type"], "workspaceWrite")
            self.assertFalse(settings["sandboxPolicy"]["networkAccess"])
            client.request("turn/start", {"threadId": thread_id,
                "input": [{"type": "text", "text": "SECOND_TURN_MARKER"}]})
            second_body = requests.get(timeout=20)
            encoded = json.dumps(second_body)
            self.assertIn("FIRST_TURN_HISTORY_MARKER", encoded)
            self.assertIn("WORKTREE_TEST_DONE", encoded)
            self.assertIn(str(target), encoded)
            self.assertIn("TARGET_INSTRUCTION_MARKER", encoded)
            contexts = re.findall(r"<environment_context>.*?</environment_context>", encoded)
            self.assertTrue(contexts, "Codex did not send an environment update")
            self.assertIn(str(target), contexts[-1])
            self.assertNotIn(str(source), contexts[-1])
            self._wait_idle(client, thread_id)
            final_target = root / "verified-destination"
            final_target.mkdir()
            wt.git(final_target, "init", "-q")
            self._check_cli(client, source, final_target, requests)

    @staticmethod
    def _wait_idle(client, thread_id):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            thread = client.request("thread/read", {"threadId": thread_id, "includeTurns": False})["thread"]
            if thread["status"]["type"] == "idle":
                return
            time.sleep(.1)
        raise AssertionError("Test thread did not become idle")

    @staticmethod
    def _close_thread(thread_id, socket_path):
        if Path(socket_path).exists():
            with CodexClient(socket_path) as client:
                client.request("thread/delete", {"threadId": thread_id})

    @staticmethod
    def _stop_server(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()

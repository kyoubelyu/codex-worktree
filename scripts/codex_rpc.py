"""Small local-only Codex App Server client (WebSocket over a Unix socket)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import time


class RpcError(RuntimeError):
    pass


class CodexClient:
    """Connect to an existing daemon; never start or replace the user's server."""

    MAX_MESSAGE = 16 * 1024 * 1024

    def __init__(self, socket_path: str | None = None, timeout: float = 10):
        root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        self.path = socket_path or os.environ.get("WORKTREE_CODEX_SOCKET") or str(
            root / "app-server-control" / "app-server-control.sock"
        )
        self.timeout = timeout
        self.sequence = 0
        self.notifications: list[dict] = []
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        try:
            self.sock.connect(self.path)
            self._upgrade()
            self.info = self.request("initialize", {
                "clientInfo": {"name": "codex_worktree", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            })
            self.send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.sock.close()

    def _exact(self, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            part = self.sock.recv(size - len(result))
            if not part:
                raise RpcError("Codex closed the connection")
            result.extend(part)
        return bytes(result)

    def _upgrade(self):
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        self.sock.sendall((
            "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Key: {key}\r\n\r\n"
        ).encode("ascii"))
        response = bytearray()
        while not response.endswith(b"\r\n\r\n"):
            if len(response) >= 16384:
                raise RpcError("Oversized WebSocket handshake")
            response.extend(self._exact(1))
        lines = response.decode("latin-1").split("\r\n")
        if lines[0].split()[1:2] != ["101"]:
            raise RpcError(f"Codex WebSocket upgrade failed: {lines[0]}")
        headers = dict(line.lower().split(":", 1) for line in lines[1:] if ":" in line)
        expected = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
        ).digest()).decode("ascii")
        # Header names are case insensitive; the accept value is not.
        accept = next((line.split(":", 1)[1].strip() for line in lines[1:]
                       if line.lower().startswith("sec-websocket-accept:")), "")
        if accept != expected or headers.get("upgrade", "").strip() != "websocket":
            raise RpcError("Invalid WebSocket handshake from Codex")

    def _frame(self, opcode: int, data: bytes):
        length = len(data)
        if length > self.MAX_MESSAGE:
            raise RpcError("Oversized WebSocket message")
        header = bytes([0x80 | opcode])
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += b"\xfe" + struct.pack("!H", length)
        else:
            header += b"\xff" + struct.pack("!Q", length)
        mask = os.urandom(4)
        self.sock.sendall(header + mask + bytes(x ^ mask[i % 4] for i, x in enumerate(data)))

    def send(self, message: dict):
        self._frame(1, json.dumps(message, ensure_ascii=False).encode("utf-8"))

    def receive(self, deadline: float) -> dict:
        fragments = bytearray()
        started = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for Codex")
            self.sock.settimeout(remaining)
            first, second = self._exact(2)
            final, opcode = bool(first & 0x80), first & 0x0f
            if first & 0x70 or second & 0x80:
                raise RpcError("Unsupported WebSocket frame from Codex")
            size = second & 0x7f
            if size == 126:
                size = struct.unpack("!H", self._exact(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self._exact(8))[0]
            if size > self.MAX_MESSAGE or len(fragments) + size > self.MAX_MESSAGE:
                raise RpcError("Oversized WebSocket message from Codex")
            if opcode >= 8 and (not final or size > 125):
                raise RpcError("Invalid WebSocket control frame")
            payload = self._exact(size)
            if opcode == 8:
                raise RpcError("Codex closed the WebSocket")
            if opcode == 9:
                self._frame(10, payload)
                continue
            if opcode == 10:
                continue
            if opcode == 1 and not started:
                started = True
            elif opcode != 0 or not started:
                raise RpcError("Unexpected WebSocket frame from Codex")
            fragments.extend(payload)
            if final:
                result = json.loads(fragments)
                if not isinstance(result, dict):
                    raise RpcError("Invalid JSON-RPC message from Codex")
                return result

    def request(self, method: str, params: dict) -> dict:
        self.sock.settimeout(self.timeout)
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            message = self.receive(deadline)
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    error = message["error"]
                    raise RpcError(f"{method}: {error.get('message', error)}")
                return message.get("result", {})
            if "id" in message and "method" in message:
                # This helper is not an approval UI and must not grant permissions.
                self.send({"id": message["id"], "error": {
                    "code": -32601, "message": "Use the interactive Codex client for this request"
                }})
            else:
                self.notifications.append(message)
                self.notifications = self.notifications[-100:]

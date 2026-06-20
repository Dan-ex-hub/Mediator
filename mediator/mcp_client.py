"""Minimal MCP (Model Context Protocol) client (Phase 21).

Connects to MCP servers over the stdio transport (newline-delimited JSON-RPC 2.0), lists
their tools, and calls them — so the agent tool-loop can use any MCP server the user
configures in ``config.toml``:

    [mcp.servers.fetch]
    command = "uvx"
    args = ["mcp-server-fetch"]
    # env = { KEY = "value" }
    # disabled = false
    # autoApprove = ["fetch"]

This is a compact pure-stdlib client (no MCP SDK dependency). A background thread reads the
server's stdout so requests work cross-platform without select() on pipes.

SAFETY: MCP servers can do anything the user's machine can. Only servers the user
explicitly configures are started, every tool call is surfaced as a visible event, and the
server is a child process under the local-only web server.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import MCPServerConfig

_PROTOCOL_VERSION = "2024-11-05"
_DEFAULT_TIMEOUT = 30.0


class MCPError(RuntimeError):
    """Raised for MCP connection / protocol / tool-call failures."""


@dataclass
class MCPTool:
    server: str
    name: str           # bare tool name on the server
    description: str
    schema: dict

    @property
    def full_name(self) -> str:
        return f"{self.server}.{self.name}"


class MCPClient:
    """One MCP server subprocess, spoken to over stdio JSON-RPC."""

    def __init__(self, cfg: MCPServerConfig, cwd: Path | None = None,
                 timeout: float = _DEFAULT_TIMEOUT) -> None:
        self.cfg = cfg
        self.cwd = cwd
        self.timeout = timeout
        self.proc: subprocess.Popen | None = None
        self._q: queue.Queue = queue.Queue()
        self._next_id = 0
        self._lock = threading.Lock()

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        env = {**os.environ, **self.cfg.env}
        try:
            self.proc = subprocess.Popen(
                [self.cfg.command, *self.cfg.args],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1, encoding="utf-8", errors="replace",
                env=env, cwd=str(self.cwd) if self.cwd else None,
            )
        except (OSError, ValueError) as exc:
            raise MCPError(f"could not start MCP server '{self.cfg.name}': {exc}") from exc
        threading.Thread(target=self._reader, daemon=True).start()
        self._initialize()

    def _reader(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                self._q.put(line)
        self._q.put(None)  # EOF sentinel

    def _send(self, msg: dict) -> None:
        if not self.proc or not self.proc.stdin:
            raise MCPError(f"MCP server '{self.cfg.name}' is not running.")
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MCPError(f"MCP server '{self.cfg.name}' write failed: {exc}") from exc

    def _await(self, want_id: int) -> dict:
        import time
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError(f"MCP server '{self.cfg.name}' timed out.")
            try:
                line = self._q.get(timeout=remaining)
            except queue.Empty:
                raise MCPError(f"MCP server '{self.cfg.name}' timed out.")
            if line is None:
                raise MCPError(f"MCP server '{self.cfg.name}' closed the connection.")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # ignore non-JSON log noise
            if msg.get("id") == want_id:
                if "error" in msg:
                    raise MCPError(f"{self.cfg.name}: {msg['error'].get('message', msg['error'])}")
                return msg.get("result", {})
            # notifications / other ids: ignore

    def _request(self, method: str, params: dict | None = None) -> dict:
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                        "params": params or {}})
            return self._await(rid)

    def _notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _initialize(self) -> None:
        self._request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mediator", "version": "1.0"},
        })
        self._notify("notifications/initialized")

    # -- tools ------------------------------------------------------------
    def list_tools(self) -> list[MCPTool]:
        result = self._request("tools/list", {})
        tools: list[MCPTool] = []
        for t in result.get("tools", []):
            tools.append(MCPTool(
                server=self.cfg.name,
                name=t.get("name", ""),
                description=t.get("description", ""),
                schema=t.get("inputSchema", {}) or {},
            ))
        return tools

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        parts: list[str] = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(json.dumps(item))
        text = "\n".join(p for p in parts if p)
        if result.get("isError"):
            text = f"(tool reported an error)\n{text}"
        return text or "(no output)"

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass
        self.proc = None


class MCPManager:
    """Starts a set of MCP servers and exposes their tools under ``server.tool`` names."""

    def __init__(self, servers: list[MCPServerConfig], cwd: Path | None = None) -> None:
        self.servers = servers
        self.cwd = cwd
        self.clients: dict[str, MCPClient] = {}
        self.tools: dict[str, MCPTool] = {}   # full_name -> MCPTool
        self.errors: list[str] = []

    def start_all(self) -> None:
        for cfg in self.servers:
            if cfg.disabled:
                continue
            client = MCPClient(cfg, cwd=self.cwd)
            try:
                client.start()
                for tool in client.list_tools():
                    self.tools[tool.full_name] = tool
                self.clients[cfg.name] = client
            except MCPError as exc:
                self.errors.append(str(exc))
                client.stop()

    def tool_specs(self) -> list[dict]:
        return [{"name": t.full_name, "description": t.description} for t in self.tools.values()]

    def call(self, full_name: str, arguments: dict) -> str:
        tool = self.tools.get(full_name)
        if not tool:
            return f"(unknown MCP tool: {full_name})"
        client = self.clients.get(tool.server)
        if not client:
            return f"(MCP server not connected: {tool.server})"
        try:
            return client.call_tool(tool.name, arguments)
        except MCPError as exc:
            return f"(MCP call failed: {exc})"

    def stop_all(self) -> None:
        for client in self.clients.values():
            client.stop()
        self.clients.clear()

    def __enter__(self) -> "MCPManager":
        self.start_all()
        return self

    def __exit__(self, *exc) -> None:
        self.stop_all()

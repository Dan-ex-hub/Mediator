"""Web IDE for Mediator.

An IDE-style single-page app served by FastAPI: a file-tree explorer, a Monaco
code editor (the editor that powers VS Code), and an AI chat panel that runs the
multi-agent debate on the open file.

Run with:  python -m mediator web   (then open http://localhost:8000)

Safety: file browsing/reading/writing is restricted to a chosen workspace root,
and paths are validated to prevent escaping it. This server is intended for local
use only (binds to 127.0.0.1 by default).
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from .agent import refine_prompt
from .client import LLMError
from .config import Config, ConfigError, load_config
from .debate_log import DebateLog
from .orchestrator import Orchestrator, summarize_project
from .util import detect_language

_STATIC = Path(__file__).parent / "static"

# Directories that are noise in a file tree.
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea",
              ".vscode", "dist", "build", ".mypy_cache", ".pytest_cache", "logs"}

# Extension -> Monaco language id.
_MONACO_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".jsx": "javascript", ".java": "java", ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".php": "php", ".c": "c", ".h": "c", ".cpp": "cpp", ".cs": "csharp",
    ".sh": "shell", ".sql": "sql", ".html": "html", ".css": "css", ".kt": "kotlin",
    ".json": "json", ".md": "markdown", ".yml": "yaml", ".yaml": "yaml",
    ".toml": "ini", ".txt": "plaintext", ".xml": "xml",
}

MAX_FILE_BYTES = 2_000_000

# Code-only extensions for folder review (skip docs/data files).
CODE_EXTS = set(_MONACO_LANG) - {".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".xml"}


def _debate_events(cfg: Config, task: str, filename: str, code: str, language: str,
                   refine: bool, file_label: str | None = None):
    """Run a debate in a worker thread, yielding event dicts as they happen."""
    q: queue.Queue = queue.Queue()

    def on_event(ev: dict) -> None:
        if file_label:
            ev = {**ev, "file": file_label}
        q.put(ev)

    def work() -> None:
        try:
            t = task
            if refine:
                t = refine_prompt(cfg, task)
                on_event({"type": "refined", "task": t})
            log = DebateLog(live=False)
            orch = Orchestrator(cfg, log)
            orch.run(t, filename, code, language, on_event=on_event)
            try:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                log.save(Path("logs") / f"{stamp}_{Path(filename).stem}.md",
                         {"file": filename, "task": t,
                          "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            except OSError:
                pass
        except LLMError as exc:
            on_event({"type": "error", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001 - surface any failure to the client
            on_event({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            q.put(None)

    threading.Thread(target=work, daemon=True).start()
    while True:
        ev = q.get()
        if ev is None:
            break
        yield ev


def _ndjson(ev: dict) -> str:
    return json.dumps(ev) + "\n"


def _plan_dict(plan) -> dict:
    return {
        "name": plan.name,
        "stack": plan.stack,
        "files": [{"path": f.path, "purpose": f.purpose} for f in plan.files],
        "setup": plan.setup,
        "run": plan.run,
    }


def _monaco_lang(path: Path) -> str:
    return _MONACO_LANG.get(path.suffix.lower(), "plaintext")


class ReviewRequest(BaseModel):
    code: str = ""
    path: str = ""
    task: str = "Review this code."
    filename: str = "snippet.txt"
    rounds: int | None = None
    refine: bool = False


class RefineRequest(BaseModel):
    text: str = ""


class FolderRequest(BaseModel):
    path: str = ""
    task: str = "Review this code."
    rounds: int | None = None
    refine: bool = False
    max_files: int = 12


class GenerateRequest(BaseModel):
    request: str = ""


class PathRequest(BaseModel):
    path: str = ""


class SaveRequest(BaseModel):
    path: str
    content: str


class MkdirRequest(BaseModel):
    path: str


class TerminalRequest(BaseModel):
    command: str = ""


MAX_SEARCH_RESULTS = 200
_TEXT_EXTS = set(_MONACO_LANG.keys())


def create_app(config_path: str = "config.toml") -> FastAPI:
    app = FastAPI(title="Mediator IDE")
    app.state.root = Path.cwd().resolve()
    app.state.term_proc = None

    def _config():
        return load_config(config_path)

    def _resolve(rel: str) -> Path | None:
        """Resolve a path (relative to root, or absolute) and ensure it stays in root."""
        root: Path = app.state.root
        if not rel:
            return root
        candidate = Path(rel)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            candidate = candidate.resolve()
        except OSError:
            return None
        if candidate == root or root in candidate.parents:
            return candidate
        return None

    def _list_dir(d: Path) -> list[dict]:
        entries: list[dict] = []
        try:
            for child in sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if child.name in _SKIP_DIRS or child.name.startswith("."):
                    continue
                entries.append({
                    "name": child.name,
                    "path": str(child.relative_to(app.state.root)).replace("\\", "/"),
                    "is_dir": child.is_dir(),
                })
        except OSError:
            pass
        return entries

    # -- pages ------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC / "index.html").read_text(encoding="utf-8")

    # -- workspace / files ------------------------------------------------
    @app.get("/api/config")
    def get_config():
        try:
            cfg = _config()
        except ConfigError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        from .profiles import profile_for
        return {
            "max_rounds": cfg.max_rounds,
            "has_cloud": cfg.has_cloud_agent(),
            "root": str(app.state.root),
            "agents": {
                r: {
                    "provider": cfg.agent(r).provider,
                    "model": cfg.agent(r).model or "auto",
                    "is_local": cfg.provider_for(r).is_local,
                    "dialect": profile_for(cfg.agent(r).model).dialect,
                    "profile": profile_for(cfg.agent(r).model).label,
                }
                for r in cfg.agents
            },
        }

    @app.post("/api/open")
    def open_workspace(req: PathRequest):
        p = Path(req.path).expanduser()
        try:
            p = p.resolve()
        except OSError:
            return JSONResponse({"error": "Invalid path."}, status_code=400)
        if not p.is_dir():
            return JSONResponse({"error": f"Not a folder: {req.path}"}, status_code=400)
        app.state.root = p
        return {"root": str(p), "entries": _list_dir(p)}

    @app.get("/api/tree")
    def tree(path: str = ""):
        target = _resolve(path)
        if target is None or not target.is_dir():
            return JSONResponse({"error": "Folder not found."}, status_code=400)
        return {"path": path, "entries": _list_dir(target)}

    @app.get("/api/file")
    def read_file(path: str):
        target = _resolve(path)
        if target is None or not target.is_file():
            return JSONResponse({"error": "File not found."}, status_code=400)
        if target.stat().st_size > MAX_FILE_BYTES:
            return JSONResponse({"error": "File too large to open."}, status_code=400)
        try:
            content = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return JSONResponse({"error": "Cannot open binary or unreadable file."},
                                status_code=400)
        return {"path": path, "content": content, "language": _monaco_lang(target)}

    @app.post("/api/save")
    def save_file(req: SaveRequest):
        target = _resolve(req.path)
        if target is None:
            return JSONResponse({"error": "Invalid path."}, status_code=400)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(req.content, encoding="utf-8")
        except OSError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"saved": req.path}

    @app.post("/api/mkdir")
    def make_dir(req: MkdirRequest):
        target = _resolve(req.path)
        if target is None:
            return JSONResponse({"error": "Invalid path."}, status_code=400)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"created": req.path}

    @app.get("/api/search")
    def search(q: str):
        q = (q or "").strip()
        if len(q) < 2:
            return {"results": []}
        root: Path = app.state.root
        results: list[dict] = []
        ql = q.lower()
        for path in root.rglob("*"):
            if len(results) >= MAX_SEARCH_RESULTS:
                break
            if any(part in _SKIP_DIRS or part.startswith(".") for part in path.parts[len(root.parts):]):
                continue
            if not path.is_file():
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            # Filename match.
            if ql in path.name.lower():
                results.append({"path": rel, "line": 0, "preview": path.name, "kind": "file"})
            # Content match (text files only, bounded).
            if path.suffix.lower() in _TEXT_EXTS and path.stat().st_size <= MAX_FILE_BYTES:
                try:
                    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                        if ql in line.lower():
                            results.append({"path": rel, "line": i,
                                            "preview": line.strip()[:160], "kind": "match"})
                            if len(results) >= MAX_SEARCH_RESULTS:
                                break
                except (UnicodeDecodeError, OSError):
                    pass
        return {"results": results}

    # -- agents -----------------------------------------------------------
    @app.post("/api/refine")
    def api_refine(req: RefineRequest):
        if not req.text.strip():
            return JSONResponse({"error": "Empty request."}, status_code=400)
        try:
            refined = refine_prompt(_config(), req.text)
        except (ConfigError, LLMError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"refined": refined}

    @app.post("/api/review")
    def api_review(req: ReviewRequest):
        try:
            cfg = _config()
        except ConfigError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        filename = req.filename
        code = req.code
        if req.path and not code:
            target = _resolve(req.path)
            if target is None or not target.is_file():
                return JSONResponse({"error": f"File not found: {req.path}"}, status_code=400)
            code = target.read_text(encoding="utf-8")
            filename = target.name
        if not code.strip():
            return JSONResponse({"error": "No code to review."}, status_code=400)

        if req.rounds is not None:
            cfg.max_rounds = req.rounds

        task = req.task
        language = detect_language(Path(filename))
        log = DebateLog(live=False)
        try:
            if req.refine:
                task = refine_prompt(cfg, req.task)
            orch = Orchestrator(cfg, log)
            result = orch.run(task, filename, code, language)
        except LLMError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        med = result.mediator
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        meta = {"file": filename, "task": task,
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "rounds": str(result.rounds_run)}
        try:
            log.save(Path("logs") / f"{stamp}_{Path(filename).stem}.md", meta)
        except OSError:
            pass

        return {
            "task": task,
            "turns": log.to_dicts(),
            "rounds_run": result.rounds_run,
            "stopped_early": result.stopped_early,
            "mediator": None if med is None else {
                "final_code": med.final_code,
                "security": med.security_verdict,
                "requirement": med.requirement_verdict,
                "summary": med.summary,
                "residual_risks": med.residual_risks,
                "raw": med.raw,
            },
        }

    # -- streaming (Phase 9) ---------------------------------------------
    @app.post("/api/review/stream")
    def review_stream(req: ReviewRequest):
        try:
            cfg = _config()
        except ConfigError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        filename, code = req.filename, req.code
        if req.path and not code:
            target = _resolve(req.path)
            if target is None or not target.is_file():
                return JSONResponse({"error": f"File not found: {req.path}"}, status_code=400)
            code = target.read_text(encoding="utf-8")
            filename = target.name
        if not code.strip():
            return JSONResponse({"error": "No code to review."}, status_code=400)
        if req.rounds is not None:
            cfg.max_rounds = req.rounds
        language = detect_language(Path(filename))

        def gen():
            for ev in _debate_events(cfg, req.task, filename, code, language, req.refine):
                yield _ndjson(ev)
            yield _ndjson({"type": "done"})

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    # -- folder review (Phase 8, streamed) -------------------------------
    @app.post("/api/review/folder/stream")
    def folder_stream(req: FolderRequest):
        try:
            cfg = _config()
        except ConfigError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        root = _resolve(req.path) if req.path else app.state.root
        if root is None or not root.is_dir():
            return JSONResponse({"error": "Folder not found."}, status_code=400)
        if req.rounds is not None:
            cfg.max_rounds = req.rounds

        files: list[Path] = []
        for p in sorted(root.rglob("*")):
            rel_parts = p.relative_to(root).parts
            if any(part in _SKIP_DIRS or part.startswith(".") for part in rel_parts):
                continue
            if p.is_file() and p.suffix.lower() in CODE_EXTS:
                try:
                    if p.stat().st_size <= MAX_FILE_BYTES:
                        files.append(p)
                except OSError:
                    pass
            if len(files) >= req.max_files:
                break

        def gen():
            rels = [str(f.relative_to(root)).replace("\\", "/") for f in files]
            yield _ndjson({"type": "plan", "files": rels, "total": len(files),
                           "folder": str(root)})
            if not files:
                yield _ndjson({"type": "done"})
                return
            file_results: list[dict] = []
            for idx, f in enumerate(files, 1):
                rel = str(f.relative_to(root)).replace("\\", "/")
                yield _ndjson({"type": "file_start", "file": rel, "index": idx,
                               "total": len(files)})
                try:
                    code = f.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    yield _ndjson({"type": "file_skip", "file": rel,
                                   "reason": "unreadable/binary"})
                    continue
                language = detect_language(f)
                latest = {"security": "", "requirement": "", "summary": ""}
                for ev in _debate_events(cfg, req.task, f.name, code, language,
                                         req.refine, file_label=rel):
                    if ev.get("type") == "result":
                        latest = {"security": ev.get("security", ""),
                                  "requirement": ev.get("requirement", ""),
                                  "summary": ev.get("summary", "")}
                    yield _ndjson(ev)
                file_results.append({"file": rel, **latest})
                yield _ndjson({"type": "file_done", "file": rel, **latest})

            # Cross-file Mediator pass.
            yield _ndjson({"type": "overall_thinking"})
            try:
                overall = summarize_project(cfg, file_results)
            except LLMError as exc:
                overall = f"(Could not produce overall report: {exc})"
            yield _ndjson({"type": "overall", "content": overall,
                           "results": file_results})
            yield _ndjson({"type": "done"})

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    # -- verification planning (Phase 10): proposes commands, runs NOTHING ---
    @app.post("/api/verify/plan")
    def verify_plan(req: ReviewRequest):
        try:
            cfg = _config()
        except ConfigError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        from .verify import plan_verification

        filename, code = req.filename, req.code
        if req.path and not code:
            target = _resolve(req.path)
            if target is None or not target.is_file():
                return JSONResponse({"error": f"File not found: {req.path}"}, status_code=400)
            code = target.read_text(encoding="utf-8")
            filename = target.name
        if not code.strip():
            return JSONResponse({"error": "No code to verify."}, status_code=400)

        try:
            cmds = plan_verification(cfg, req.task, filename, code,
                                     detect_language(Path(filename)))
        except LLMError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"commands": [{"command": c.command, "why": c.why, "risk": c.risk}
                             for c in cmds]}

    # -- project generation (Phase 11) -----------------------------------
    @app.post("/api/generate/plan")
    def generate_plan(req: GenerateRequest):
        try:
            cfg = _config()
        except ConfigError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not req.request.strip():
            return JSONResponse({"error": "Describe what to build."}, status_code=400)
        from .scaffold import plan_project
        try:
            plan = plan_project(cfg, req.request)
        except LLMError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return _plan_dict(plan)

    @app.post("/api/generate/stream")
    def generate_stream(req: GenerateRequest):
        try:
            cfg = _config()
        except ConfigError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not req.request.strip():
            return JSONResponse({"error": "Describe what to build."}, status_code=400)
        root: Path = app.state.root

        def gen():
            from .scaffold import plan_project, generate_file
            from .verify import classify_risk
            try:
                plan = plan_project(cfg, req.request)
            except LLMError as exc:
                yield _ndjson({"type": "error", "message": str(exc)})
                yield _ndjson({"type": "done"})
                return

            proj_dir = (root / plan.name).resolve()
            yield _ndjson({"type": "plan", **_plan_dict(plan), "folder": plan.name})

            for spec in plan.files:
                target = (proj_dir / spec.path).resolve()
                # Safety: never write outside the workspace root.
                if root != target and root not in target.parents:
                    yield _ndjson({"type": "file_skip", "path": spec.path,
                                   "reason": "path escapes workspace"})
                    continue
                rel = str(target.relative_to(root)).replace("\\", "/")
                yield _ndjson({"type": "file_writing", "path": rel, "purpose": spec.purpose})
                try:
                    content = generate_file(cfg, req.request, plan, spec)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                except (LLMError, OSError) as exc:
                    yield _ndjson({"type": "file_error", "path": rel, "message": str(exc)})
                    continue
                yield _ndjson({"type": "file_written", "path": rel, "bytes": len(content)})

            # Setup/run commands are PROPOSALS — the user approves & runs them.
            cmds = []
            for c in plan.setup + plan.run:
                full = f"cd {plan.name} && {c}"
                cmds.append({"command": full, "why": "setup/run",
                             "risk": classify_risk(full)})
            yield _ndjson({"type": "commands", "commands": cmds})
            yield _ndjson({"type": "done", "folder": plan.name})

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    # -- integrated terminal ---------------------------------------------
    # SECURITY: runs commands the USER types, in the workspace dir, on a
    # localhost-only server. The AI agents have NO terminal access (that is the
    # sandboxed Phase 10 work). Do not expose this server beyond 127.0.0.1.
    @app.post("/api/terminal/run")
    def terminal_run(req: TerminalRequest):
        cmd = (req.command or "").strip()
        if not cmd:
            return JSONResponse({"error": "Empty command."}, status_code=400)
        root: Path = app.state.root

        def gen():
            yield _ndjson({"type": "start", "command": cmd, "cwd": str(root)})
            try:
                proc = subprocess.Popen(
                    cmd, shell=True, cwd=str(root),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, encoding="utf-8", errors="replace",
                )
            except OSError as exc:
                yield _ndjson({"type": "out", "data": f"Failed to start: {exc}\n"})
                yield _ndjson({"type": "exit", "code": -1})
                return
            app.state.term_proc = proc
            try:
                for line in iter(proc.stdout.readline, ""):
                    yield _ndjson({"type": "out", "data": line})
                proc.wait()
            finally:
                code = proc.returncode
                app.state.term_proc = None
            yield _ndjson({"type": "exit", "code": code})

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @app.post("/api/terminal/stop")
    def terminal_stop():
        proc = app.state.term_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
            return {"stopped": True}
        return {"stopped": False}

    return app


def serve(config_path: str = "config.toml", host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn
    uvicorn.run(create_app(config_path), host=host, port=port)

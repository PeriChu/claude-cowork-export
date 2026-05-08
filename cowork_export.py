#!/usr/bin/env python3
"""cowork_export — export Claude Cowork (or Claude Code) sessions to HTML / Markdown / JSON / CSV.

Cowork stores each chat as a "task" under the Claude desktop app's user-data dir:
    macOS:   ~/Library/Application Support/Claude/local-agent-mode-sessions/<acct>/<ws>/
    Windows: %APPDATA%\\Claude\\local-agent-mode-sessions\\<acct>\\<ws>\\
    Linux:   ~/.config/Claude/local-agent-mode-sessions/<acct>/<ws>/

Each task lays out:
    local_<task>.json                            # task metadata
    local_<task>/                                # task working dir
        .claude/projects/<encoded-cwd>/*.jsonl   # transcript (lossless)
        uploads/                                 # user-attached files
        outputs/                                 # files the assistant generated
        audit.jsonl                              # audit log
spaces.json                                      # space (project) registry

This tool (Windows branch) flattens that into HTML / MD / JSON / CSV plus a
snapshot of uploads, outputs, and any other files the assistant wrote. Falls
back to the legacy ~/.claude/projects/ layout when run with --source code.

Usage:
    python cowork_export.py list
    python cowork_export.py export latest
    python cowork_export.py export <task-id-prefix>
    python cowork_export.py export all --output .\\exports
    python cowork_export.py export latest --formats html,md
    python cowork_export.py export latest --source code        # legacy code mode
"""
from __future__ import annotations

import argparse
import csv
import html as html_mod
import json
import os
import shutil
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


HOME = Path.home()


def _detect_cowork_root() -> Path:
    if sys.platform == "win32":
        # 1. %APPDATA%\Claude — covers native installers and most Microsoft
        # Store (MSIX) installs, where Windows publishes a VFS reparse point
        # under %APPDATA%\Claude that mirrors the package's LocalCache.
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else (HOME / "AppData" / "Roaming")
        primary = base / "Claude" / "local-agent-mode-sessions"
        if primary.exists():
            return primary
        # 2. MSIX/Store direct lookup — used when the VFS reparse is missing
        # (reinstall edge cases, enterprise file-system policies, or future
        # MSIX manifests that drop the legacy %APPDATA% mirror).
        local = os.environ.get("LOCALAPPDATA")
        local_base = Path(local) if local else (HOME / "AppData" / "Local")
        for pkg in sorted((local_base / "Packages").glob("Claude_*")):
            cand = pkg / "LocalCache" / "Roaming" / "Claude" / "local-agent-mode-sessions"
            if cand.exists():
                return cand
        return primary
    if sys.platform == "darwin":
        return HOME / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions"
    return HOME / ".config" / "Claude" / "local-agent-mode-sessions"


COWORK_ROOT = _detect_cowork_root()
CODE_ROOT = HOME / ".claude" / "projects"
DEFAULT_OUTPUT = Path.cwd() / "exports"
SUPPORTED_FORMATS = ("html", "md", "json", "csv")
TOOL_RESULT_TRUNCATE = 8000


def _rel_to(abs_p: Path, base: Path) -> str | None:
    """Return abs_p relative to base (forward slashes), or None if not under base.

    On Windows, falls back to a case-insensitive comparison because the
    filesystem is case-insensitive but pathlib.Path.relative_to is strict.
    """
    try:
        return str(abs_p.relative_to(base)).replace("\\", "/")
    except ValueError:
        if sys.platform == "win32":
            try:
                a_str = os.path.abspath(str(abs_p))
                b_str = os.path.abspath(str(base))
                a_norm = os.path.normcase(a_str)
                b_norm = os.path.normcase(b_str)
                if a_norm == b_norm:
                    return ""
                if a_norm.startswith(b_norm + os.sep):
                    return a_str[len(b_str) + 1:].replace("\\", "/")
            except OSError:
                pass
        return None


# ---------------------------------------------------------------------------
# Discovery — Cowork tasks
# ---------------------------------------------------------------------------

@dataclass
class Task:
    source: str
    task_id: str
    title: str = ""
    model: str = ""
    workspace_dir: Path | None = None
    task_meta_file: Path | None = None
    task_dir: Path | None = None
    transcript_path: Path | None = None
    cli_session_id: str = ""
    cwd: str = ""
    initial_message: str = ""
    user_folders: list[str] = field(default_factory=list)
    created_at_ms: int = 0
    last_activity_ms: int = 0
    archived: bool = False
    error: str = ""
    space_name: str = ""
    space_id: str = ""

    @property
    def display_when(self) -> str:
        ts = self.last_activity_ms or self.created_at_ms
        if not ts:
            return ""
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")

    @property
    def display_title(self) -> str:
        return self.title or f"(untitled task {self.task_id[:8]})"


def _load_spaces(workspace_dir: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Return (folder_path -> space_name, project_uuid -> space_name)."""
    by_folder: dict[str, str] = {}
    by_project: dict[str, str] = {}
    sf = workspace_dir / "spaces.json"
    if not sf.exists():
        return by_folder, by_project
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
    except Exception:
        return by_folder, by_project
    for s in data.get("spaces", []):
        name = s.get("name", "")
        for fld in s.get("folders", []) or []:
            p = fld.get("path")
            if p:
                by_folder[p] = name
        for prj in s.get("projects", []) or []:
            u = prj.get("uuid")
            if u:
                by_project[u] = name
    return by_folder, by_project


def _resolve_transcript(task_dir: Path, cli_session_id: str) -> Path | None:
    pdir = task_dir / ".claude" / "projects"
    if not pdir.exists():
        return None
    candidates = list(pdir.glob("*/*.jsonl"))
    if not candidates:
        return None
    if cli_session_id:
        for c in candidates:
            if c.stem == cli_session_id:
                return c
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def discover_cowork_tasks(root: Path = COWORK_ROOT) -> list[Task]:
    if not root.exists():
        return []
    tasks: list[Task] = []
    for acct in sorted(root.iterdir()):
        if not acct.is_dir():
            continue
        for workspace in sorted(acct.iterdir()):
            if not workspace.is_dir():
                continue
            spaces_by_folder, _ = _load_spaces(workspace)
            for meta_file in sorted(workspace.glob("local_*.json")):
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    continue
                task_id = meta.get("sessionId") or meta_file.stem
                if task_id.startswith("local_"):
                    task_id = task_id[len("local_"):]
                task_dir = workspace / f"local_{task_id}"
                cli_id = meta.get("cliSessionId") or ""
                transcript = _resolve_transcript(task_dir, cli_id) if task_dir.exists() else None
                user_folders = meta.get("userSelectedFolders") or []
                space_name = ""
                for f in user_folders:
                    if f in spaces_by_folder:
                        space_name = spaces_by_folder[f]
                        break
                tasks.append(Task(
                    source="cowork",
                    task_id=task_id,
                    title=meta.get("title", "") or "",
                    model=meta.get("model", "") or "",
                    workspace_dir=workspace,
                    task_meta_file=meta_file,
                    task_dir=task_dir if task_dir.exists() else None,
                    transcript_path=transcript,
                    cli_session_id=cli_id,
                    cwd=meta.get("cwd", "") or "",
                    initial_message=meta.get("initialMessage", "") or "",
                    user_folders=list(user_folders),
                    created_at_ms=int(meta.get("createdAt") or 0),
                    last_activity_ms=int(meta.get("lastActivityAt") or 0),
                    archived=bool(meta.get("isArchived")),
                    error=meta.get("error", "") or "",
                    space_name=space_name,
                ))
    tasks.sort(key=lambda t: t.last_activity_ms or t.created_at_ms, reverse=True)
    return tasks


def discover_code_sessions(root: Path = CODE_ROOT) -> list[Task]:
    if not root.exists():
        return []
    out: list[Task] = []
    for proj in sorted(root.iterdir()):
        if not proj.is_dir():
            continue
        for jf in proj.glob("*.jsonl"):
            mtime_ms = int(jf.stat().st_mtime * 1000)
            t = Task(
                source="code",
                task_id=jf.stem,
                workspace_dir=proj,
                transcript_path=jf,
                cli_session_id=jf.stem,
                last_activity_ms=mtime_ms,
                created_at_ms=mtime_ms,
            )
            try:
                with jf.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not t.cwd and obj.get("cwd"):
                            t.cwd = obj["cwd"]
                        if obj.get("type") == "ai-title":
                            t.title = obj.get("aiTitle", "") or t.title
                        if t.title and t.cwd:
                            break
            except OSError:
                pass
            out.append(t)
    out.sort(key=lambda x: x.last_activity_ms, reverse=True)
    return out


def discover(source: str) -> list[Task]:
    if source == "cowork":
        return discover_cowork_tasks()
    if source == "code":
        return discover_code_sessions()
    if source == "both":
        return sorted(
            discover_cowork_tasks() + discover_code_sessions(),
            key=lambda t: t.last_activity_ms or t.created_at_ms,
            reverse=True,
        )
    raise ValueError(f"unknown source: {source}")


def resolve_tasks(selector: str, tasks: list[Task]) -> list[Task]:
    if not tasks:
        return []
    if selector == "all":
        return tasks
    if selector == "latest":
        return [tasks[0]]
    matches = [t for t in tasks if t.task_id.startswith(selector)]
    if matches:
        return matches
    matches = [t for t in tasks if selector in t.task_id]
    return matches


# ---------------------------------------------------------------------------
# Transcript parsing (shared by cowork + code)
# ---------------------------------------------------------------------------

@dataclass
class SessionMeta:
    source: str = ""
    task_id: str = ""
    cli_session_id: str = ""
    title: str = ""
    cwd: str = ""
    git_branch: str = ""
    version: str = ""
    started_at: str = ""
    ended_at: str = ""
    entrypoint: str = ""
    permission_mode: str = ""
    model: str = ""
    initial_message: str = ""
    user_folders: list[str] = field(default_factory=list)
    archived: bool = False
    error: str = ""
    space_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__}


@dataclass
class FlatMessage:
    index: int
    kind: str
    role: str
    timestamp: str
    uuid: str = ""
    parent_uuid: str = ""
    text: str = ""
    tool_name: str = ""
    tool_id: str = ""
    tool_input: Any = None
    is_error: bool = False
    attachment_type: str = ""
    attachment_payload: Any = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__}


def load_transcript(jsonl_path: Path) -> tuple[SessionMeta, list[dict[str, Any]]]:
    meta = SessionMeta()
    raw: list[dict[str, Any]] = []
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw.append(obj)
            if not meta.cli_session_id and obj.get("sessionId"):
                meta.cli_session_id = obj["sessionId"]
            if obj.get("cwd"):
                meta.cwd = obj["cwd"]
            if obj.get("gitBranch"):
                meta.git_branch = obj["gitBranch"]
            if obj.get("version"):
                meta.version = obj["version"]
            if obj.get("entrypoint"):
                meta.entrypoint = obj["entrypoint"]
            if obj.get("permissionMode"):
                meta.permission_mode = obj["permissionMode"]
            ts = obj.get("timestamp")
            if ts:
                if not meta.started_at:
                    meta.started_at = ts
                meta.ended_at = ts
            if obj.get("type") == "ai-title" and obj.get("aiTitle"):
                meta.title = obj["aiTitle"]
    return meta, raw


def merge_task_meta(meta: SessionMeta, task: Task) -> None:
    meta.source = task.source
    meta.task_id = task.task_id
    if task.title:
        meta.title = task.title
    if task.model:
        meta.model = task.model
    if task.initial_message:
        meta.initial_message = task.initial_message
    if task.cwd and not meta.cwd:
        meta.cwd = task.cwd
    meta.user_folders = list(task.user_folders)
    meta.archived = task.archived
    meta.error = task.error
    meta.space_name = task.space_name
    if not meta.started_at and task.created_at_ms:
        meta.started_at = datetime.fromtimestamp(task.created_at_ms / 1000, tz=timezone.utc).isoformat()
    if task.last_activity_ms:
        meta.ended_at = datetime.fromtimestamp(task.last_activity_ms / 1000, tz=timezone.utc).isoformat()


def flatten(raw: list[dict[str, Any]]) -> list[FlatMessage]:
    flat: list[FlatMessage] = []

    def push(**kwargs):
        flat.append(FlatMessage(index=len(flat), **kwargs))

    for obj in raw:
        t = obj.get("type")
        if t in ("queue-operation", "ai-title", "last-prompt"):
            continue
        ts = obj.get("timestamp", "") or ""
        uuid = obj.get("uuid", "") or ""
        parent = obj.get("parentUuid", "") or ""
        if t == "attachment":
            att = obj.get("attachment", {}) or {}
            push(kind="attachment", role="system", timestamp=ts, uuid=uuid, parent_uuid=parent,
                 attachment_type=att.get("type", ""), attachment_payload=att)
            continue
        msg = obj.get("message") or {}
        role = msg.get("role", "?")
        content = msg.get("content")
        if isinstance(content, str):
            push(kind="text", role=role, timestamp=ts, uuid=uuid, parent_uuid=parent, text=content)
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "text":
                push(kind="text", role=role, timestamp=ts, uuid=uuid, parent_uuid=parent, text=block.get("text", ""))
            elif bt == "thinking":
                push(kind="thinking", role=role, timestamp=ts, uuid=uuid, parent_uuid=parent, text=block.get("thinking", ""))
            elif bt == "tool_use":
                push(kind="tool_use", role=role, timestamp=ts, uuid=uuid, parent_uuid=parent,
                     tool_name=block.get("name", ""), tool_id=block.get("id", ""),
                     tool_input=block.get("input"))
            elif bt == "tool_result":
                inner = block.get("content")
                text = ""
                if isinstance(inner, str):
                    text = inner
                elif isinstance(inner, list):
                    parts: list[str] = []
                    for x in inner:
                        if isinstance(x, dict):
                            if x.get("type") == "text":
                                parts.append(x.get("text", ""))
                            elif x.get("type") == "image":
                                parts.append("[image]")
                    text = "\n".join(parts)
                tur = obj.get("toolUseResult") or {}
                tur_meta = (
                    {k: v for k, v in tur.items() if k not in ("stdout", "stderr")}
                    if isinstance(tur, dict)
                    else {}
                )
                stderr = tur.get("stderr") if isinstance(tur, dict) else None
                extra = {}
                if stderr:
                    extra["stderr"] = stderr
                if tur_meta:
                    extra["result_meta"] = tur_meta
                push(kind="tool_result", role=role, timestamp=ts, uuid=uuid, parent_uuid=parent,
                     text=text, tool_id=block.get("tool_use_id", ""),
                     is_error=bool(block.get("is_error")), extra=extra)
            elif bt == "image":
                push(kind="image", role=role, timestamp=ts, uuid=uuid, parent_uuid=parent,
                     extra={"source": block.get("source")})
            else:
                push(kind=bt or "unknown", role=role, timestamp=ts, uuid=uuid, parent_uuid=parent,
                     extra={"raw": block})
    return flat


# ---------------------------------------------------------------------------
# Touched files (Write / Edit / NotebookEdit / MultiEdit)
# ---------------------------------------------------------------------------

@dataclass
class TouchedFile:
    absolute_path: str
    relative_path: str
    op: str
    message_uuid: str
    exists: bool = False
    size: int = 0
    recorded_content: str | None = None
    edit_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d.pop("recorded_content", None)
        d["has_recorded_content"] = self.recorded_content is not None
        return d


def collect_touched_files(flat: list[FlatMessage], cwd: str) -> list[TouchedFile]:
    cwd_p = Path(cwd).resolve() if cwd else None
    seen: dict[str, TouchedFile] = {}
    for m in flat:
        if m.kind != "tool_use":
            continue
        inp = m.tool_input or {}
        if not isinstance(inp, dict):
            continue
        path: str | None = None
        op = m.tool_name
        if m.tool_name in ("Write", "Edit", "MultiEdit"):
            path = inp.get("file_path")
        elif m.tool_name == "NotebookEdit":
            path = inp.get("notebook_path")
        if not path:
            continue
        try:
            abs_p = Path(path).resolve()
        except OSError:
            continue
        rel = ""
        if cwd_p:
            r = _rel_to(abs_p, cwd_p)
            if r is not None:
                rel = r
        key = os.path.normcase(str(abs_p)) if sys.platform == "win32" else str(abs_p)
        tf = seen.get(key)
        if tf is None:
            tf = TouchedFile(absolute_path=str(abs_p), relative_path=rel, op=op, message_uuid=m.uuid)
            seen[key] = tf
        elif op not in tf.op.split("+"):
            tf.op = f"{tf.op}+{op}"
        if m.tool_name == "Write":
            content = inp.get("content")
            if isinstance(content, str):
                tf.recorded_content = content
    out = list(seen.values())
    for tf in out:
        p = Path(tf.absolute_path)
        try:
            if p.exists() and p.is_file():
                tf.exists = True
                tf.size = p.stat().st_size
        except OSError:
            tf.exists = False
        tf.edit_only = (tf.recorded_content is None) and ("Write" not in tf.op.split("+"))
    return out


def list_dir_files(d: Path) -> list[Path]:
    if not d.exists() or not d.is_dir():
        return []
    out = []
    for p in d.rglob("*"):
        if p.is_file() and not p.name.startswith("."):
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _fmt_ts(ts: str) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def render_markdown(
    meta: SessionMeta,
    flat: list[FlatMessage],
    touched: list[TouchedFile],
    uploads: list[Path],
    outputs: list[Path],
    bundle_root: Path,
) -> str:
    out: list[str] = []
    title = meta.title or f"Cowork task {meta.task_id[:8]}"
    out.append(f"# {title}")
    out.append("")
    info = [
        ("Source", meta.source),
        ("Task ID", meta.task_id),
        ("CLI session", meta.cli_session_id),
        ("Space", meta.space_name),
        ("Model", meta.model),
        ("Working dir", meta.cwd),
        ("User folders", ", ".join(meta.user_folders) if meta.user_folders else ""),
        ("Started", _fmt_ts(meta.started_at)),
        ("Ended", _fmt_ts(meta.ended_at)),
        ("Archived", "yes" if meta.archived else ""),
        ("Error", meta.error),
        ("Git branch", meta.git_branch),
        ("Claude Code", meta.version),
    ]
    for label, value in info:
        if value:
            out.append(f"- **{label}:** `{value}`" if label in ("Task ID", "CLI session", "Working dir", "Git branch", "Claude Code") else f"- **{label}:** {value}")
    out.append("")

    if meta.initial_message:
        out.append("## Initial message")
        out.append("")
        out.append("> " + meta.initial_message.strip().replace("\n", "\n> "))
        out.append("")

    if uploads:
        out.append(f"## Uploads ({len(uploads)})")
        out.append("")
        for p in uploads:
            try:
                rel = p.relative_to(bundle_root)
            except ValueError:
                rel = Path("uploads") / p.name
            out.append(f"- [{p.name}]({rel}) — {_human_size(p.stat().st_size)}")
        out.append("")

    if outputs:
        out.append(f"## Outputs ({len(outputs)})")
        out.append("")
        for p in outputs:
            try:
                rel = p.relative_to(bundle_root)
            except ValueError:
                rel = Path("outputs") / p.name
            out.append(f"- [{p.name}]({rel}) — {_human_size(p.stat().st_size)}")
        out.append("")

    if touched:
        out.append("## Files written / edited via tool calls")
        out.append("")
        for tf in touched:
            shown = tf.relative_path or tf.absolute_path
            link = f"[{shown}](assets/{tf.relative_path})" if tf.exists and tf.relative_path else shown
            status = "" if tf.exists else " _(not on disk anymore)_"
            out.append(f"- `{tf.op}` — {link}{status}")
        out.append("")

    out.append("## Transcript")
    out.append("")
    for m in flat:
        ts = _fmt_ts(m.timestamp)
        if m.kind == "text":
            who = m.role.capitalize()
            out.append(f"### {who} · {ts}")
            out.append("")
            out.append(_strip_uploaded_files_wrapper(m.text).rstrip() or "_(empty)_")
            out.append("")
        elif m.kind == "thinking":
            out.append(f"<details><summary>Thinking · {ts}</summary>")
            out.append("")
            out.append(m.text.rstrip())
            out.append("")
            out.append("</details>")
            out.append("")
        elif m.kind == "tool_use":
            inp = json.dumps(m.tool_input, ensure_ascii=False, indent=2) if m.tool_input is not None else ""
            out.append(f"#### Tool call: `{m.tool_name}` · {ts}")
            out.append("")
            out.append("```json")
            out.append(inp)
            out.append("```")
            out.append("")
        elif m.kind == "tool_result":
            tag = "Tool error" if m.is_error else "Tool result"
            out.append(f"<details><summary>{tag} · {ts}</summary>")
            out.append("")
            txt = m.text or ""
            note = ""
            if len(txt) > TOOL_RESULT_TRUNCATE:
                note = f"\n\n_…truncated, full text in JSON export ({len(txt)} chars)_"
                txt = txt[:TOOL_RESULT_TRUNCATE]
            out.append("```")
            out.append(txt.rstrip())
            out.append("```")
            if note:
                out.append(note)
            out.append("")
            out.append("</details>")
            out.append("")
        elif m.kind == "attachment":
            payload = m.attachment_payload or {}
            preview = json.dumps({k: v for k, v in payload.items() if k != "type"}, ensure_ascii=False)[:400]
            out.append(f"<details><summary>attachment · {m.attachment_type} · {ts}</summary>")
            out.append("")
            out.append("```json")
            out.append(preview)
            out.append("```")
            out.append("")
            out.append("</details>")
            out.append("")
        elif m.kind == "image":
            out.append(f"_(image attachment · {ts})_")
            out.append("")
        else:
            out.append(f"_({m.kind} · {ts})_")
            out.append("")
    return "\n".join(out)


def _strip_uploaded_files_wrapper(text: str) -> str:
    if not text or "<uploaded_files>" not in text:
        return text
    start = text.find("<uploaded_files>")
    end = text.find("</uploaded_files>")
    if start == -1 or end == -1:
        return text
    return (text[:start] + text[end + len("</uploaded_files>"):]).strip()


HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/styles/github.min.css">
<script src="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/highlight.min.js"></script>
<style>
:root {{
  --bg: #fafafa; --fg: #1f2328; --muted: #656d76; --border: #d0d7de;
  --user-bg: #ddf4ff; --user-bd: #b6e3ff;
  --asst-bg: #ffffff; --asst-bd: #d0d7de;
  --tool-bg: #fff8c5; --tool-bd: #eac54f;
  --result-bg: #dafbe1; --result-bd: #4ac26b;
  --result-err-bg: #ffebe9; --result-err-bd: #ff8182;
  --thinking-bg: #f3e8ff; --thinking-bd: #c8a2f5;
  --att-bg: #f6f8fa; --att-bd: #d0d7de;
  --code-bg: #0d1117; --code-fg: #e6edf3;
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{
  font: 15px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
        "Helvetica Neue", Arial, sans-serif;
  color: var(--fg); background: var(--bg);
}}
.wrap {{ max-width: 1080px; margin: 32px auto; padding: 0 20px; }}
header.head {{
  background: #fff; border: 1px solid var(--border); border-radius: 10px;
  padding: 20px 24px; margin-bottom: 24px;
}}
header.head h1 {{ margin: 0 0 12px; font-size: 22px; }}
header.head dl {{
  display: grid; grid-template-columns: max-content 1fr;
  gap: 4px 16px; margin: 0; font-size: 13px; color: var(--muted);
}}
header.head dt {{ font-weight: 600; color: var(--fg); }}
header.head dd {{ margin: 0; word-break: break-all; }}
header.head dd.mono {{ font-family: ui-monospace, Menlo, Consolas, monospace; }}
header.head .err {{ color: #cf222e; }}
.initial {{
  background: #fff; border: 1px solid var(--border); border-radius: 10px;
  padding: 14px 20px; margin-bottom: 24px;
}}
.initial h2 {{ margin: 0 0 8px; font-size: 14px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }}
.initial blockquote {{ margin: 0; padding-left: 12px; border-left: 3px solid var(--border); color: var(--fg); white-space: pre-wrap; font-size: 14px; }}
.section {{
  background: #fff; border: 1px solid var(--border); border-radius: 10px;
  padding: 14px 20px; margin-bottom: 24px;
}}
.section h2 {{ margin: 0 0 10px; font-size: 15px; }}
.section ul {{ margin: 0; padding-left: 22px; font-size: 13px; }}
.section li {{ margin: 3px 0; font-family: ui-monospace, Menlo, Consolas, monospace; }}
.section .op {{
  display: inline-block; padding: 1px 6px; margin-right: 6px;
  border-radius: 4px; background: #eee; font-size: 11px;
}}
.section .size {{ color: var(--muted); font-size: 12px; margin-left: 6px; }}
.toc {{
  background: #fff; border: 1px solid var(--border); border-radius: 10px;
  padding: 12px 20px 14px; margin-bottom: 24px;
}}
.toc h2 {{ margin: 0 0 8px; font-size: 14px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }}
.toc ol {{ margin: 0; padding-left: 22px; font-size: 13px; }}
.toc a {{ color: #0969da; text-decoration: none; }}
.toc a:hover {{ text-decoration: underline; }}
.msg {{
  border: 1px solid; border-radius: 10px; padding: 14px 18px;
  margin-bottom: 16px; background: #fff; overflow: hidden;
}}
.msg.user {{ background: var(--user-bg); border-color: var(--user-bd); }}
.msg.assistant {{ background: var(--asst-bg); border-color: var(--asst-bd); }}
.msg.thinking {{ background: var(--thinking-bg); border-color: var(--thinking-bd); border-style: dashed; }}
.msg.tool_use {{ background: var(--tool-bg); border-color: var(--tool-bd); }}
.msg.tool_result {{ background: var(--result-bg); border-color: var(--result-bd); }}
.msg.tool_result.error {{ background: var(--result-err-bg); border-color: var(--result-err-bd); }}
.msg.attachment, .msg.image, .msg.unknown {{
  background: var(--att-bg); border-color: var(--att-bd);
  color: var(--muted); font-size: 13px;
}}
.msg-head {{
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 10px; font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}}
.msg-head .role {{ font-weight: 700; }}
.msg-body {{ font-size: 15px; }}
.msg-body > *:first-child {{ margin-top: 0; }}
.msg-body > *:last-child {{ margin-bottom: 0; }}
.md p, .md ul, .md ol, .md blockquote {{ margin: 0.5em 0; }}
.md h1, .md h2, .md h3, .md h4 {{ margin: 0.6em 0 0.3em; }}
.md ul, .md ol {{ padding-left: 1.6em; }}
.md blockquote {{ border-left: 3px solid var(--border); padding: 0 12px; color: var(--muted); margin-left: 0; }}
.md pre {{
  background: var(--code-bg); color: var(--code-fg);
  padding: 12px 14px; border-radius: 6px; overflow-x: auto;
  max-height: 520px; margin: 0.5em 0;
}}
.md pre code {{ background: transparent; padding: 0; color: inherit; }}
.md :not(pre) > code {{ background: rgba(175,184,193,0.2); padding: 1px 5px; border-radius: 4px; font-size: 0.9em; }}
.md table {{ border-collapse: collapse; margin: 0.6em 0; max-width: 100%; display: block; overflow-x: auto; }}
.md th, .md td {{ border: 1px solid var(--border); padding: 4px 8px; }}
.md th {{ background: #f6f8fa; }}
.md img {{ max-width: 100%; height: auto; }}
code, pre {{ font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; }}
details {{ margin: 6px 0; }}
details > summary {{ cursor: pointer; color: #444; font-weight: 600; user-select: none; }}
details[open] > summary {{ margin-bottom: 8px; }}
.tool-name {{
  display: inline-block; padding: 2px 8px; border-radius: 4px;
  background: rgba(0,0,0,0.08); font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 13px;
}}
.truncated {{ color: var(--muted); font-style: italic; font-size: 12px; margin-top: 6px; }}
pre.raw {{
  background: var(--code-bg); color: var(--code-fg);
  padding: 12px 14px; border-radius: 6px; overflow-x: auto;
  max-height: 520px; margin: 0; white-space: pre-wrap; word-break: break-word;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #0d1117; --fg: #e6edf3; --muted: #8b949e; --border: #30363d;
    --user-bg: #0c2d6b; --user-bd: #1f6feb;
    --asst-bg: #161b22; --asst-bd: #30363d;
    --tool-bg: #3d2e00; --tool-bd: #8b6914;
    --result-bg: #04260f; --result-bd: #2ea043;
    --result-err-bg: #3d0e0e; --result-err-bd: #f85149;
    --thinking-bg: #2d1b4e; --thinking-bd: #8957e5;
    --att-bg: #161b22; --att-bd: #30363d;
  }}
  body {{ background: var(--bg); }}
  header.head, .initial, .section, .toc {{ background: #161b22; }}
  .md th {{ background: #161b22; }}
  .md :not(pre) > code {{ background: rgba(110,118,129,0.4); }}
  .toc a {{ color: #58a6ff; }}
}}
</style>
</head>
<body>
<div class="wrap">
{header}
{initial}
{uploads}
{outputs}
{touched}
{toc}
{messages}
</div>
<script>
(function () {{
  const opts = {{ gfm: true, breaks: true, headerIds: false, mangle: false }};
  if (window.marked && marked.setOptions) marked.setOptions(opts);
  document.querySelectorAll('.md').forEach(el => {{
    const raw = el.textContent;
    el.innerHTML = window.marked ? marked.parse(raw, opts) : raw;
  }});
  if (window.hljs) {{
    document.querySelectorAll('pre code').forEach(el => {{
      try {{ hljs.highlightElement(el); }} catch (e) {{}}
    }});
  }}
}})();
</script>
</body>
</html>
"""


def render_html(
    meta: SessionMeta,
    flat: list[FlatMessage],
    touched: list[TouchedFile],
    uploads: list[Path],
    outputs: list[Path],
    bundle_root: Path,
) -> str:
    esc = html_mod.escape
    title = meta.title or f"Cowork task {meta.task_id[:8]}"

    rows: list[tuple[str, str, bool]] = [
        ("Source", meta.source, False),
        ("Task ID", meta.task_id, True),
        ("CLI session", meta.cli_session_id, True),
        ("Space", meta.space_name, False),
        ("Model", meta.model, False),
        ("Working dir", meta.cwd, True),
        ("User folders", ", ".join(meta.user_folders), True),
        ("Started", _fmt_ts(meta.started_at), False),
        ("Ended", _fmt_ts(meta.ended_at), False),
        ("Archived", "yes" if meta.archived else "", False),
        ("Error", meta.error, False),
        ("Git branch", meta.git_branch, True),
        ("Claude Code", meta.version, True),
    ]
    head_parts = [f"<header class='head'><h1>{esc(title)}</h1><dl>"]
    for label, value, mono in rows:
        if not value:
            continue
        cls = " class='mono'" if mono else ""
        if label == "Error":
            cls = " class='err'"
        head_parts.append(f"<dt>{esc(label)}</dt><dd{cls}>{esc(str(value))}</dd>")
    head_parts.append("</dl></header>")

    initial_html = ""
    if meta.initial_message:
        initial_html = (
            "<div class='initial'><h2>Initial message</h2>"
            f"<blockquote>{esc(meta.initial_message)}</blockquote></div>"
        )

    def _file_section(label: str, paths: list[Path]) -> str:
        if not paths:
            return ""
        items = []
        for p in paths:
            try:
                rel = p.relative_to(bundle_root)
            except ValueError:
                continue
            href = "/".join(html_mod.escape(seg, quote=True) for seg in rel.parts)
            size = _human_size(p.stat().st_size) if p.exists() else ""
            items.append(
                f"<li><a href=\"{href}\">{esc(p.name)}</a>"
                f"<span class='size'>{esc(size)}</span></li>"
            )
        return f"<div class='section'><h2>{esc(label)} ({len(paths)})</h2><ul>{''.join(items)}</ul></div>"

    uploads_html = _file_section("Uploads", uploads)
    outputs_html = _file_section("Outputs", outputs)

    touched_html = ""
    if touched:
        items = []
        for tf in touched:
            shown = esc(tf.relative_path or tf.absolute_path)
            if tf.exists and tf.relative_path:
                href = "assets/" + "/".join(html_mod.escape(seg, quote=True) for seg in tf.relative_path.split("/"))
                link = f"<a href=\"{href}\">{shown}</a>"
            else:
                link = shown
            status = "" if tf.exists else " <em>(not on disk anymore)</em>"
            items.append(f"<li><span class='op'>{esc(tf.op)}</span>{link}{status}</li>")
        touched_html = (
            "<div class='section'><h2>Files written / edited via tool calls</h2>"
            f"<ul>{''.join(items)}</ul></div>"
        )

    toc_items = []
    for m in flat:
        if m.kind == "text" and m.role == "user":
            preview = _strip_uploaded_files_wrapper(m.text).strip().splitlines()
            preview = preview[0] if preview else "(empty)"
            preview = preview[:80] + ("…" if len(preview) > 80 else "")
            toc_items.append(f"<li><a href=\"#m{m.index}\">{esc(preview)}</a></li>")
    toc_html = ""
    if toc_items:
        toc_html = f"<div class='toc'><h2>User prompts</h2><ol>{''.join(toc_items)}</ol></div>"

    parts: list[str] = []
    for m in flat:
        ts = esc(_fmt_ts(m.timestamp))
        anchor = f"m{m.index}"
        if m.kind == "text":
            klass = "user" if m.role == "user" else "assistant"
            label = m.role.capitalize()
            text = _strip_uploaded_files_wrapper(m.text)
            body = f"<div class='md'>{esc(text)}</div>"
            parts.append(_msg_html(anchor, klass, label, ts, body))
        elif m.kind == "thinking":
            body = f"<details><summary>Reasoning</summary><div class='md'>{esc(m.text)}</div></details>"
            parts.append(_msg_html(anchor, "thinking", "thinking", ts, body))
        elif m.kind == "tool_use":
            tn = esc(m.tool_name)
            inp = json.dumps(m.tool_input, ensure_ascii=False, indent=2) if m.tool_input is not None else ""
            body = (
                f"<div><span class='tool-name'>{tn}</span></div>"
                f"<details><summary>Input</summary>"
                f"<pre><code class='language-json'>{esc(inp)}</code></pre></details>"
            )
            parts.append(_msg_html(anchor, "tool_use", "tool call", ts, body))
        elif m.kind == "tool_result":
            klass = "tool_result error" if m.is_error else "tool_result"
            label = "tool error" if m.is_error else "tool result"
            txt = m.text or ""
            note = ""
            if len(txt) > TOOL_RESULT_TRUNCATE:
                note = (
                    f"<div class='truncated'>…truncated, full text in JSON export "
                    f"({len(txt)} chars)</div>"
                )
                txt = txt[:TOOL_RESULT_TRUNCATE]
            body = f"<details open><summary>Output</summary><pre class='raw'>{esc(txt)}</pre>{note}</details>"
            parts.append(_msg_html(anchor, klass, label, ts, body))
        elif m.kind == "attachment":
            atype = esc(m.attachment_type)
            payload = m.attachment_payload or {}
            preview = json.dumps({k: v for k, v in payload.items() if k != "type"}, ensure_ascii=False)
            if len(preview) > 600:
                preview = preview[:600] + " …"
            body = f"<details><summary>attachment · {atype}</summary><pre class='raw'>{esc(preview)}</pre></details>"
            parts.append(_msg_html(anchor, "attachment", "attachment", ts, body))
        elif m.kind == "image":
            parts.append(_msg_html(anchor, "image", "image", ts, "<em>(image attachment)</em>"))
        else:
            parts.append(_msg_html(anchor, "unknown", esc(m.kind), ts, "<em>unhandled block</em>"))

    return HTML_TEMPLATE.format(
        title=esc(title),
        header="".join(head_parts),
        initial=initial_html,
        uploads=uploads_html,
        outputs=outputs_html,
        touched=touched_html,
        toc=toc_html,
        messages="".join(parts),
    )


def _msg_html(anchor: str, klass: str, label: str, ts: str, body: str) -> str:
    return (
        f"<div class='msg {klass}' id='{anchor}'>"
        f"<div class='msg-head'><span class='role'>{label}</span><span>{ts}</span></div>"
        f"<div class='msg-body'>{body}</div></div>"
    )


def render_json(
    meta: SessionMeta,
    flat: list[FlatMessage],
    touched: list[TouchedFile],
    uploads: list[Path],
    outputs: list[Path],
    bundle_root: Path,
) -> str:
    def file_entry(p: Path) -> dict[str, Any]:
        try:
            rel = str(p.relative_to(bundle_root))
        except ValueError:
            rel = p.name
        return {
            "name": p.name,
            "relative_path": rel,
            "size": p.stat().st_size if p.exists() else 0,
        }

    payload = {
        "meta": meta.to_dict(),
        "uploads": [file_entry(p) for p in uploads],
        "outputs": [file_entry(p) for p in outputs],
        "files": [tf.to_dict() for tf in touched],
        "messages": [m.to_dict() for m in flat],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def write_csv(path: Path, flat: list[FlatMessage]) -> None:
    cols = [
        "index", "timestamp", "role", "kind",
        "tool_name", "tool_id", "is_error",
        "preview", "content", "tool_input_json",
        "uuid", "parent_uuid",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for m in flat:
            content = m.text
            if m.kind == "tool_use":
                content = json.dumps(m.tool_input, ensure_ascii=False) if m.tool_input is not None else ""
            elif m.kind == "attachment":
                content = json.dumps(m.attachment_payload, ensure_ascii=False) if m.attachment_payload is not None else ""
            preview_src = m.text if m.kind != "tool_use" else (m.tool_name or "")
            preview = (preview_src or "").strip().replace("\n", " ")[:200]
            w.writerow([
                m.index, m.timestamp, m.role, m.kind,
                m.tool_name, m.tool_id, "1" if m.is_error else "",
                preview, content,
                json.dumps(m.tool_input, ensure_ascii=False) if m.tool_input is not None else "",
                m.uuid, m.parent_uuid,
            ])


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def export_one(
    task: Task,
    output_root: Path,
    formats: Iterable[str],
    include_files: bool,
) -> Path | None:
    if not task.transcript_path or not task.transcript_path.exists():
        print(f"  warn: skipping {task.task_id} — no transcript file", file=sys.stderr)
        return None

    meta, raw = load_transcript(task.transcript_path)
    merge_task_meta(meta, task)
    flat = flatten(raw)
    touched = collect_touched_files(flat, meta.cwd) if include_files else []

    target = output_root / task.task_id
    target.mkdir(parents=True, exist_ok=True)

    shutil.copy2(task.transcript_path, target / "transcript.jsonl")
    if task.task_meta_file and task.task_meta_file.exists():
        shutil.copy2(task.task_meta_file, target / "task.json")
    audit_src = (task.task_dir / "audit.jsonl") if task.task_dir else None
    if audit_src and audit_src.exists():
        shutil.copy2(audit_src, target / "audit.jsonl")

    uploads_paths: list[Path] = []
    outputs_paths: list[Path] = []
    if include_files and task.task_dir:
        uploads_src = task.task_dir / "uploads"
        outputs_src = task.task_dir / "outputs"
        if uploads_src.exists():
            for f in list_dir_files(uploads_src):
                rel = f.relative_to(uploads_src)
                dest = target / "uploads" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(f, dest)
                    uploads_paths.append(dest)
                except OSError as e:
                    print(f"  warn: failed to copy {f}: {e}", file=sys.stderr)
        if outputs_src.exists():
            for f in list_dir_files(outputs_src):
                rel = f.relative_to(outputs_src)
                dest = target / "outputs" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(f, dest)
                    outputs_paths.append(dest)
                except OSError as e:
                    print(f"  warn: failed to copy {f}: {e}", file=sys.stderr)

    if include_files:
        cwd_p = Path(meta.cwd).resolve() if meta.cwd else None
        outputs_bundle = target / "outputs"
        for tf in touched:
            src = Path(tf.absolute_path)
            inside_cwd = False
            if cwd_p:
                try:
                    inside_cwd = _rel_to(src.resolve(), cwd_p) is not None
                except OSError:
                    inside_cwd = False
            if inside_cwd and outputs_bundle.exists():
                continue
            asset_rel = tf.relative_path or src.name
            target_path = target / "assets" / asset_rel
            target_path.parent.mkdir(parents=True, exist_ok=True)
            copied = False
            if tf.exists:
                try:
                    shutil.copy2(src, target_path)
                    copied = True
                except OSError:
                    copied = False
            if not copied and tf.recorded_content is not None:
                try:
                    target_path.write_text(tf.recorded_content, encoding="utf-8")
                    copied = True
                except OSError as e:
                    print(f"  warn: failed to write recorded content for {asset_rel}: {e}", file=sys.stderr)
            if not copied:
                if tf.edit_only:
                    print(
                        f"  note: {asset_rel} only had Edit/MultiEdit calls and is not readable; "
                        "skipping (no recorded full content available)",
                        file=sys.stderr,
                    )
                else:
                    print(f"  warn: could not snapshot {asset_rel}", file=sys.stderr)

    formats = list(formats)
    if "html" in formats:
        (target / "session.html").write_text(
            render_html(meta, flat, touched, uploads_paths, outputs_paths, target),
            encoding="utf-8",
        )
    if "md" in formats:
        (target / "session.md").write_text(
            render_markdown(meta, flat, touched, uploads_paths, outputs_paths, target),
            encoding="utf-8",
        )
    if "json" in formats:
        (target / "session.json").write_text(
            render_json(meta, flat, touched, uploads_paths, outputs_paths, target),
            encoding="utf-8",
        )
    if "csv" in formats:
        write_csv(target / "session.csv", flat)

    _write_readme(target, task, meta, flat, touched, uploads_paths, outputs_paths, formats)
    return target


def _write_readme(
    target: Path,
    task: Task,
    meta: SessionMeta,
    flat: list[FlatMessage],
    touched: list[TouchedFile],
    uploads: list[Path],
    outputs: list[Path],
    formats: list[str],
) -> None:
    n_user = sum(1 for m in flat if m.kind == "text" and m.role == "user")
    n_asst = sum(1 for m in flat if m.kind == "text" and m.role == "assistant")
    n_tool = sum(1 for m in flat if m.kind == "tool_use")
    lines = [
        f"# {meta.title or task.task_id}",
        "",
        f"- Source: `{meta.source}`",
        f"- Task ID: `{meta.task_id}`",
        f"- CLI session: `{meta.cli_session_id}`",
    ]
    if meta.space_name:
        lines.append(f"- Space: {meta.space_name}")
    if meta.model:
        lines.append(f"- Model: {meta.model}")
    if meta.cwd:
        lines.append(f"- Working dir: `{meta.cwd}`")
    if meta.started_at:
        lines.append(f"- Started: {_fmt_ts(meta.started_at)}")
    if meta.ended_at:
        lines.append(f"- Ended: {_fmt_ts(meta.ended_at)}")
    lines.append(
        f"- Messages: {len(flat)} blocks ({n_user} user, {n_asst} assistant, {n_tool} tool calls)"
    )
    if uploads:
        lines.append(f"- Uploads: {len(uploads)}")
    if outputs:
        lines.append(f"- Outputs: {len(outputs)}")
    if touched:
        lines.append(f"- Files written/edited: {len(touched)}")
    lines += ["", "## Files in this bundle", ""]
    if "html" in formats:
        lines.append("- `session.html` — formatted reading view (open in a browser)")
    if "md" in formats:
        lines.append("- `session.md` — Markdown export")
    if "json" in formats:
        lines.append("- `session.json` — structured export for LLM consumption")
    if "csv" in formats:
        lines.append("- `session.csv` — flat per-block table")
    lines.append("- `transcript.jsonl` — raw JSONL transcript (lossless source)")
    if (target / "task.json").exists():
        lines.append("- `task.json` — original Cowork task metadata")
    if (target / "audit.jsonl").exists():
        lines.append("- `audit.jsonl` — Cowork audit log")
    if uploads:
        lines.append("- `uploads/` — files the user attached to this chat")
    if outputs:
        lines.append("- `outputs/` — files the assistant generated (Cowork output dir)")
    if touched:
        lines.append("- `assets/` — files written/edited outside the outputs dir")
    (target / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_list(args: argparse.Namespace) -> int:
    tasks = discover(args.source)
    if not tasks:
        root = COWORK_ROOT if args.source != "code" else CODE_ROOT
        print(f"No sessions found under {root}")
        return 0
    for t in tasks:
        title = t.display_title
        if len(title) > 60:
            title = title[:57] + "…"
        archived = " [archived]" if t.archived else ""
        space = f" · {t.space_name}" if t.space_name else ""
        model = f" · {t.model}" if t.model else ""
        when = t.display_when
        ttype = t.source
        print(f"{t.task_id}  [{ttype}]  {when}  {title}{space}{model}{archived}")
        if t.cwd:
            print(f"   cwd: {t.cwd}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    invalid = [f for f in formats if f not in SUPPORTED_FORMATS]
    if invalid:
        print(f"error: unknown format(s): {', '.join(invalid)}", file=sys.stderr)
        return 2

    tasks = discover(args.source)
    if not tasks:
        root = COWORK_ROOT if args.source != "code" else CODE_ROOT
        print(f"No sessions found under {root}", file=sys.stderr)
        return 1

    targets = resolve_tasks(args.session, tasks)
    if not targets:
        print(f"No session matched '{args.session}'", file=sys.stderr)
        return 1
    if len(targets) > 1 and args.session != "all":
        print(f"Ambiguous selector '{args.session}' matched {len(targets)} tasks:", file=sys.stderr)
        for t in targets:
            print(f"  {t.task_id}  {t.display_title}", file=sys.stderr)
        return 1

    output_root = Path(args.output).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    exported = 0
    for task in targets:
        target = export_one(task, output_root, formats, include_files=not args.no_files)
        if target:
            print(f"exported {task.task_id} → {target}")
            exported += 1
    if not exported:
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cowork_export",
        description="Export Claude Cowork (and Code) sessions to HTML, Markdown, JSON, CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              cowork_export.py list
              cowork_export.py list --source code
              cowork_export.py export latest
              cowork_export.py export <task-id> --output ./exports
              cowork_export.py export all --formats html,json
              cowork_export.py export latest --source code
        """),
    )
    p.add_argument(
        "--source",
        default="cowork",
        choices=("cowork", "code", "both"),
        help="which session store to read (default: cowork)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list available sessions").set_defaults(func=cmd_list)

    pe = sub.add_parser("export", help="export one or more sessions")
    pe.add_argument("session", help="task id (prefix), 'latest', or 'all'")
    pe.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"output directory (default: {DEFAULT_OUTPUT})")
    pe.add_argument(
        "--formats",
        default=",".join(SUPPORTED_FORMATS),
        help=f"comma-separated subset of {','.join(SUPPORTED_FORMATS)} (default: all)",
    )
    pe.add_argument("--no-files", action="store_true", help="skip copying uploads / outputs / touched files")
    pe.set_defaults(func=cmd_export)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

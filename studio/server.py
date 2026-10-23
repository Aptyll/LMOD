"""Skin Studio: a local web page for watching skin work happen live.

Run it on the PC that runs Blender:

    python studio/server.py            then open http://localhost:7777

It only listens on 127.0.0.1 and uses only the Python standard library.

What it shows:
  * Claude activity  - tails the newest Claude Code session log (~/.claude/projects/*/*.jsonl)
  * Live 3D model    - the newest .glb in studio/data/models (or uploaded by the Blender add-on)
  * Blender viewport - frames posted by the Blender add-on (studio/blender/studio_stream.py)
  * Gallery          - images in studio/data/gallery, ../LMOD_previews/previews, renders/
                       and work/<skin>/previews/

Nothing here is ever uploaded anywhere. Riot-derived files stay on this PC.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

STUDIO = Path(__file__).resolve().parent
REPO = STUDIO.parent
WEB = STUDIO / "web"
DATA = STUDIO / "data"

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MODEL_EXT = {".glb"}
MAX_UPLOAD = 64 * 1024 * 1024
MAX_EVENTS = 400

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("model/gltf-binary", ".glb")


# --------------------------------------------------------------------------- hub


class Hub:
    """Holds the latest state and fans events out to every open page (SSE)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.clients: list[queue.Queue] = []
        self.events: deque[dict] = deque(maxlen=MAX_EVENTS)
        self.state: dict = {
            "session": None,
            "step": "",
            "viewport": None,
            "blender": None,
            "model": None,
            "gallery": [],
            "last_activity": 0,
        }

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def snapshot(self) -> dict:
        with self.lock:
            return {"state": dict(self.state), "events": list(self.events)}

    def publish(self, msg: dict) -> None:
        with self.lock:
            clients = list(self.clients)
        for q in clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass  # a stuck tab just misses messages; it resyncs on reload

    def add_events(self, events: list[dict]) -> None:
        if not events:
            return
        with self.lock:
            self.events.extend(events)
            self.state["last_activity"] = time.time()
        self.publish({"type": "events", "events": events})

    def set_state(self, **changes) -> None:
        with self.lock:
            self.state.update(changes)
        self.publish({"type": "state", "state": changes})

    def clear_events(self) -> None:
        with self.lock:
            self.events.clear()
        self.publish({"type": "reset"})


HUB = Hub()


# --------------------------------------------------------------- Claude activity


def short(text: str, n: int = 180) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def tool_label(name: str) -> str:
    """mcp__remote-devices__Blender__execute_blender_code -> blender.execute_blender_code"""
    if name.startswith("mcp__"):
        parts = [p for p in name.split("__")[1:] if p]
        server = parts[-2] if len(parts) >= 2 else parts[0]
        return f"{server.lower()}.{parts[-1]}"
    return name.lower()


def tool_summary(name: str, args: dict) -> str:
    if not isinstance(args, dict):
        return short(args)
    for key in ("description", "command", "file_path", "path", "pattern", "url", "query", "prompt"):
        if key in args and isinstance(args[key], str):
            first = args[key]
            if key == "command":
                first = first.strip().splitlines()[0] if first.strip() else first
            return short(first)
    code = args.get("code") or args.get("python_code")
    if isinstance(code, str):
        lines = [l.strip() for l in code.splitlines() if l.strip()]
        comments = [l.lstrip("# ") for l in lines if l.startswith("#")]
        body = [l for l in lines if not l.startswith(("#", "import ", "from "))]
        return short(comments[0] if comments else (body[0] if body else "python"))
    for v in args.values():
        if isinstance(v, str) and v:
            return short(v)
    return ""


def transcript_events(line: dict) -> list[dict]:
    """Turn one Claude Code log line into zero or more feed events."""
    kind = line.get("type")
    msg = line.get("message") or {}
    content = msg.get("content")
    ts = line.get("timestamp") or ""
    out: list[dict] = []
    if line.get("isSidechain"):
        return out
    if kind == "assistant" and isinstance(content, list):
        for block in content:
            t = block.get("type")
            if t == "text" and block.get("text", "").strip():
                out.append({"kind": "note", "ts": ts, "text": short(block["text"], 600)})
            elif t == "tool_use":
                name = block.get("name", "")
                out.append(
                    {
                        "kind": "tool",
                        "ts": ts,
                        "id": block.get("id"),
                        "tool": tool_label(name),
                        "text": tool_summary(name, block.get("input") or {}),
                    }
                )
    elif kind == "user" and not line.get("isMeta"):
        if isinstance(content, str) and content.strip() and not content.lstrip().startswith("<"):
            out.append({"kind": "user", "ts": ts, "text": short(content, 400)})
        elif isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_result":
                    out.append(
                        {
                            "kind": "result",
                            "ts": ts,
                            "id": block.get("tool_use_id"),
                            "error": bool(block.get("is_error")),
                        }
                    )
                elif block.get("type") == "text" and not block.get("text", "").lstrip().startswith("<"):
                    out.append({"kind": "user", "ts": ts, "text": short(block.get("text", ""), 400)})
    return out


class TranscriptTailer(threading.Thread):
    """Follows the most recently written Claude Code session log."""

    def __init__(self, root: Path, match: str, backlog: int = 60) -> None:
        super().__init__(daemon=True)
        self.root = root
        self.match = match.lower()
        self.backlog = backlog
        self.path: Path | None = None
        self.pos = 0
        self.buf = b""

    def newest(self) -> Path | None:
        if not self.root.is_dir():
            return None
        best, best_m = None, -1.0
        for d in self.root.iterdir():
            if not d.is_dir() or (self.match and self.match not in d.name.lower()):
                continue
            for f in d.glob("*.jsonl"):
                try:
                    m = f.stat().st_mtime
                except OSError:
                    continue
                if m > best_m:
                    best, best_m = f, m
        return best

    def switch(self, path: Path) -> None:
        self.path, self.buf = path, b""
        HUB.clear_events()
        # Show the tail end of what already happened, then follow new lines.
        size = path.stat().st_size
        start = max(0, size - 2_000_000)
        with path.open("rb") as fh:
            fh.seek(start)
            data = fh.read()
        self.pos = start + len(data)
        lines = data.split(b"\n")
        if start > 0:
            lines = lines[1:]  # first line is probably cut in half
        self.buf = lines.pop() if lines and not data.endswith(b"\n") else b""
        events: list[dict] = []
        for raw in lines:
            events.extend(self.parse(raw))
        HUB.add_events(events[-self.backlog :])
        HUB.set_state(session={"name": path.stem[:8], "project": path.parent.name})

    @staticmethod
    def parse(raw: bytes) -> list[dict]:
        raw = raw.strip()
        if not raw:
            return []
        try:
            return transcript_events(json.loads(raw))
        except (ValueError, AttributeError, TypeError):
            return []

    def poll(self) -> None:
        assert self.path is not None
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size < self.pos:  # file was replaced
            self.switch(self.path)
            return
        if size == self.pos:
            return
        with self.path.open("rb") as fh:
            fh.seek(self.pos)
            data = fh.read()
        self.pos += len(data)
        data = self.buf + data
        lines = data.split(b"\n")
        self.buf = lines.pop()
        events: list[dict] = []
        for raw in lines:
            events.extend(self.parse(raw))
        HUB.add_events(events)

    def run(self) -> None:
        last_scan = 0.0
        while True:
            try:
                now = time.time()
                if now - last_scan > 3:
                    last_scan = now
                    newest = self.newest()
                    if newest and newest != self.path:
                        self.switch(newest)
                if self.path:
                    self.poll()
            except Exception as exc:  # keep the page alive whatever the log does
                print(f"[studio] transcript: {exc}")
            time.sleep(0.4)


# ------------------------------------------------------------------ files


class FileWatcher(threading.Thread):
    """Polls the gallery and model folders and tells the page when they change."""

    def __init__(self, gallery_dirs: list[Path], model_dirs: list[Path]) -> None:
        super().__init__(daemon=True)
        self.gallery_dirs = gallery_dirs
        self.model_dirs = model_dirs
        self.last_gallery: list = []
        self.last_model = None

    @staticmethod
    def scan(dirs: list[Path], exts: set[str], depth: int = 3) -> list[tuple[float, int, Path]]:
        found = []
        for i, root in enumerate(dirs):
            if not root.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                rel_depth = len(Path(dirpath).relative_to(root).parts)
                if rel_depth >= depth:
                    dirnames[:] = []
                for fn in filenames:
                    p = Path(dirpath) / fn
                    if p.suffix.lower() in exts:
                        try:
                            found.append((p.stat().st_mtime, i, p))
                        except OSError:
                            pass
        found.sort(key=lambda x: x[0], reverse=True)
        return found

    def run(self) -> None:
        while True:
            try:
                imgs = self.scan(self.gallery_dirs, IMAGE_EXT)[:80]
                gallery = [
                    {
                        "url": f"/files/g{i}/{p.relative_to(self.gallery_dirs[i]).as_posix()}?v={int(m)}",
                        "name": p.name,
                        "mtime": m,
                    }
                    for m, i, p in imgs
                ]
                if gallery != self.last_gallery:
                    self.last_gallery = gallery
                    HUB.set_state(gallery=gallery)
                models = self.scan(self.model_dirs, MODEL_EXT)
                model = None
                if models:
                    m, i, p = models[0]
                    model = {
                        "url": f"/files/m{i}/{p.relative_to(self.model_dirs[i]).as_posix()}?v={int(m * 1000)}",
                        "name": p.name,
                        "mtime": m,
                    }
                if model != self.last_model:
                    self.last_model = model
                    HUB.set_state(model=model)
            except Exception as exc:
                print(f"[studio] files: {exc}")
            time.sleep(1.0)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def safe_name(name: str, default: str) -> str:
    keep = "".join(c for c in Path(name).name if c.isalnum() or c in "._- ")
    return keep.strip() or default


# ------------------------------------------------------------------ http


class Handler(BaseHTTPRequestHandler):
    server_version = "SkinStudio/1"
    roots: dict[str, Path] = {}
    port = 7777

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    # Only answer requests addressed to localhost. This blocks DNS-rebinding
    # pages from reading the activity feed through the browser.
    def host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in {f"localhost:{self.port}", f"127.0.0.1:{self.port}", f"[::1]:{self.port}"}

    def send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, obj, code: int = 200):
        self.send(code, json.dumps(obj).encode(), "application/json")

    def send_file(self, path: Path):
        try:
            data = path.read_bytes()
        except OSError:
            return self.send(404, b"not found")
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send(200, data, ctype)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.host_ok():
            return self.send(403, b"localhost only")
        url = urlparse(self.path)
        path = unquote(url.path)
        if path == "/events":
            return self.stream()
        if path == "/api/state":
            return self.send_json(HUB.snapshot())
        if path == "/viewport.jpg":
            vp = DATA / "viewport" / "latest.jpg"
            png = DATA / "viewport" / "latest.png"
            return self.send_file(vp if vp.exists() else png)
        if path.startswith("/files/"):
            key, _, rel = path[len("/files/") :].partition("/")
            root = self.roots.get(key)
            if not root:
                return self.send(404, b"not found")
            target = (root / rel).resolve()
            if root.resolve() not in target.parents:
                return self.send(403, b"no")
            return self.send_file(target)
        if path in ("", "/"):
            path = "/index.html"
        target = (WEB / path.lstrip("/")).resolve()
        if WEB.resolve() not in target.parents or not target.is_file():
            return self.send(404, b"not found")
        return self.send_file(target)

    def read_body(self) -> bytes | None:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_UPLOAD:
            self.send(413 if n > MAX_UPLOAD else 400, b"bad size")
            return None
        return self.rfile.read(n)

    def do_POST(self):
        if not self.host_ok():
            return self.send(403, b"localhost only")
        # Browsers send Origin on cross-site POSTs; only allow our own page or none (scripts).
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc.lower() not in {f"localhost:{self.port}", f"127.0.0.1:{self.port}"}:
            return self.send(403, b"wrong origin")
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        body = self.read_body()
        if body is None:
            return
        if url.path == "/api/viewport":
            ext = ".png" if body[:4] == b"\x89PNG" else ".jpg"
            atomic_write(DATA / "viewport" / ("latest" + ext), body)
            other = DATA / "viewport" / ("latest" + (".jpg" if ext == ".png" else ".png"))
            if other.exists():
                other.unlink()
            info = {
                "t": time.time(),
                "file": self.headers.get("X-Blend-File", ""),
                "frame": self.headers.get("X-Frame", ""),
                "mode": self.headers.get("X-Mode", ""),
            }
            HUB.set_state(viewport=info, blender=info)
            return self.send_json({"ok": True})
        if url.path == "/api/model":
            name = safe_name(qs.get("name", ["model.glb"])[0], "model.glb")
            if not name.lower().endswith(".glb"):
                name += ".glb"
            atomic_write(DATA / "models" / name, body)
            return self.send_json({"ok": True, "name": name})
        if url.path == "/api/gallery":
            name = safe_name(qs.get("name", ["render.jpg"])[0], "render.jpg")
            if Path(name).suffix.lower() not in IMAGE_EXT:
                return self.send(400, b"images only")
            atomic_write(DATA / "gallery" / name, body)
            return self.send_json({"ok": True, "name": name})
        if url.path == "/api/events":
            # For sessions whose log isn't on this PC (a cloud thread driving Blender remotely).
            try:
                data = json.loads(body)
            except ValueError:
                return self.send(400, b"json please")
            items = data if isinstance(data, list) else [data]
            events = []
            for it in items[:50]:
                kind = it.get("kind", "note")
                if kind not in ("note", "tool", "user"):
                    continue
                events.append({
                    "kind": kind,
                    "ts": it.get("ts") or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "tool": short(it.get("tool", "step"), 40),
                    "text": short(it.get("text", ""), 600),
                })
            HUB.add_events(events)
            return self.send_json({"ok": True, "added": len(events)})
        if url.path == "/api/status":
            try:
                data = json.loads(body or b"{}")
            except ValueError:
                return self.send(400, b"json please")
            changes = {}
            if "step" in data:
                changes["step"] = short(data["step"], 120)
            if "blender" in data:  # heartbeat from the add-on
                changes["blender"] = {"t": time.time(), **{k: str(v) for k, v in data["blender"].items()}}
            HUB.set_state(**changes)
            return self.send_json({"ok": True})
        return self.send(404, b"not found")

    def stream(self):
        q = HUB.subscribe()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            first = {"type": "hello", **HUB.snapshot(), "now": time.time()}
            self.wfile.write(f"data: {json.dumps(first)}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                    payload = f"data: {json.dumps(msg)}\n\n"
                except queue.Empty:
                    payload = ": ping\n\n"
                self.wfile.write(payload.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            HUB.unsubscribe(q)


def main() -> None:
    ap = argparse.ArgumentParser(description="Local live view of skin work.")
    ap.add_argument("--port", type=int, default=7777)
    ap.add_argument("--claude-projects", default=str(Path.home() / ".claude" / "projects"),
                    help="folder with Claude Code session logs")
    ap.add_argument("--match", default="LMOD",
                    help="only follow sessions whose project folder name contains this ('' = any)")
    ap.add_argument("--gallery", action="append", default=[],
                    help="extra image folder for the gallery (repeatable)")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    for sub in ("gallery", "models", "viewport"):
        (DATA / sub).mkdir(parents=True, exist_ok=True)

    gallery_dirs = [DATA / "gallery", REPO.parent / "LMOD_previews" / "previews", REPO / "renders"]
    gallery_dirs += sorted((REPO / "work").glob("*/previews"))  # e.g. work/pianomaw/previews
    gallery_dirs += [Path(g).expanduser().resolve() for g in args.gallery]
    model_dirs = [DATA / "models"]
    Handler.port = args.port
    Handler.roots = {f"g{i}": d for i, d in enumerate(gallery_dirs)}
    Handler.roots.update({f"m{i}": d for i, d in enumerate(model_dirs)})

    TranscriptTailer(Path(args.claude_projects).expanduser(), args.match).start()
    FileWatcher(gallery_dirs, model_dirs).start()

    class Server(ThreadingHTTPServer):
        # On Windows, address reuse lets a second copy bind the same port silently.
        allow_reuse_address = os.name != "nt"

    try:
        httpd = Server(("127.0.0.1", args.port), Handler)
    except OSError:
        print(f"Port {args.port} is busy. Skin Studio is probably already running: open http://localhost:{args.port}")
        return
    httpd.daemon_threads = True
    url = f"http://localhost:{args.port}"
    print(f"Skin Studio running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        import webbrowser

        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("bye")


if __name__ == "__main__":
    main()

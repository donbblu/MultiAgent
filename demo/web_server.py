from __future__ import annotations

import json
import mimetypes
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from coding_agent_cli import run_requirement


ROOT = Path(__file__).parent.resolve()
WEB_ROOT = ROOT / "web"
TASKS: dict[str, dict[str, object]] = {}
TASKS_LOCK = threading.Lock()


def public_event(entry: dict[str, object]) -> dict[str, object]:
    event = str(entry.get("event", "event"))
    payload = entry.get("payload", {})
    labels = {
        "task_started": ("system", "任务已进入工作流"),
        "role_assigned": ("handoff", "角色已接手任务"),
        "state_transition": ("status", "工作状态发生变化"),
        "implementation": ("agent", "实现 Agent 已提交变更"),
        "verification": ("agent", "验证 Agent 已返回结果"),
        "review": ("agent", "审查 Agent 已返回结果"),
        "parallel_stage_started": ("handoff", "并行质量阶段已启动"),
        "result_envelope": ("status", "结构化结果已提交"),
    }
    kind, title = labels.get(event, ("status", event))
    detail = payload
    if event == "task_started" and isinstance(payload, dict):
        detail = {"objective": payload.get("objective"), "acceptance_criteria": payload.get("acceptance_criteria")}
    return {
        "type": kind,
        "event": event,
        "title": title,
        "detail": detail,
        "timestamp": entry.get("timestamp"),
    }


def run_in_background(task_key: str, request: dict[str, object]) -> None:
    def on_event(entry: dict[str, object]) -> None:
        with TASKS_LOCK:
            task = TASKS[task_key]
            events = task["events"]
            assert isinstance(events, list)
            events.append(public_event(entry))
            payload = entry.get("payload")
            if entry.get("event") == "role_assigned" and isinstance(payload, dict):
                task["active_role"] = payload.get("name")
                task["active_roles"] = [payload.get("name")]
            if entry.get("event") == "parallel_stage_started" and isinstance(payload, dict):
                task["active_role"] = None
                task["active_roles"] = payload.get("roles", [])
            if entry.get("event") == "state_transition" and isinstance(payload, dict):
                task["status"] = payload.get("state", "running")

    try:
        run = run_requirement(
            str(request["requirement"]),
            str(request["name"]),
            provider=str(request["provider"]) if request.get("provider") else None,
            model=str(request["model"]) if request.get("model") else None,
            max_attempts=int(request.get("max_attempts", 2)),
            event_listener=on_event,
        )
        files = [
            str(path.relative_to(run.output))
            for path in sorted(run.output.rglob("*"))
            if path.is_file() and ".verification" not in path.parts
        ]
        with TASKS_LOCK:
            TASKS[task_key].update(
                {
                    "status": run.task.state.value,
                    "active_role": None,
                    "active_roles": [],
                    "result": {
                        "task_id": run.task.task_id,
                        "summary": run.task.history[-1] if run.task.history else "任务结束",
                        "attempts": run.task.attempt,
                        "provider": run.provider,
                        "model": run.model,
                        "output": str(run.output),
                        "files": files,
                    },
                }
            )
    except Exception as exc:
        with TASKS_LOCK:
            TASKS[task_key].update(
                {
                    "status": "failed",
                    "active_role": None,
                    "active_roles": [],
                    "error": str(exc),
                }
            )


class Handler(BaseHTTPRequestHandler):
    server_version = "MultiAgentUI/1.0"

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def send_json(self, status: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/tasks/"):
            task_key = path.removeprefix("/api/tasks/")
            with TASKS_LOCK:
                task = TASKS.get(task_key)
                snapshot = dict(task) if task else None
                if snapshot and isinstance(snapshot.get("events"), list):
                    snapshot["events"] = list(snapshot["events"])
            if snapshot is None:
                self.send_json(404, {"error": "任务不存在"})
            else:
                self.send_json(200, snapshot)
            return
        assets = {"/": "index.html", "/app.js": "app.js", "/styles.css": "styles.css"}
        filename = assets.get(path)
        if not filename:
            self.send_error(404)
            return
        content = (WEB_ROOT / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(filename)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/tasks":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 32768:
                raise ValueError("请求大小不合法")
            data = json.loads(self.rfile.read(length))
            requirement = str(data.get("requirement", "")).strip()
            name = str(data.get("name", "")).strip()
            if not 3 <= len(requirement) <= 4000:
                raise ValueError("需求长度必须在 3 到 4000 字符之间")
            if not name:
                name = f"ui-{datetime.now().strftime('%m%d-%H%M%S')}"
            request = {
                "requirement": requirement,
                "name": name,
                "provider": data.get("provider"),
                "model": data.get("model"),
                "max_attempts": min(max(int(data.get("max_attempts", 2)), 1), 3),
            }
            task_key = uuid.uuid4().hex[:12]
            with TASKS_LOCK:
                TASKS[task_key] = {
                    "id": task_key,
                    "status": "queued",
                    "active_role": None,
                    "active_roles": [],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "events": [],
                    "result": None,
                    "error": None,
                }
            threading.Thread(
                target=run_in_background, args=(task_key, request), daemon=True
            ).start()
            self.send_json(202, {"id": task_key})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        print(f"[ui] {self.address_string()} {format % args}")


def main() -> None:
    host, port = "127.0.0.1", 8765
    print(f"Multi-Agent UI: http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()

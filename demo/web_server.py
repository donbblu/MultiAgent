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
from coding_workflow.harness import LifecycleController
from coding_workflow.visionforge import VisionForgeWebError, VisionForgeWebRuntime


ROOT = Path(__file__).parent.resolve()
WEB_ROOT = ROOT / "web"
TASKS: dict[str, dict[str, object]] = {}
TASK_CONTROLS: dict[str, LifecycleController] = {}
TASKS_LOCK = threading.Lock()
VISIONFORGE_WEB = VisionForgeWebRuntime(
    ROOT / ".runtime" / "visionforge-web",
    ROOT / "visionforge_vue_template",
    env_file=ROOT / ".env",
)

WORKFLOW = {
    "nodes": [
        {"id": "planner", "label": "Planner", "summary": "理解需求与边界", "permissions": ["读取任务", "输出规划"]},
        {"id": "implementer", "label": "Implementer", "summary": "生成并写入项目文件", "permissions": ["读取项目", "写入允许路径"]},
        {"id": "tester", "label": "Tester", "summary": "运行白名单验收命令", "permissions": ["读取验收标准", "执行验证命令"]},
        {"id": "reviewer", "label": "Reviewer", "summary": "独立只读代码审查", "permissions": ["读取项目", "提交审查结果"]},
        {"id": "fixer", "label": "Fixer", "summary": "根据反馈修复代码", "permissions": ["读取项目与反馈", "写入允许路径"]},
    ],
    "edges": [
        ["planner", "implementer"],
        ["implementer", "tester"],
        ["implementer", "reviewer"],
        ["tester", "fixer"],
        ["reviewer", "fixer"],
        ["fixer", "tester"],
        ["fixer", "reviewer"],
    ],
}


def initial_nodes() -> dict[str, dict[str, object]]:
    return {
        str(node["id"]): {
            **node,
            "status": "pending",
            "attempt": 0,
            "started_at": None,
            "finished_at": None,
            "duration_ms": None,
            "last_summary": "等待 Harness 调度",
            "artifacts": [],
        }
        for node in WORKFLOW["nodes"]
    }


def finalize_node_states(
    nodes: dict[str, dict[str, object]], task_status: str
) -> None:
    """任务结束后为未触发的条件节点给出明确原因。"""
    fixer = nodes.get("fixer")
    if fixer and fixer.get("status") == "pending":
        fixer.update(
            {
                "status": "skipped",
                "last_summary": (
                    "实现和质量检查已通过，无需返工"
                    if task_status == "completed"
                    else "任务已结束或达到尝试上限，未进入返工"
                ),
            }
        )


def parse_visionforge_task_payload(
    data: object,
) -> tuple[str, str]:
    if not isinstance(data, dict):
        raise VisionForgeWebError("请求必须是 JSON 对象")
    unknown = set(data) - {"requirement", "asset_id"}
    if unknown:
        raise VisionForgeWebError(
            "任务请求包含未知字段: " + ", ".join(sorted(unknown))
        )
    return str(data.get("requirement", "")), str(data.get("asset_id", ""))


def role_from_event(entry: dict[str, object]) -> str | None:
    payload = entry.get("payload")
    if entry.get("event") == "role_assigned" and isinstance(payload, dict):
        return str(payload.get("name") or "") or None
    if entry.get("event") == "agent_message" and isinstance(payload, dict):
        sender = str(payload.get("sender") or "")
        recipient = str(payload.get("recipient") or "")
        return sender if sender not in {"user", "coordinator"} else (
            recipient if recipient not in {"user", "coordinator"} else None
        )
    return None


def public_event(entry: dict[str, object], sequence: int = 0) -> dict[str, object] | None:
    event = str(entry.get("event", "event"))
    payload = entry.get("payload", {})
    if event in {
        "task_started",
        "role_assigned",
        "implementation",
        "verification",
        "review",
        "result_envelope",
    }:
        return None
    labels = {
        "task_started": ("system", "任务已进入工作流"),
        "role_assigned": ("handoff", "角色已接手任务"),
        "state_transition": ("status", "工作状态发生变化"),
        "implementation": ("agent", "实现 Agent 已提交变更"),
        "verification": ("agent", "验证 Agent 已返回结果"),
        "review": ("agent", "审查 Agent 已返回结果"),
        "parallel_stage_started": ("handoff", "并行质量阶段已启动"),
        "result_envelope": ("status", "结构化结果已提交"),
        "agent_message": ("message", "Agent 消息"),
        "task_graph_created": ("status", "Planner 已生成任务图"),
        "task_graph_finished": ("status", "任务图执行结束"),
        "artifacts_integrated": ("status", "Artifact 已通过门禁并合并"),
        "artifacts_rejected": ("status", "Artifact 被安全门禁拒绝"),
    }
    kind, title = labels.get(event, ("status", event))
    detail = payload
    if event == "agent_message" and isinstance(payload, dict):
        title = (
            f"{payload.get('sender', '?')} → {payload.get('recipient', '?')}"
            f" · {payload.get('message_type', 'message')}"
        )
    return {
        "id": f"event-{sequence}",
        "type": kind,
        "event": event,
        "node_id": role_from_event(entry),
        "title": title,
        "detail": detail,
        "timestamp": entry.get("timestamp"),
    }


def run_in_background(
    task_key: str,
    request: dict[str, object],
    lifecycle: LifecycleController,
) -> None:
    def on_event(entry: dict[str, object]) -> None:
        with TASKS_LOCK:
            task = TASKS[task_key]
            events = task["events"]
            assert isinstance(events, list)
            visible_event = public_event(entry, len(events) + 1)
            if visible_event:
                events.append(visible_event)
            payload = entry.get("payload")
            if entry.get("event") == "role_assigned" and isinstance(payload, dict):
                role = str(payload.get("name") or "")
                previous_role = str(task.get("active_role") or "")
                previous = task["nodes"].get(previous_role)
                if previous and previous.get("status") == "running":
                    started = previous.get("started_at")
                    duration = None
                    if started and entry.get("timestamp"):
                        duration = int((datetime.fromisoformat(str(entry["timestamp"])) - datetime.fromisoformat(str(started))).total_seconds() * 1000)
                    previous.update({
                        "status": "success",
                        "finished_at": entry.get("timestamp"),
                        "duration_ms": duration,
                        "last_summary": "已完成职责并交回 Harness",
                    })
                task["active_role"] = role
                task["active_roles"] = [role]
                node = task["nodes"].get(role)
                if node:
                    node.update({
                        "status": "running",
                        "attempt": int(payload.get("attempt", 0)),
                        "started_at": entry.get("timestamp"),
                        "finished_at": None,
                        "duration_ms": None,
                        "last_summary": str(payload.get("objective") or "开始执行"),
                    })
            if entry.get("event") == "parallel_stage_started" and isinstance(payload, dict):
                task["active_role"] = None
                task["active_roles"] = payload.get("roles", [])
                for role in task["active_roles"]:
                    node = task["nodes"].get(role)
                    if node:
                        node.update({"status": "running", "started_at": entry.get("timestamp"), "finished_at": None})
            if entry.get("event") == "agent_message" and isinstance(payload, dict):
                sender = str(payload.get("sender") or "")
                message_type = str(payload.get("message_type") or "")
                node = task["nodes"].get(sender)
                if node and message_type == "result":
                    started = node.get("started_at")
                    duration = None
                    if started and entry.get("timestamp"):
                        duration = int((datetime.fromisoformat(str(entry["timestamp"])) - datetime.fromisoformat(str(started))).total_seconds() * 1000)
                    message_payload = payload.get("payload", {})
                    success = not (isinstance(message_payload, dict) and message_payload.get("passed") is False)
                    if isinstance(message_payload, dict) and message_payload.get("success") is False:
                        success = False
                    node.update({
                        "status": "success" if success else "failed",
                        "finished_at": entry.get("timestamp"),
                        "duration_ms": duration,
                        "last_summary": str(payload.get("summary") or "执行结束"),
                        "artifacts": message_payload.get("changed_files", []) if isinstance(message_payload, dict) else [],
                    })
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
            lifecycle=lifecycle,
            engine=str(request.get("engine", "dag")),
        )
        files = [
            str(path.relative_to(run.output))
            for path in sorted(run.output.rglob("*"))
            if path.is_file() and ".verification" not in path.parts
        ]
        with TASKS_LOCK:
            finalize_node_states(TASKS[task_key]["nodes"], run.task.state.value)
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
                        "engine": run.engine,
                        "output": str(run.output),
                        "files": files,
                    },
                }
            )
    except Exception as exc:
        with TASKS_LOCK:
            finalize_node_states(TASKS[task_key]["nodes"], "failed")
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
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' blob:; "
            "style-src 'self'; script-src 'self'; connect-src 'self'",
        )
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
        if path.startswith("/api/visionforge/assets/"):
            parts = path.strip("/").split("/")
            if len(parts) != 4 or parts[:3] != ["api", "visionforge", "assets"]:
                self.send_error(404)
                return
            try:
                content, mime_type = VISIONFORGE_WEB.read_asset(parts[3])
            except KeyError as exc:
                self.send_json(404, {"error": str(exc.args[0])})
                return
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(content)
            return
        if path.startswith("/api/visionforge/tasks/"):
            parts = path.strip("/").split("/")
            if len(parts) != 4 or parts[:3] != ["api", "visionforge", "tasks"]:
                self.send_error(404)
                return
            try:
                self.send_json(200, VISIONFORGE_WEB.task_snapshot(parts[3]))
            except KeyError as exc:
                self.send_json(404, {"error": str(exc.args[0])})
            return
        if path.startswith("/api/tasks/"):
            task_key = path.removeprefix("/api/tasks/")
            with TASKS_LOCK:
                task = TASKS.get(task_key)
                snapshot = dict(task) if task else None
                if snapshot and isinstance(snapshot.get("events"), list):
                    snapshot["events"] = list(snapshot["events"])
                controller = TASK_CONTROLS.get(task_key)
                if snapshot and controller:
                    lifecycle = controller.snapshot()
                    snapshot["lifecycle"] = {
                        "state": lifecycle.state.value,
                        "reason": lifecycle.reason,
                        "updated_at": lifecycle.updated_at,
                    }
                    snapshot["lifecycle_history"] = [
                        {
                            "previous": event.previous.value if event.previous else None,
                            "current": event.current.value,
                            "reason": event.reason,
                            "timestamp": event.timestamp,
                        }
                        for event in controller.history()
                    ]
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
        path = urlparse(self.path).path
        if path == "/api/visionforge/assets":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 10 * 1024 * 1024:
                    raise VisionForgeWebError("图片大小必须在 1 字节到 10 MiB 之间")
                content_type = self.headers.get("Content-Type", "")
                uploaded = VISIONFORGE_WEB.upload_image(
                    self.rfile.read(length), content_type
                )
                self.send_json(201, uploaded)
            except (ValueError, TypeError, VisionForgeWebError) as exc:
                self.send_json(400, {"error": str(exc)})
            return
        if path == "/api/visionforge/tasks":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 32768:
                    raise VisionForgeWebError("请求大小不合法")
                data = json.loads(self.rfile.read(length))
                requirement, asset_id = parse_visionforge_task_payload(data)
                task_id = VISIONFORGE_WEB.submit_task(
                    requirement,
                    asset_id,
                )
                self.send_json(202, {"id": task_id})
            except KeyError as exc:
                self.send_json(404, {"error": str(exc.args[0])})
            except (
                ValueError,
                TypeError,
                json.JSONDecodeError,
                VisionForgeWebError,
            ) as exc:
                self.send_json(400, {"error": str(exc)})
            return
        if path.startswith("/api/tasks/"):
            parts = path.strip("/").split("/")
            if len(parts) != 4 or parts[:2] != ["api", "tasks"]:
                self.send_error(404)
                return
            task_key, action = parts[2], parts[3]
            with TASKS_LOCK:
                task = TASKS.get(task_key)
                controller = TASK_CONTROLS.get(task_key)
                if not task or not controller:
                    self.send_json(404, {"error": "任务不存在"})
                    return
                if action == "pause":
                    accepted = controller.request_pause("用户从界面暂停任务")
                elif action == "resume":
                    accepted = controller.resume()
                elif action == "cancel":
                    accepted = controller.cancel("用户从界面取消任务")
                else:
                    self.send_error(404)
                    return
                snapshot = controller.snapshot()
                task["status"] = snapshot.state.value
            self.send_json(
                200 if accepted else 409,
                {"accepted": accepted, "state": snapshot.state.value, "reason": snapshot.reason},
            )
            return
        if path != "/api/tasks":
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
                "engine": str(data.get("engine", "dag")),
            }
            if request["engine"] not in {"dag", "legacy"}:
                raise ValueError("engine 必须是 dag 或 legacy")
            task_key = uuid.uuid4().hex[:12]
            lifecycle = LifecycleController()
            lifecycle.mark_queued()
            with TASKS_LOCK:
                TASKS[task_key] = {
                    "id": task_key,
                    "status": "queued",
                    "active_role": None,
                    "active_roles": [],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "events": [],
                    "workflow": WORKFLOW,
                    "nodes": initial_nodes(),
                    "result": None,
                    "error": None,
                }
                TASK_CONTROLS[task_key] = lifecycle
            threading.Thread(
                target=run_in_background,
                args=(task_key, request, lifecycle),
                daemon=True,
            ).start()
            self.send_json(202, {"id": task_key})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        print(f"[ui] {self.address_string()} {format % args}")


def main() -> None:
    host, port = "127.0.0.1", 8765
    print(f"VisionForge UI: http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()

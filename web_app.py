from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
OUTPUT_DIR = ROOT / "output"
DEFAULT_PORT = int(os.environ.get("A_SHARE_ANALYST_WEB_PORT", "3000"))

STEP_ORDER = ["parse", "prefetch", "parse_data", "react", "sections", "output"]
STEP_LABELS = {
    "parse": "输入解析",
    "prefetch": "核心数据预取",
    "parse_data": "LLM 数据解析",
    "react": "ReAct 补充采集",
    "sections": "章节生成验证",
    "output": "Markdown 输出",
}

TERMINAL_STATUSES = {"completed", "failed"}
PROXY_ENV_KEYS = {
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "https_proxy",
    "http_proxy",
    "all_proxy",
}


def _default_steps() -> dict[str, dict]:
    return {
        step_id: {
            "id": step_id,
            "label": STEP_LABELS[step_id],
            "status": "idle",
            "detail": "等待",
            "progress": 0,
        }
        for step_id in STEP_ORDER
    }


@dataclass
class Job:
    id: str
    query: str
    no_proxy: bool = True
    disable_langsmith: bool = True
    status: str = "queued"
    returncode: int | None = None
    output_path: str = ""
    error: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    steps: dict[str, dict] = field(default_factory=_default_steps)
    metrics: dict[str, int] = field(
        default_factory=lambda: {
            "initial_data": 0,
            "final_data": 0,
            "tool_calls": 0,
            "sections_done": 0,
        }
    )
    logs: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    condition: threading.Condition = field(default_factory=threading.Condition)

    def emit(self, event_type: str, payload: dict) -> None:
        with self.condition:
            event = {
                "seq": len(self.events),
                "type": event_type,
                "payload": payload,
                "ts": time.time(),
            }
            self.events.append(event)
            self.condition.notify_all()

    def snapshot(self) -> dict:
        active_step = next(
            (step["id"] for step in self.steps.values() if step["status"] == "active"),
            "",
        )
        return {
            "id": self.id,
            "query": self.query,
            "status": self.status,
            "returncode": self.returncode,
            "output_path": self.output_path,
            "error": self.error,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration": round((self.ended_at or time.time()) - self.started_at, 1),
            "active_step": active_step,
            "steps": [self.steps[step_id] for step_id in STEP_ORDER],
            "metrics": dict(self.metrics),
        }


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def _activate_step(job: Job, step_id: str, detail: str = "", progress: int | None = None) -> None:
    target_index = STEP_ORDER.index(step_id)
    for previous_id in STEP_ORDER[:target_index]:
        if job.steps[previous_id]["status"] in {"idle", "active"}:
            job.steps[previous_id]["status"] = "done"
            job.steps[previous_id]["progress"] = 100
    step = job.steps[step_id]
    step["status"] = "active"
    if detail:
        step["detail"] = detail
    if progress is not None:
        step["progress"] = max(0, min(100, progress))


def _complete_step(job: Job, step_id: str, detail: str = "") -> None:
    step = job.steps[step_id]
    step["status"] = "done"
    step["progress"] = 100
    if detail:
        step["detail"] = detail


def _fail_active_step(job: Job, detail: str) -> None:
    active_step = next(
        (step for step in job.steps.values() if step["status"] == "active"),
        job.steps["parse"],
    )
    active_step["status"] = "error"
    active_step["detail"] = detail


def _output_path_from_line(line: str) -> str:
    match = re.search(r"(?:研报已保存至|研报生成完成)：\s*(.+\.md)", line)
    return match.group(1).strip() if match else ""


def _update_job_from_line(job: Job, line: str) -> None:
    if "[main] 解析完成" in line:
        _complete_step(job, "parse", "公司、代码和期间已确认")
        return

    if "阶段一：预取" in line or "阶段一：获取" in line:
        _activate_step(job, "prefetch", "正在读取 AKShare 核心接口", 20)
        return

    if "阶段一：" in line and "失败" in line:
        _activate_step(job, "prefetch", "部分数据源超时，已跳过", 70)
        return

    if "阶段一：LLM 解析原始数据" in line:
        _complete_step(job, "prefetch", "核心数据读取完成")
        _activate_step(job, "parse_data", "正在抽取结构化指标", 10)
        return

    if "阶段一：解析 " in line:
        source = line.rsplit("解析 ", 1)[-1].strip(".")
        _activate_step(job, "parse_data", source, 45)
        return

    match = re.search(r"阶段一完成，初始化\s+(\d+)\s+条数据项", line)
    if match:
        job.metrics["initial_data"] = int(match.group(1))
        _complete_step(job, "parse_data", f"初始化 {match.group(1)} 条数据")
        return

    match = re.search(r"阶段二：调用\s+(\w+)（轮次\s+(\d+)/(\d+)）", line)
    if match:
        tool_name, current, total = match.group(1), int(match.group(2)), int(match.group(3))
        job.metrics["tool_calls"] = max(job.metrics["tool_calls"], current)
        progress = round(current / total * 100)
        _activate_step(job, "react", f"{tool_name} 第 {current}/{total} 次", progress)
        return

    match = re.search(r"阶段二完成，共\s+(\d+)\s+条数据项", line)
    if match:
        job.metrics["final_data"] = int(match.group(1))
        _complete_step(job, "react", f"累计 {match.group(1)} 条数据")
        return

    match = re.search(r"生成章节\s+(\d+)/(\d+)：(.+)", line)
    if match:
        current, total, title = int(match.group(1)), int(match.group(2)), match.group(3)
        job.metrics["sections_done"] = max(job.metrics["sections_done"], current - 1)
        progress = round((current - 1) / total * 100)
        _activate_step(job, "sections", title, progress)
        return

    if "[report_generation]" in line and "验证通过" in line:
        job.metrics["sections_done"] = min(5, job.metrics["sections_done"] + 1)
        progress = round(job.metrics["sections_done"] / 5 * 100)
        _activate_step(job, "sections", "章节验证通过", progress)
        if job.metrics["sections_done"] >= 5:
            _complete_step(job, "sections", "5 个章节均已验证")
        return

    if "[report_generation]" in line and ("验证失败" in line or "重试" in line):
        _activate_step(job, "sections", "校验未通过，正在重试", None)
        return

    output_path = _output_path_from_line(line)
    if output_path:
        job.output_path = output_path
        _complete_step(job, "sections", "章节已完成")
        _complete_step(job, "output", "报告已写入 output/")


def _compose_query(payload: dict) -> str:
    query = str(payload.get("query", "")).strip()
    if query:
        return query

    company = str(payload.get("company", "")).strip()
    year = str(payload.get("year", "")).strip()
    quarter = str(payload.get("quarter", "")).strip().upper()
    return f"{company} {year} {quarter}".strip()


def _create_job(payload: dict) -> Job:
    query = _compose_query(payload)
    if not query:
        raise ValueError("请输入公司名或股票代码、年份和季度")

    job = Job(
        id=uuid.uuid4().hex[:12],
        query=query,
        no_proxy=bool(payload.get("no_proxy", True)),
        disable_langsmith=bool(payload.get("disable_langsmith", True)),
    )
    with JOBS_LOCK:
        JOBS[job.id] = job

    job.emit("state", job.snapshot())
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return job


def _run_job(job: Job) -> None:
    job.status = "running"
    _activate_step(job, "parse", "启动生成流程", 20)
    job.emit("state", job.snapshot())

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    if job.disable_langsmith:
        env["LANGCHAIN_TRACING_V2"] = "false"
    if job.no_proxy:
        for key in PROXY_ENV_KEYS:
            env.pop(key, None)

    cmd = [sys.executable, "-u", "main.py", job.query]
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.ended_at = time.time()
        _fail_active_step(job, job.error)
        job.emit("state", job.snapshot())
        return

    assert proc.stdout is not None
    for raw_line in proc.stdout:
        line = raw_line.rstrip()
        if not line:
            continue
        job.logs.append(line)
        _update_job_from_line(job, line)
        job.emit("log", {"line": line})
        job.emit("state", job.snapshot())

    job.returncode = proc.wait()
    job.ended_at = time.time()
    if job.returncode == 0:
        job.status = "completed"
        if not job.output_path:
            _complete_step(job, "output", "流程完成")
    else:
        job.status = "failed"
        job.error = f"流程退出码 {job.returncode}"
        _fail_active_step(job, job.error)
    job.emit("state", job.snapshot())


def _safe_output_file(filename: str) -> Path:
    decoded = unquote(filename)
    path = (OUTPUT_DIR / Path(decoded).name).resolve()
    if not str(path).startswith(str(OUTPUT_DIR.resolve())):
        raise ValueError("invalid report path")
    if not path.exists() or path.suffix.lower() != ".md":
        raise FileNotFoundError(decoded)
    return path


def _list_reports() -> list[dict]:
    if not OUTPUT_DIR.exists():
        return []
    reports = sorted(OUTPUT_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "name": path.name,
            "path": str(path.relative_to(ROOT)),
            "size": path.stat().st_size,
            "mtime": path.stat().st_mtime,
        }
        for path in reports[:20]
    ]


class AppHandler(BaseHTTPRequestHandler):
    server_version = "AShareAnalystWeb/0.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def _send_json(self, payload: dict | list, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path: str) -> None:
        if path == "/":
            file_path = WEB_DIR / "index.html"
        else:
            relative = Path(unquote(path.removeprefix("/static/")))
            file_path = (WEB_DIR / relative).resolve()
            if not str(file_path).startswith(str(WEB_DIR.resolve())):
                self.send_error(404)
                return

        if not file_path.exists() or not file_path.is_file():
            self.send_error(404)
            return

        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _get_job(self, job_id: str) -> Job | None:
        with JOBS_LOCK:
            return JOBS.get(job_id)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path.startswith("/static/"):
            self._serve_static(path)
            return

        if path == "/api/reports":
            self._send_json({"reports": _list_reports()})
            return

        if path.startswith("/api/reports/"):
            try:
                report = _safe_output_file(path.removeprefix("/api/reports/"))
                self._send_text(report.read_text(encoding="utf-8"), "text/markdown; charset=utf-8")
            except (FileNotFoundError, ValueError):
                self.send_error(404)
            return

        match = re.fullmatch(r"/api/jobs/([^/]+)", path)
        if match:
            job = self._get_job(match.group(1))
            if not job:
                self.send_error(404)
                return
            self._send_json(job.snapshot())
            return

        match = re.fullmatch(r"/api/jobs/([^/]+)/report", path)
        if match:
            job = self._get_job(match.group(1))
            if not job or not job.output_path:
                self.send_error(404)
                return
            try:
                report_path = _safe_output_file(Path(job.output_path).name)
                self._send_json(
                    {
                        "path": str(report_path.relative_to(ROOT)),
                        "markdown": report_path.read_text(encoding="utf-8"),
                    }
                )
            except (FileNotFoundError, ValueError):
                self.send_error(404)
            return

        match = re.fullmatch(r"/api/jobs/([^/]+)/events", path)
        if match:
            job = self._get_job(match.group(1))
            if not job:
                self.send_error(404)
                return
            self._stream_events(job)
            return

        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/jobs":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            job = _create_job(payload)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        self._send_json(job.snapshot(), status=201)

    def _stream_events(self, job: Job) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        cursor = 0
        try:
            while True:
                with job.condition:
                    if cursor >= len(job.events) and job.status not in TERMINAL_STATUSES:
                        job.condition.wait(timeout=15)
                    events = job.events[cursor:]
                    cursor = len(job.events)

                if not events:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                for event in events:
                    payload = json.dumps(event["payload"], ensure_ascii=False)
                    chunk = (
                        f"id: {event['seq']}\n"
                        f"event: {event['type']}\n"
                        f"data: {payload}\n\n"
                    ).encode("utf-8")
                    self.wfile.write(chunk)
                    self.wfile.flush()

                if job.status in TERMINAL_STATUSES and cursor >= len(job.events):
                    break
        except (BrokenPipeError, ConnectionResetError):
            return


def run_server(port: int = DEFAULT_PORT) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), AppHandler)
    print(f"A-share Analyst web console: http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    port_arg = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    run_server(port_arg)

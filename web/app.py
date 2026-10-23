from datetime import timedelta
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from urllib.parse import urlsplit
import uuid

from flask import Flask, abort, jsonify, request, send_file, session

from run import write_json
from workflow import STAGES, now


PROJECT = Path(__file__).resolve().parents[1]


def create_app(results=None, token=None):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    root = Path(results or PROJECT / "results").resolve()
    root.mkdir(parents=True, exist_ok=True)
    if token is None:
        token = os.environ.get("RESEARCH_DEBATE_TOKEN")
        if not token:
            token_file = PROJECT / ".web-token"
            try:
                with open(token_file, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
                    stream.write(secrets.token_urlsafe(24))
            except FileExistsError:
                pass
            token = token_file.read_text().strip()
    if len(token) < 16:
        raise ValueError("访问口令至少16个字符")
    app.secret_key = hashlib.sha256(token.encode()).digest()
    app.config.update(MAX_CONTENT_LENGTH=32_000, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Strict", PERMANENT_SESSION_LIFETIME=timedelta(days=1),
                      SESSION_COOKIE_SECURE=os.environ.get("RESEARCH_DEBATE_HTTPS") == "1")
    children = []

    def lock():
        stream = (root / ".run.lock").open("a")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.close()
            abort(409, description="已有研究正在运行，请完成或停止后再开始。")
        return stream

    def recover():
        for child in children[:]:
            if child.poll() is not None:
                children.remove(child)
        stream = (root / ".run.lock").open("a")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.close()
            return
        with stream:
            for path in root.glob("*/job.json"):
                state = json.loads(path.read_text())
                if state["status"] in ("queued", "running"):
                    state.update(status="interrupted", error="后台进程已退出，已有材料仍可查看。", updated_at=now())
                    write_json(path, state)

    def job_dir(job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            abort(404)
        directory = root / job_id
        if directory.is_symlink() or not (directory / "job.json").is_file():
            abort(404)
        return directory

    def launch(directory, slot):
        with slot, (directory / "worker.log").open("a") as log:
            try:
                child = subprocess.Popen([sys.executable, str(PROJECT / "workflow.py"), str(directory)],
                                         cwd=PROJECT, stdout=log, stderr=log,
                                         start_new_session=True, pass_fds=(slot.fileno(),))
                children.append(child)
            except OSError:
                state = json.loads((directory / "job.json").read_text())
                state.update(status="failed", error="无法启动后台进程。", updated_at=now())
                write_json(directory / "job.json", state)
                raise

    @app.before_request
    def protect():
        if request.method in ("POST", "DELETE", "PUT", "PATCH"):
            if not request.is_json:
                abort(415, description="需要 JSON 请求。")
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                abort(403, description="不接受跨站请求。")
        if request.path.startswith("/api/") and request.path != "/api/login" and not session.get("authenticated"):
            abort(401, description="请先输入访问口令。")

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(413)
    @app.errorhandler(415)
    def client_error(error):
        return jsonify(error=error.description), error.code

    @app.errorhandler(500)
    def server_error(error):
        return jsonify(error="服务器处理失败，已有材料仍保存在任务目录；请检查服务器日志。"), 500

    def body():
        value = request.get_json()
        if not isinstance(value, dict):
            abort(400, description="请求内容应为对象。")
        return value

    def text_field(data, key, maximum, required=False):
        value = data.get(key, "")
        if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
            abort(400, description=f"{key} 不能为空或超过 {maximum} 字符。")
        return value.strip()

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.post("/api/login")
    def login():
        supplied = text_field(body(), "token", 256, True)
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            abort(401, description="访问口令不正确。")
        session.clear()
        session["authenticated"] = True
        session.permanent = True
        return jsonify(ok=True)

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/jobs")
    def jobs():
        recover()
        states = [json.loads(path.read_text()) for path in root.glob("*/job.json")]
        return jsonify(jobs=sorted(states, key=lambda x: x["created_at"], reverse=True), stages=STAGES)

    @app.post("/api/jobs")
    def create():
        data = body()
        topic = text_field(data, "topic", 3000, True)
        context = text_field(data, "context", 12000)
        slot = lock()
        try:
            job_id = uuid.uuid4().hex
            directory = root / job_id
            directory.mkdir()
            state = {"id": job_id, "topic": topic, "context": context, "status": "queued",
                     "stage": "plan", "created_at": now(), "updated_at": now(), "questions": [], "error": None}
            write_json(directory / "job.json", state)
            launch(directory, slot)
        finally:
            slot.close()
        return jsonify(state), 202

    @app.get("/api/jobs/<job_id>")
    def detail(job_id):
        recover()
        directory = job_dir(job_id)
        state = json.loads((directory / "job.json").read_text())
        path = directory / "events.jsonl"
        events = []
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # An append may be in flight; next poll reads the complete line.
        files = []
        for path in sorted(directory.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix in (".md", ".json") and path.resolve().is_relative_to(directory):
                files.append({"name": path.relative_to(directory).as_posix(), "size": path.stat().st_size})
        return jsonify(job=state, events=events, files=files)

    @app.post("/api/jobs/<job_id>/answer")
    def answer(job_id):
        clarification = text_field(body(), "answer", 12000, True)
        directory = job_dir(job_id)
        slot = lock()
        try:
            state = json.loads((directory / "job.json").read_text())
            if state["status"] != "needs_input":
                abort(409, description="此任务当前不需要补充回答。")
            state.update(clarification=clarification, questions=[], status="queued", updated_at=now())
            write_json(directory / "job.json", state)
            launch(directory, slot)
        finally:
            slot.close()
        return jsonify(ok=True), 202

    @app.post("/api/jobs/<job_id>/cancel")
    def cancel(job_id):
        body()
        directory = job_dir(job_id)
        state = json.loads((directory / "job.json").read_text())
        if state["status"] not in ("running", "queued", "needs_input"):
            abort(409, description="任务已经结束。")
        (directory / "cancel.request").touch()
        if state["status"] == "needs_input":
            state.update(status="cancelled", updated_at=now())
            write_json(directory / "job.json", state)
        return jsonify(ok=True)

    @app.get("/api/jobs/<job_id>/files/<path:name>")
    def file(job_id, name):
        directory = job_dir(job_id)
        path = directory / name
        if (not path.resolve().is_relative_to(directory) or path.is_symlink()
                or any(p.is_symlink() for p in path.parents if p != directory)
                or path.suffix not in (".md", ".json") or not path.is_file()):
            abort(404)
        if path.stat().st_size > 2_000_000:
            abort(413, description="文件较大，请在服务器任务目录查看。")
        return send_file(path, mimetype="text/plain; charset=utf-8", as_attachment=request.args.get("download") == "1",
                         download_name=path.name)

    return app

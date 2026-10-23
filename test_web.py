import fcntl
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from run import write_json
from web.app import create_app


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = create_app(self.root, "test-token-long-enough")
        self.app.testing = True
        self.client = self.app.test_client()
        self.client.post("/api/login", json={"token": "test-token-long-enough"})

    def job(self, status="completed"):
        directory = self.root / ("a" * 32)
        directory.mkdir()
        write_json(directory / "job.json", {"id": directory.name, "topic": "研究", "context": "",
                   "status": status, "stage": "plan", "created_at": "2026-01-01", "updated_at": "2026-01-01"})
        return directory, "/api/jobs/" + directory.name

    def test_requires_login_and_rejects_cross_origin(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/api/jobs").status_code, 401)
        self.assertEqual(client.post("/api/login", json={"token": "wrong"}).status_code, 401)
        self.assertEqual(self.client.post("/api/jobs", json={"topic": "test"},
                         headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/jobs", data="topic=x").status_code, 415)

    def test_first_start_creates_private_persistent_token(self):
        with patch("web.app.PROJECT", self.root), patch.dict(os.environ, {}, clear=True):
            create_app(self.root / "results")
            token = (self.root / ".web-token").read_text()
            create_app(self.root / "results")
        self.assertGreaterEqual(len(token), 16)
        self.assertEqual((self.root / ".web-token").stat().st_mode & 0o777, 0o600)
        self.assertEqual(token, (self.root / ".web-token").read_text())

    def test_artifacts_are_plain_text_and_cannot_escape(self):
        directory, url = self.job()
        (directory / "report.md").write_text("<script>alert(1)</script>")
        (self.root / "secret.md").write_text("not accessible")
        (directory / "link.md").symlink_to(self.root / "secret.md")
        (directory / "worker.log").write_text("private raw log")
        result = self.client.get(url + "/files/report.md")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.content_type.startswith("text/plain"))
        self.assertIn("nosniff", result.headers["X-Content-Type-Options"])
        result.close()
        for path in ("../secret.md", "link.md", "worker.log", "../../.web-token"):
            self.assertEqual(self.client.get(url + "/files/" + path).status_code, 404)
        with self.client.get(url + "/files/report.md?download=1") as download:
            self.assertIn("attachment", download.headers["Content-Disposition"])

    def test_cancel_waiting_job_and_reject_resume(self):
        directory, url = self.job("needs_input")
        self.assertEqual(self.client.post(url + "/cancel", json={}).status_code, 200)
        self.assertTrue((directory / "cancel.request").exists())
        self.assertEqual(json.loads((directory / "job.json").read_text())["status"], "cancelled")
        self.assertEqual(self.client.post(url + "/answer", json={"answer": "x"}).status_code, 409)

    def test_single_job_lock_and_stale_recovery(self):
        directory, url = self.job("running")
        with (self.root / ".run.lock").open("a") as slot:
            fcntl.flock(slot.fileno(), fcntl.LOCK_EX)
            self.assertEqual(self.client.post("/api/jobs", json={"topic": "test"}).status_code, 409)
            self.assertEqual(self.client.get(url).json["job"]["status"], "running")
        self.assertEqual(self.client.get(url).json["job"]["status"], "interrupted")

    def test_valid_creation_passes_lock_to_worker_and_invalid_inputs_do_not(self):
        with patch("web.app.subprocess.Popen") as spawn:
            for data in ({"topic": ""}, {"topic": ["x"]}, {"topic": "x" * 3001}, []):
                self.assertEqual(self.client.post("/api/jobs", json=data).status_code, 400)
            spawn.assert_not_called()
            response = self.client.post("/api/jobs", json={"topic": "可靠性评估", "context": "限定摘要"})
            self.assertEqual(response.status_code, 202)
            args, kwargs = spawn.call_args
            self.assertIn("workflow.py", args[0][1])
            self.assertTrue(kwargs["pass_fds"])
            self.assertTrue((self.root / response.json["id"] / "job.json").exists())

    def test_refresh_preserves_materials_and_handles_partial_event(self):
        directory, url = self.job()
        (directory / "answer.md").write_text("研究结论")
        (directory / "events.jsonl").write_text('{"time":"now","message":"完成"}\n{"time":')
        first = self.client.get(url).json
        second = self.client.get(url).json
        self.assertEqual(first, second)
        self.assertEqual(len(first["events"]), 1)
        self.assertIn("answer.md", [x["name"] for x in first["files"]])


if __name__ == "__main__":
    unittest.main()

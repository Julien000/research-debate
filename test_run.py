import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import run


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prompt = self.root / "input.md"
        self.prompt.write_text("中文课题 `touch unwanted` $(echo unsafe)", encoding="utf-8")
        self.out = self.root / "contribution"

    def invoke(self, code, agent="claude", timeout=5):
        with patch.object(run, "command_for", return_value=[sys.executable, "-c", code]):
            return run.run(agent, self.prompt, self.out, timeout)

    def test_stdin_unicode_and_model_record(self):
        status = self.invoke("import sys,json; print(json.dumps(dict(subtype='success', result=sys.stdin.read(), modelUsage={'test-model':{}})))")
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["models"], ["test-model"])
        self.assertIn(self.prompt.read_text(), (self.out / "answer.md").read_text())
        self.assertEqual(json.loads((self.out / "status.json").read_text())["status"], "completed")

    def test_codex_completed(self):
        events = [{"type": "item.completed", "item": {"type": "agent_message", "text": "研究结论"}}, {"type": "turn.completed"}]
        status = self.invoke("print(" + repr("\n".join(json.dumps(e) for e in events)) + ")", "codex")
        self.assertEqual(status["status"], "completed")
        self.assertEqual((self.out / "answer.md").read_text(), "研究结论\n")

    def test_nonzero_preserves_output(self):
        status = self.invoke("import sys; print('partial'); print('error', file=sys.stderr); sys.exit(2)")
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["returncode"], 2)
        self.assertIn("partial", (self.out / "stdout.txt").read_text())
        self.assertFalse((self.out / "answer.md").exists())

    def test_timeout_preserves_partial(self):
        status = self.invoke("import time; print('partial', flush=True); time.sleep(10)", timeout=0.2)
        self.assertEqual(status["status"], "timeout")
        self.assertIn("partial", (self.out / "stdout.txt").read_text())

    def test_invalid_answers(self):
        for agent, raw in [("claude", '{"subtype":"success","result":""}'),
                           ("claude", '{"subtype":"success","is_error":true,"result":"bad"}'),
                           ("claude", "not json"), ("codex", '{"type":"thread.started"}'),
                           ("codex", '{"type":"turn.failed"}'),
                           ("claude", '[]'), ("codex", 'null'),
                           ("codex", '{"type":"item.completed","item":null}\n{"type":"turn.completed"}')]:
            with self.subTest(agent=agent, raw=raw), self.assertRaises(ValueError):
                run.extract_answer(agent, raw)

    def test_existing_directory_not_overwritten(self):
        self.out.mkdir()
        with self.assertRaises(FileExistsError):
            run.run("codex", self.prompt, self.out)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_missing_cli(self):
        with patch.object(run, "command_for", return_value=[str(self.root / "missing")]):
            status = run.run("codex", self.prompt, self.out)
        self.assertEqual(status["status"], "failed")
        self.assertIn("FileNotFoundError", status["error"])

    def test_empty_prompt_rejected_before_output(self):
        self.prompt.write_text(" ")
        with self.assertRaises(ValueError):
            run.run("codex", self.prompt, self.out)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()

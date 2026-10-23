import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import literature
from run import write_json
from workflow import Workflow


def paper(ident="L1", title="A study"):
    return {"id": ident, "title": title, "abstract": "Evidence with limits.", "authors": ["A"],
            "year": 2025, "source": "Crossref", "publication_type": "journal-article",
            "url": "https://doi.org/10.example/test", "read_scope": "摘要"}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_json(self.root / "job.json", {"id": "a" * 32, "topic": "研究问题", "context": "限制",
                   "status": "queued", "stage": "plan"})
        self.calls = {}

    def respond(self, name, agent, material):
        self.calls[name] = material
        if name.startswith("plan"):
            return json.dumps({"questions": [], "queries": ["agent debate"], "scope": "范围"})
        if name == "selection": return '{"selected":["L1"],"reason":"相关"}'
        if name == "gaps": return '{"queries":[],"reason":"保留缺口"}'
        if name == "summary": return "# 报告\n摘要支持[L1]，效果尚未验证。"
        return name + " 公开回答 [L1]"

    def test_complete_workflow_keeps_initial_independent_and_history_complete(self):
        with patch.object(Workflow, "call", side_effect=self.respond), patch("literature.search", return_value=([paper()], [])):
            Workflow(self.root).execute()
        self.assertEqual(json.loads((self.root / "job.json").read_text())["status"], "completed")
        self.assertEqual(self.calls["initial-codex"], self.calls["initial-claude"])
        self.assertNotIn("公开回答", self.calls["initial-codex"])
        self.assertIn("initial-claude 公开回答", self.calls["round1-codex"])
        self.assertIn("round1-codex 公开回答", self.calls["round2-claude"])
        self.assertTrue((self.root / "report.md").exists())
        self.assertIn("round2-claude", (self.root / "debate.md").read_text())

    def test_ambiguous_topic_pauses_before_search(self):
        with patch.object(Workflow, "call", return_value='{"questions":["研究什么对象？"],"queries":["debate"],"scope":"未定"}'), patch("literature.search") as search:
            Workflow(self.root).execute()
        search.assert_not_called()
        self.assertEqual(json.loads((self.root / "job.json").read_text())["status"], "needs_input")

    def test_evidence_gate_rejects_metadata_only(self):
        p = paper(); p["abstract"] = ""
        with patch.object(Workflow, "call", side_effect=self.respond), patch("literature.search", return_value=([p], [])):
            Workflow(self.root).execute()
        self.assertEqual(json.loads((self.root / "job.json").read_text())["status"], "failed")
        self.assertNotIn("initial-codex", self.calls)

    def test_unknown_final_citation_fails_without_publishing_report(self):
        def answer(name, agent, material):
            return "错误引用[L999]" if name == "summary" else self.respond(name, agent, material)
        with patch.object(Workflow, "call", side_effect=answer), patch("literature.search", return_value=([paper()], [])):
            Workflow(self.root).execute()
        self.assertEqual(json.loads((self.root / "job.json").read_text())["status"], "failed")
        self.assertFalse((self.root / "report.md").exists())

    def test_cancelled_workflow_does_not_call_models(self):
        (self.root / "cancel.request").touch()
        with patch.object(Workflow, "call") as call:
            Workflow(self.root).execute()
        call.assert_not_called()
        self.assertEqual(json.loads((self.root / "job.json").read_text())["status"], "cancelled")

    def test_supplement_ids_do_not_collide_with_selected_ids(self):
        with patch("literature.crossref", return_value=[paper(title="New study")]), patch("literature.arxiv", side_effect=TimeoutError("timeout")):
            papers, errors = literature.search(["query"], self.root, existing=[paper("L20")])
        self.assertEqual([p["id"] for p in papers], ["L20", "L21"])
        self.assertEqual(errors[0]["provider"], "arxiv")
        self.assertIn("检索缺口", literature.render(papers, errors))

    def test_abstract_markup_is_plain_text(self):
        self.assertEqual(literature.plain("<jats:p>A &amp; B</jats:p>"), "A & B")


if __name__ == "__main__":
    unittest.main()

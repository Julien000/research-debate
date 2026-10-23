"""A bounded research workflow, runnable independently of the chat window."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import threading

import literature
from run import run, write_json


STAGES = [("plan", "明确问题"), ("research", "检索文献"), ("initial", "独立分析"),
          ("round1", "第一轮交锋"), ("supplement", "补查证据"),
          ("round2", "第二轮交锋"), ("summary", "汇总结论")]
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def now():
    return datetime.now(timezone.utc).isoformat()


def parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError("主持人应返回 JSON 对象，原始回答已保留")
    return result


def string_list(value, limit, label):
    if not isinstance(value, list) or len(value) > limit or any(
            not isinstance(x, str) or not x.strip() or len(x) > 500 for x in value):
        raise ValueError(f"主持人返回的 {label} 格式无效")
    return value


class Workflow:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.state = json.loads((self.directory / "job.json").read_text())
        self.log_lock = threading.Lock()

    def cancelled(self):
        return (self.directory / "cancel.request").exists()

    def check_cancel(self):
        if self.cancelled():
            raise ValueError("任务已取消")

    def event(self, message):
        with self.log_lock, (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": now(), "message": message}, ensure_ascii=False) + "\n")

    def update(self, **values):
        self.state.update(values, updated_at=now())
        write_json(self.directory / "job.json", self.state)

    def stage(self, key):
        self.check_cancel()
        self.update(status="running", stage=key)
        self.event(dict(STAGES)[key])

    def call(self, name, agent, material):
        self.check_cancel()
        path = self.directory / f"{name}.md"
        path.write_text(material, encoding="utf-8")
        self.event(f"{name} · {agent} 开始")
        status = run(agent, path, self.directory / name, cancelled=self.cancelled)
        self.event(f"{name} · {status['status']}")
        if status["status"] != "completed":
            raise ValueError(f"{name} 未完成：{status.get('error', status['status'])}")
        self.check_cancel()
        return (self.directory / name / "answer.md").read_text(encoding="utf-8")

    def pair(self, stage, material):
        # Both participants get exactly the same evidence and prior-stage answers.
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {agent: pool.submit(self.call, f"{stage}-{agent}", agent, material)
                       for agent in ("codex", "claude")}
            return {agent: future.result() for agent, future in futures.items()}

    def execute(self):
        try:
            self._execute()
        except Exception as exc:
            self.update(status="cancelled" if self.cancelled() else "failed", error=str(exc))
            self.event(self.state["status"] + "：" + str(exc))

    def _execute(self):
        self.stage("plan")
        brief = "# 研究问题\n\n" + self.state["topic"] + "\n\n# 背景与限制\n\n" + self.state["context"]
        clarification = self.state.get("clarification", "")
        if clarification:
            brief += "\n\n# 用户补充\n\n" + clarification
        (self.directory / "brief.md").write_text(brief, encoding="utf-8")
        prepare = brief + """\n你是研究主持人。仅输出 JSON：
{"questions": ["必须澄清的问题"], "queries": ["English academic keywords"], "scope": "研究边界"}。
只有课题宽泛到无法选择文献时才提问，最多3个问题；否则 questions 为空。
queries 为1–2个简短英文检索短语，各3–6个关键词，覆盖核心概念，不编造论文。
scope 是中文问题定义。"""
        if clarification:
            prepare += "\n用户已补充；不要再次提问，将仍未知的约束明确列为假设，questions 必须为空。"
        plan = parse_json(self.call("plan-resolved" if clarification else "plan", "codex", prepare))
        questions = string_list(plan.get("questions"), 3, "questions")
        queries = string_list(plan.get("queries"), 2, "queries")
        if not queries or not isinstance(plan.get("scope"), str) or not plan["scope"].strip():
            raise ValueError("缺少有效检索词或研究边界")
        write_json(self.directory / "plan.json", plan)
        if questions and not clarification:
            self.update(status="needs_input", questions=questions)
            self.event("等待你补充问题边界")
            return
        brief += "\n\n# 研究边界\n\n" + plan["scope"]
        (self.directory / "brief.md").write_text(brief, encoding="utf-8")
        self.stage("research")
        candidates, errors = literature.search(queries, self.directory)
        for error in errors:
            self.event("检索缺口 · " + error["provider"] + "：" + error["error"])
        write_json(self.directory / "candidates.json", candidates)
        if not any(p["abstract"] for p in candidates):
            raise ValueError("未取得任何可用摘要，无法据此开展有证据的辩论；检索记录已保留")
        selection = parse_json(self.call("selection", "codex", brief + "\n" + literature.render(candidates, errors) + """
从真实候选中挑选8–15篇最相关的文献，优先有摘要且与问题直接相关者；不足则如实少选。
仅输出JSON：{"selected": ["L1"], "reason": "选择依据及证据缺口"}。
不能选择不存在的ID，不能为凑数选择无关论文。"""))
        selected = string_list(selection.get("selected"), 15, "selected")
        by_id = {p["id"]: p for p in candidates}
        if not selected or len(set(selected)) != len(selected) or any(x not in by_id for x in selected):
            raise ValueError("文献筛选结果为空、重复或引用了不存在的编号")
        papers = [by_id[x] for x in selected]
        if not any(p["abstract"] for p in papers):
            raise ValueError("所选文献只有书目信息，不能作为研究发现的证据")
        evidence = literature.render(papers, errors)
        (self.directory / "literature.md").write_text(evidence, encoding="utf-8")
        write_json(self.directory / "papers.json", papers)
        history = ""
        for stage, instruction in (
                ("initial", "独立提出研究方向、文献依据、关键假设、局限和最小验证建议。"),
                ("round1", "逐条回应对方的主要观点。指出同意、反对与理由，明确哪些证据需要补查。")):
            self.stage(stage)
            answers = self.pair(stage, brief + "\n" + evidence + history + "\n任务：" + instruction +
                                "\n每人约800–1200中文字；证据引用用[L编号]。只读摘要不能声称读过全文。")
            for agent, answer in answers.items():
                history += f"\n\n## {dict(STAGES)[stage]} · {agent}\n\n{answer}"
            (self.directory / "debate.md").write_text(history, encoding="utf-8")
        self.stage("supplement")
        gaps = parse_json(self.call("gaps", "codex", brief + evidence + history + """
判断交锋中最关键的证据缺口。仅输出 JSON：{"queries": ["one short English search phrase"], "reason": "原因"}。
最多补查一个查询；已有材料足够讨论时queries为空，不把模型共识当作核验。"""))
        extra = string_list(gaps.get("queries"), 1, "queries")
        if extra:
            found, more_errors = literature.search(extra, self.directory, "supplement", papers)
            # Keep supplementary evidence visible; the final report judges its relevance.
            papers = found
            errors += more_errors
            evidence = literature.render(papers, errors)
            (self.directory / "supplement.md").write_text(evidence, encoding="utf-8")
            write_json(self.directory / "papers.json", papers)
            for error in more_errors:
                self.event("补查缺口 · " + error["provider"] + "：" + error["error"])
        else:
            (self.directory / "supplement.md").write_text("未追加检索。\n" + str(gaps.get("reason", "")), encoding="utf-8")
        self.stage("round2")
        answers = self.pair("round2", brief + evidence + history +
                            "\n回应质疑与补查证据，说明修改了哪些观点、仍有哪些分歧，给出最终判断与最小验证方案。"
                            "约800–1200中文字，引用[L编号]，允许暂时不能下结论。")
        for agent, answer in answers.items():
            history += f"\n\n## 第二轮交锋 · {agent}\n\n{answer}"
        (self.directory / "debate.md").write_text(history, encoding="utf-8")
        self.stage("summary")
        report = self.call("summary", "codex", brief + evidence + history + """
生成最终中文研究报告：课题解释、核心文献表（原始链接）、双方观点及修正、共识与分歧、
文献直接支持的判断、主持人推断、待验证假设、推荐方向和最小验证方案。
最小方案写明数据、基线、指标及什么结果会否定假设。逐项对照所给摘要审查关键主张，
纠正讨论中的过度外推；不能声称人工审阅或全文核验。仅引用材料已有[L编号]及对应真实链接。
少于8篇或检索失败要披露；缺乏证据可以不推荐。末尾写明这是自动摘要级证据审查，非系统性综述。
不要输出JSON，直接输出Markdown报告。""")
        known = {p["id"] for p in papers}
        unknown = set(re.findall(r"\[(L\d+)\]", report)) - known
        if unknown:
            raise ValueError("最终报告含未知文献编号：" + ", ".join(sorted(unknown)))
        (self.directory / "report.md").write_text(report, encoding="utf-8")
        self.check_cancel()
        self.update(status="completed", error=None)
        self.event("研究完成，报告与全部中间材料已保存")


if __name__ == "__main__":
    # The inherited lock FD keeps the single-job slot until this worker exits.
    Workflow(sys.argv[1]).execute()

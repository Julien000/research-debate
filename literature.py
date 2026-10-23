"""Fetch traceable scholarly metadata/abstracts; never pretend to read full text."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from run import write_json


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain(value):
    parser = PlainText()
    parser.feed(value or "")
    return " ".join(" ".join(parser.parts).split())


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "research-debate/0.2 (academic research tool)"})
    with urllib.request.urlopen(request, timeout=25) as response:
        data = response.read(8_000_001)
    if len(data) > 8_000_000:
        raise ValueError("Scholarly API response exceeded 8 MB")
    return data


def crossref(query):
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode({
        "query.bibliographic": query, "rows": 15,
        "select": "DOI,title,author,published,abstract,type,container-title"})
    data = json.loads(fetch(url))
    papers = []
    for item in data["message"]["items"]:
        if not item.get("DOI") or not item.get("title"):
            continue
        dates = item.get("published", {}).get("date-parts", [[]])
        papers.append({"title": plain(item["title"][0]), "doi": item["DOI"],
                       "url": "https://doi.org/" + urllib.parse.quote(item["DOI"], safe="/"),
                       "authors": [" ".join([a.get("given", ""), a.get("family", "")]).strip()
                                   for a in item.get("author", [])],
                       "year": dates[0][0] if dates and dates[0] else None,
                       "abstract": plain(item.get("abstract")), "source": "Crossref",
                       "publication_type": item.get("type", "unknown"),
                       "venue": "; ".join(item.get("container-title", [])), "query": query})
    return papers


def arxiv(query):
    # Query terms are data, never executable expressions or arbitrary fetch URLs.
    terms = re.findall(r"[\w-]+", query)[:12]
    expression = " AND ".join('all:"' + word + '"' for word in terms)
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({
        "search_query": expression, "max_results": 12, "sortBy": "relevance"})
    root = ET.fromstring(fetch(url))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    papers = []
    for item in root.findall("a:entry", ns):
        ident = item.findtext("a:id", "", ns)
        if "/abs/" not in ident:
            continue
        papers.append({"title": plain(item.findtext("a:title", "", ns)), "doi": "",
                       "url": "https://arxiv.org/abs/" + ident.split("/abs/", 1)[1],
                       "authors": [a.findtext("a:name", "", ns) for a in item.findall("a:author", ns)],
                       "year": item.findtext("a:published", "", ns)[:4],
                       "abstract": plain(item.findtext("a:summary", "", ns)), "source": "arXiv",
                       "publication_type": "preprint", "venue": "arXiv", "query": query})
    return papers


def search(queries, directory, prefix="search", existing=None):
    papers = list(existing or [])
    keys = {re.sub(r"\W", "", p["title"]).casefold() for p in papers}
    errors = []
    # Sequential per-provider queries respect arXiv's single-connection guidance.
    for index, query in enumerate(queries):
        with ThreadPoolExecutor(max_workers=2) as pool:
            calls = [(name, pool.submit(fn, query)) for name, fn in (("crossref", crossref), ("arxiv", arxiv))]
            for name, future in calls:
                try:
                    found = future.result()
                except (OSError, ValueError, KeyError, ET.ParseError) as exc:
                    errors.append({"provider": name, "query": query, "error": str(exc)})
                    continue
                write_json(directory / f"{prefix}-{index}-{name}.json", found)
                for paper in found:
                    key = re.sub(r"\W", "", paper["title"]).casefold()
                    if key in keys:
                        continue
                    keys.add(key)
                    next_id = max((int(p["id"][1:]) for p in papers), default=0) + 1
                    paper["id"] = f"L{next_id}"
                    paper["read_scope"] = "摘要" if paper["abstract"] else "仅书目信息"
                    papers.append(paper)
    write_json(directory / f"{prefix}-log.json", {"date": datetime.now(timezone.utc).isoformat(),
               "queries": queries, "errors": errors, "candidate_count": len(papers)})
    return papers, errors


def render(papers, errors):
    text = "# 文献证据材料\n\n来源为 Crossref / arXiv 的书目信息与可用摘要，未阅读论文全文。检索并不等同于系统性综述。\n"
    if len(papers) < 8:
        text += f"\n仅筛得 {len(papers)} 篇相关材料，未达到 8–15 篇目标，不补造文献。\n"
    if errors:
        text += "\n检索缺口：" + "; ".join(e["provider"] + "：" + e["error"] for e in errors) + "\n"
    for paper in papers:
        text += f"\n## [{paper['id']}] {paper['title']}\n"
        text += f"\n作者：{', '.join(paper['authors'])}；年份：{paper['year']}；类型：{paper['publication_type']}\n"
        text += f"\n来源：{paper['url']}\n\n阅读范围：{paper['read_scope']}\n"
        text += "\n" + (paper["abstract"] or "无可用摘要，不能据此推断研究发现。") + "\n"
    return text

# Research Debate · 研究工作台

输入一个研究课题，自动检索文献，让 Codex CLI 与 Claude CLI 独立分析、进行两轮交锋，再生成研究建议。网页保留每一步的材料，支持历史任务、澄清问题、进度查看、文件预览与下载、停止任务。

这是个人使用的研究辅助工具。双方一致不代表结论正确，报告不是系统性综述。

## 启动网页

需要 Python 3.10+、Linux/macOS，以及已安装并登录的 `codex`、`claude`。两个 CLI 必须能从启动服务的用户环境中找到。默认沿用各自账户与模型配置；CLI 名称不保证其后端模型厂商。

```bash
git clone git@github.com:Julien000/research-debate.git
cd research-debate
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/gunicorn --config gunicorn_config.py 'web.app:create_app()'
```

浏览器访问 **http://127.0.0.1:8080**。首次启动自动生成访问口令，存放在当前项目的 `.web-token`（仅当前系统用户可读）；在服务器终端运行 `cat .web-token` 查看，再输入网页登录。也可以在启动前设置至少16字符的 `RESEARCH_DEBATE_TOKEN` 环境变量，修改后须重启服务。口令、运行材料和缓存不会提交到 Git。

如果服务部署在远程服务器，请在自己的电脑执行：

```bash
ssh -N -L 8080:127.0.0.1:8080 用户名@服务器地址
```

然后在自己的浏览器打开 http://127.0.0.1:8080 。这里的地址通过 SSH 转发到服务器，并非服务器的公网地址。第一版不需要开放公网端口。

如需常驻后台，可用：

```bash
mkdir -p .runtime
.venv/bin/gunicorn --config gunicorn_config.py --daemon --pid .runtime/web.pid --access-logfile .runtime/access.log --error-logfile .runtime/error.log 'web.app:create_app()'
```

该命令不会配置系统开机自启。服务器重启后需要重新启动服务。若已有 HTTPS 反向代理，将其转发到本地8080，并设置 `RESEARCH_DEBATE_HTTPS=1`。监听地址可通过 `RESEARCH_DEBATE_BIND` 修改；不要将未启用 HTTPS 的口令登录直接暴露到不可信网络。

## 如何使用

1. 点击“新的研究”，输入课题，可以补充数据、对象和研究限制。
2. 如果问题过宽，网页会展示澄清问题。提交回答后自动继续。
3. 在进度页查看当前阶段与调用状态。各阶段生成的材料会出现在右侧，可以预览纯文本或下载原始 Markdown/JSON。
4. 研究结束后查看“讨论过程”和“最终报告”。原始观点不会被汇总报告覆盖。

一次运行一个研究任务；多个浏览器标签页不会重复启动同一任务。页面每2秒更新状态，模型回答在本次调用完成后出现。关闭网页不终止后台进程；停止按钮会终止正在执行的CLI调用，正在进行的文献请求需等待其超时或返回。

Web服务重启时，独立研究进程仍可继续；若研究进程本身中断，状态标记为“已中断”，已保存材料保留。第一版不自动重跑失败任务或从中途断点续跑，避免重复消耗额度。

## 自动研究流程与证据边界

- **明确问题**：Codex担任主持，生成研究边界和1–2组英文检索词，必要时向用户提问一次。
- **检索与筛选**：从 [Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) 和 [arXiv](https://info.arxiv.org/help/api/index.html) 获取真实书目信息与可用摘要，再筛选约8–15篇相关论文。少于目标数量如实报告；只有书目信息不能当作论文结论。
- **独立分析**：Codex和Claude收到相同材料，首次回答前不交换观点。
- **第一轮交锋**：双方收到完整首次回答，逐条审查对方观点。
- **补查**：主持人提取关键证据缺口，最多追加一次检索，补充候选材料保留原始来源；检索失败可见，不静默伪造材料。
- **第二轮交锋**：双方收到补查材料和前轮完整记录，回应质疑、修正或保留立场。
- **汇总**：主持人基于取得的摘要审查主张，生成共识、分歧、推荐方向及最小验证方案，检查最终报告是否引用了不存在的文献编号。

当前不自动下载或精读论文全文，无法核验全文中的实验细节。Crossref的摘要覆盖并不完整，arXiv也不覆盖所有学科；两者可能超时或限流。单个来源失败时会保留缺口并使用另一来源的实际材料；完全没有摘要时停止讨论。自动检查不能保证每条引用都支持相应结论，仍需要你审阅重要判断。

默认固定两位辩手、首次分析加两轮交锋（六次辩手调用）；主持人的规划、筛选、补查判断与汇总另外消耗模型调用。每次CLI调用最多600秒，不自动无限追加轮次或重试。单次完整研究通常需要等待多次模型返回，不承诺固定完成时间。

## 保存哪些文件

每次研究存放在 `results/<任务ID>/`：

- `job.json`：课题、任务状态和用户补充；`events.jsonl`：阶段事件。
- `brief.md`、`plan.json`：研究边界和检索计划。
- `search-*.json`、`candidates.json`：真实检索结果与失败记录。
- `papers.json`、`literature.md`、`supplement.md`：筛选后的核心文献及补查材料。
- 每次调用的独立目录：完整提示词、回答、状态、原始CLI输出。
- `debate.md`、`report.md`：公开讨论记录与最终报告。

网页仅开放任务目录内的 Markdown/JSON 文件，不开放任意服务器路径；原始CLI标准输出/错误及工作进程日志仅在服务器保留。保存的讨论是公开论证，不是模型隐藏思维过程。未经登录无法读取研究材料。

## 保留的命令行接口

仍可在当前对话中让助手主持研究，或单独执行一次贡献：

```bash
python run.py --agent codex --prompt results/某次讨论/initial.md --out results/某次讨论/initial-codex
python run.py --agent claude --prompt results/某次讨论/initial.md --out results/某次讨论/initial-claude
```

每次使用新的输出目录。`--timeout` 指定超时秒数，默认600；成功返回0，失败返回非零并保留材料。既有目录拒绝覆盖。单独使用此接口无需 Flask。

## 验证与结构

```bash
.venv/bin/python -m unittest discover -s . -p 'test_*.py' -v
node --check web/static/app.js
```

测试使用模拟模型与检索返回，不消耗模型额度。覆盖CLI失败/取消、完整流程的数据传递、证据不足、未知引用、网页登录、跨站请求、文件目录边界、单任务限制和中断状态恢复。

`run.py`负责单次CLI调用；`literature.py`负责学术来源检索；`workflow.py`负责完整流程；`web/`负责网页与任务接口。Python依赖仅Flask、Gunicorn及其必需的传递依赖，前端无需构建工具。

界面和任务体验参考了 [Crucible](https://github.com/Starlight143/crucible) 的阶段进度与研究记录思路，应用代码独立实现。

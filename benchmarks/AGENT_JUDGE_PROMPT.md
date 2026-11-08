# 让 Agent 用宿主内置模型做评审：可直接粘贴的提示词

适用于 Codex、Antigravity、Cursor Agent 等能读写本地文件、能执行命令的 Agent。
建议用**非 DeepSeek 系列**的宿主模型（如 Codex 的 GPT、Antigravity 的 Gemini），因为补丁阶段的宿主是 DeepSeek（`deepseek-flash`），同家模型当评委容易偏爱它的改写。

准备：在项目根目录执行过 `python benchmarks/agent_judge.py export --run <轮次>`（补丁阶段全部完成后再导出）。

**本轮：`m3judge`**（M3 定稿复核：10 篇 × 3 个匿名候选＝原文、法语往返补丁版、俄语往返补丁版，共 30 个候选、10 个包）。
本轮使用评分规则 v2：评审包里附了“人味 / 可读性”的正反例，强调好读不等于信息密度高。
下面提示词里的 `m3judge` 换成要评的轮次即可。

把下面整段粘贴给 Agent：

---

你是这次实验的**评委**。请用你自己（当前宿主模型）的阅读和判断，给中文技术文本的改写版本打分。
这项工作不能交给任何脚本或规则来完成。

**工作目录**：项目根目录。相关文件：
- 评审包：`benchmarks/results/m3judge/agent_judge/packets/<包名>.md`，每包含 1 篇参考原文和若干匿名候选（A、B、C……）
- 答卷：`benchmarks/results/m3judge/agent_judge/answers/<包名>.json`，骨架已生成，你只需要填写

**循环执行，直到全部完成：**

1. 运行 `python benchmarks/agent_judge.py status --run m3judge`，挑一个未完成的包。
2. **完整阅读**这个包的 `.md` 文件：先读评分规则和其中的对照例子，再读参考原文，再逐个读候选。文件较长时分段读，但每个候选都必须读完。
3. 对每个候选**独立**打分（不要互相排名），写进对应的答卷 JSON：
   - `judge_model` 填你实际使用的模型名，例如 `gpt-5.5-codex` 或 `gemini-3.1-pro`；
   - `fidelity`、`readability` 填 1~5 的整数；
   - `errors` 和 `readability_issues` 的每一条都写成 `{"quote": "逐字引文", "why": "一句话说明"}`。
     quote 必须从文件里**逐字复制**（4~80 字），导入时会被代码核实，对不上的会被拒收。
4. 运行 `python benchmarks/agent_judge.py import --run m3judge`。如果有 ✗ 提示，按提示修改该答卷，再执行一次 import，直到这个包没有 ✗。
5. 回到第 1 步。

**禁止事项**（违反任何一条，结果都会作废）：
- 禁止编写或运行任何生成分数、错误、引文的代码，包括根据长度、指标、关键词、补丁记录推算分数。
  命令行只用来运行上面的 `status` / `import` 和读取文件。
- 禁止打开 `benchmarks/.cache/` 下的任何文件，以及 `benchmarks/results/` 下各轮次目录中的 `rtt.jsonl`、`patch.jsonl`、`judge.jsonl`、`rows.jsonl`、`summary.*`。
  这些文件会暴露候选的来源，破坏盲评。
- 禁止修改评审包 `.md` 文件和项目里的代码。
- 不确定时按评分规则如实打分；不要为了加快速度给多个候选填相同的分数和理由。

**上下文快满时**：先把当前这个包写完并 import，然后停下来告诉我进度。下一次会话从第 1 步继续，进度保存在文件里。

全部完成后，运行 `python benchmarks/run_ablation.py audit --run m3judge` 和 `python benchmarks/run_ablation.py report --run m3judge`，把两条命令的输出贴给我。

---

## 用户侧检查

- 跑完先看 `python benchmarks/run_ablation.py audit --run m3judge`：应为 30/30，没有 ⚠。
- 评委建议继续用 `gpt-6`。本轮评分规则改为 v2，易读度分数只在本轮内部比较。
- 答卷 `answers/*.json` 会提交进仓库，作为评审证据。
- 可以抽查几份答卷，看引文和理由是否言之有物。

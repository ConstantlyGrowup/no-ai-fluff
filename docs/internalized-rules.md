# 内化规则参考

no-ai-fluff 的改写规则不是一份外挂的写作指南，而是内化在 MCP 引擎里的三样东西：

- 本地雷达：`src/no_ai_fluff/radar.py`、`src/no_ai_fluff/en/radar.py`；
- 领域分诊：`src/no_ai_fluff/telegraph_router.py`；
- 每次 `prepare_rtt_draft` 返回给宿主模型的审校提示词：`src/no_ai_fluff/patcher.py`、`src/no_ai_fluff/en/rules.py`。

本页是紧凑的参考，不复述完整提示词。各处的例子要么摘自提示词原文，要么是最终版评测里真实生效的补丁，后者注明了出处。

English summary: see [the last section](#english-edition).

---

## 1. 两条判断准绳

### 1.1 真术语，还是包装黑话

一句话版本：**去概念索引里查得到的是术语，保留；换成“做、建、接、跟完、主线”这类说法后信息一点不少的，是包装黑话，改写。**

提示词里的正式判据是一个反问：**换一个普通说法，懂行的读者会不会误解成另一件事？** 会，就是真术语；不会，就是包装黑话。

| | 例子（摘自提示词） | 处理 |
|---|---|---|
| 真术语 | SHA256、多臂老虎机、熵值、幂等、P99 尾延迟、Trace、Holdout、消融实验、事务、死锁 | 一字不动 |
| 包装黑话 | 挂载契约门禁、控制并发水位、协议对齐、固化基线、语义分簇；收口、底座、兜底、心智、颗粒度、抓手、打通、闭环 | 说出这个词本身指的动作 |

改写黑话只说出原文本来的意思，不替作者补充没写的机制、后果或分支。正负、前后、方向、顺序照原文写，即使看起来像是原文写错了。

### 1.2 往返翻译当照妖镜

往返翻译（RTT）会把原文翻成外语再翻回来：中文版走中文 → 俄语 → 中文，英文版走英文 → 日语 → 英文，翻译模型是 hy-mt2。

通用术语在外语里有固定译法，往返后基本不变。生造的包装词在外语里没有对应习语，翻译模型只能按字面意思处理，翻回来常常变成平实的说法。下面是提示词里给宿主的例子：

| 原文 | 往返后 |
|---|---|
| 语义分簇 | 语义聚类 |
| 并发水位 | 并行度 |
| 灰度放行 | 逐步部署上线 |
| drive alignment | agree |
| de-risks the runway | reduces the risk of the migration |

所以宿主看到“原文里像术语、往返稿里变成普通说法”的词，就把它当作包装黑话，顺着参考稿改直白。

往返稿本身会走样，比如把“运维成本”译成“运营成本”、把 amortized O(1) 译成 average O(1)。因此它只作参考：补丁永远打在原文上，术语、数字、语气以原文为准。

---

## 2. 双通道门控

```text
原文 ──► 本地雷达（只看句法和统计结构）
            │
            ├─ 没有“电报式短句群” ───────────────────────► 基础通道
            │
            └─ 有电报式短句群 ─► 领域分诊（宿主判断这几句是否出自技术/工程语境）
                                   ├─ 全部不是（旅游攻略、生活叮嘱等）─► 基础通道
                                   └─ 至少一句是 ──────────────────────► 强化通道
```

- **基础通道**：提示词与第一代单通道规则逐字相同。
- **强化通道**：在基础规则上增加三项：
  - 真术语与包装黑话的判据（1.1 节）；
  - 往返稿照妖镜（1.2 节）；
  - 一个新问题类型 [电报堆叠] / [telegraph-stack]，只拆句，不补原文没有的信息。
- **领域分诊**由宿主模型在握手里完成：`prepare_rtt_draft` 先返回待判断的句子，宿主带上 `draft_id` 和 `domain_triage` 再调一次。往返翻译不会重跑，判断结果按句子 sha256 缓存在本地。普通稿子不会触发这一步。

### 2.1 雷达：运行时不背词表

运行时的雷达只扫句法和统计结构，不内置黑话词表。原因见 README 里“为什么句式雷达只看结构”一节。黑话词表只留在评测脚本里，做事后统计。

| 中文雷达（运行时） | 判定 |
|---|---|
| 长句 / 定语堆积 | 单个逗号分句超过 40 字，或一个分句里有 3 个以上“的”；一整句超过 70 字且内部有 4 个以上逗号、顿号、冒号 |
| 散装英文 | 没被冻结、混在正文里的日常英文词 |
| 密集代词 | 一句里有 3 个以上“它 / 其”，或一个分句里有 2 个以上 |
| 电报式短句群 | 一句话（去掉标点和占位符）至少 24 字，被逗号、顿号、分号、冒号切成至少 4 段，平均每段 5～11 字，虚词与连接词占比不超过 4% |

| 英文雷达（运行时） | 判定 |
|---|---|
| long sentence | 一句超过 25 个词（提示用）；评测统计时以 30 个词为长句 |
| noun stack / nominal-heavy | 名词连排作修饰语；动作藏在 -tion、-ment、-ance 等名词里 |
| symbol for word / label fragment | 用 `/`、`+`、`→`、`—` 代替词语；用“Goal: ...”这类标签代替句子 |
| telegraphic sentence chain | 一句至少 12 个词，切成至少 3 段，平均每段 3～8 个词，虚词占比不超过 0.30 |

英文阈值与中文不同。英文的黑话密句通常是几个虚词很少的短语连成一串，而不是中文那种两三个字一段的碎片。

---

## 3. 问题类型

宿主开的每条补丁都必须在 `type` 里写明修的是哪一类问题；写不出类别的补丁会被代码拒收。

### 3.1 中文：基础通道 10 类 + 强化通道 1 类

| 类型 | 指什么 | 改前 → 改后（真实补丁） |
|---|---|---|
| 残句堆砌 | 主谓不全，用名词短语堆砌代替句子；一句塞太多信息 | 后台合并会带来： → 后台合并会带来这些开销：（封存集 teaching-009） |
| 隐喻黑话 | 空间动作、跨界比喻、包装黑话充当逻辑 | 构建提速的核心抓手 → 构建提速主要靠这几件事（封存集 writing-010） |
| 自造代号 | 要回溯上下文才猜得出的自造缩略说法 | **管理比较源** → **少拿别人当参照**（Dev daily-005） |
| 中英混杂 | 有通行中文说法的地方夹英文 | smoke test → 冒烟测试（封存集 writing-010） |
| 翻译腔 | 长定语、被动堆叠、生硬直译，先列名词后说动作 | 让其在实践中学习 → 让他们在实践中学习（Dev writing-006） |
| 空洞大词 | 口号、排比、没有度量的夸张修饰 | 小步提交意愿增强 → 更愿意小步提交（封存集 writing-010） |
| 推理跳步 | 读者要自己补“因为、所以” | 单件那一行不放按钮，底部已有「去打印文件」。 → 单件那一行不放按钮，因为底部已经有「去打印文件」了。（Dev coding-011） |
| 冗余 | 重复、复述提问、只预告下文的铺垫、口号式收尾 | 下面为你详细拆解这两个问题。 → 删除（封存集 discussion-010） |
| 指代不清 | “它、其、该”指代不明，主客体容易弄混 | 届时部分系统将无法使用 → 届时内网系统将无法使用（封存集 writing-007） |
| 省略压缩 | 像发电报一样省字：省掉“的、了、在、把”或宾语；用 `/ + — →` 代替“或、和、到、然后”；用标签代替句子 | 基础镜像版本固定 → 把基础镜像版本固定下来（封存集 writing-010） |
| **电报堆叠**（仅强化通道） | 一句里连排一串“动作 + 抽象名词”的短片段；只拆句，主语和连接词只补上下文里已有的 | 通过 Prepare 阶段逐 slot 学习、补齐已接受的值 → 通过 Prepare 阶段逐个 slot 学习，把已接受的值补齐（封存集 teaching-006） |

### 3.2 英文：基础通道 11 类 + 强化通道 1 类

| type | 指什么 | before → after（真实补丁，除非注明） |
|---|---|---|
| telegraphic | 省掉冠词、动词或宾语；符号代替词；标签代替句子 | Caffeine cutoff 2 pm. → Have your last caffeine by 2 pm.（tune） |
| noun-stack | 三个以上名词连排作修饰语 | flake-rate-driven CI retry overhead → CI retry overhead caused by test flakiness（tune） |
| buried-verb | 动作藏在名词或生造动词里 | that empowers teams to seamlessly operationalize their reporting workflows → that helps teams run their reporting workflows（tune） |
| inanimate-agent | 让物做只有人能做的事，掩盖了真正的行动者 | Any other format rejects the whole batch → If any file is in any other format, the whole batch is rejected（test） |
| hard-clause | 超过约 25 个词的句子、主谓之间夹长列表、省掉 that 的宾语从句 | the boundaries the codebase already encodes → the boundaries that the codebase already defines（tune） |
| jargon | 商业或空间比喻充当逻辑 | unblocks the release-train critical path → removes a blocker from the critical path for the release train（tune） |
| coined-label | 读者要从上下文解码的临时压缩短语 | the no active RM retry → the retry when there is no active RM（test） |
| empty-words | 没有可度量内容的夸张形容词、副词和口号 | We're excited to unveil a reimagined export experience → We're introducing a redesigned export feature（tune） |
| missing-link | 读者要自己补“because、so” | 提示词定义，评测集里没有短例 |
| redundant | 重复、只预告下文的铺垫、口号式收尾 | It's worth noting that in CPython → In CPython（tune） |
| unclear-reference | it、this、that 指代不明 | none of its CVEs were matched. → none of the package's CVEs were matched.（tune） |
| **telegraph-stack**（仅强化通道） | 一句里连排多个“动作 + 抽象名词”短语，拆成两三句完整的话，不补原文没有的条件和机制 | 提示词例子：“Post-merge, stale-read windows the old flow never exposed now surface under concurrent checkout load” |

中文出处里的“封存集”指 `benchmarks/results/holdout-v16`，“Dev”指 `benchmarks/results/r12-dev`。英文出处里的 tune 指 `benchmarks/results/en-r5`，test 指 `benchmarks/results/en-test-en5`。

---

## 4. 补丁守卫（代码强制，与提示词无关）

宿主只能提交 `[{"find", "replace", "type", "reason"}]` 这样的局部替换补丁。合并由确定性代码完成，单条补丁出现以下情况就整条拒收，其余补丁照常生效：

- `find` 在原文里找不到，或出现不止一次（有歧义）；
- 跨行，或超过 400 字；
- 切断、丢失或复制了被冻结的代码、公式、路径、URL（`{{Pn}}` 占位符）；
- 改动了标题级别、列表编号等行首结构，或改变了表格列数；
- `type` 不在当前通道允许的类型里。

合并后还有一次定稿校验：任何受保护内容缺失，整篇原样退回原文。

### 4.1 定稿之后：点名整段重写

MCP 定稿只做局部修改。如果用户读完觉得某一段还是太密，可以点名让主会话只把那一段整段重写。这一步不经过 MCP 和补丁守卫。规则和那句提示的唯一来源是 `src/no_ai_fluff/delivery_note.py`，内容提炼自本页，并通过三条路交给宿主：`finalize_patched_delivery` 的返回（定稿后的“给宿主的交付说明”，任何 MCP 客户端都看得到）、通用 Skill 与 `AGENTS.md` 段落（`adapters/generic*/`，供 Codex、Qoder、WorkBuddy、ZCode 等），以及 Claude Code 的斜杠命令模板（`adapters/claude_code*/skill.md`）。要点：

- 只改点名的部分；
- 代码、路径、URL、公式、版本号和数字一字不动，不补原文没写的机制和条件；
- 句子语法完整：箭头链、电报句拆成有主语、有连接词的完整句子，长句拆短；
- 用大白话：真术语保留，包装黑话拆成实际动作（第 1.1 节），没有依据的夸张修饰删掉或换成原文里的事实；
- 不发明晦涩代号，原文自造的代号第一次出现时说清它指什么；
- 原文里有的中间步骤不省略，容易指代不清的词写清谁对谁做了什么（只用原文已有的信息）；
- 信息充分的前提下尽量简洁，允许整段重组语序，但必须忠于原意。

定稿交付时，主会话会在聊天里附一句提示告诉用户可以这样做；落盘文件里不写这句提示。

---

## 5. 版本

只保留两个架构代际。其余实验轮次的提示词和结果见 `benchmarks/optimization_ledger.jsonl` 与 git 历史。

| 代际 | 中文 | 英文 | 说明 |
|---|---|---|---|
| 第一代 v1 | `v1`（账本名 `v8`） | `v1`（账本名 `en2`） | 单通道：补丁打在原文上，往返稿只作参考，雷达含静态黑话词表 |
| 第二代 v2（默认） | `v2`（账本名 `v16`） | `v2`（账本名 `en5`） | 双通道门控：结构雷达 + 领域分诊 + 强化通道 |

切换：`NO_AI_FLUFF_AUDIT_RULES=v1`（中文）、`NO_AI_FLUFF_EN_RULES=v1`（英文）。账本名作为兼容键保留，与代际名指向同一份规则。

---

## English edition

The English edition (`no-ai-fluff-en`, English → Japanese → English) uses the same machinery:

- **Genuine term or packaging jargon?** Ask whether a plain substitute would make an expert misunderstand. If it would, keep the word. If not, it is jargon: say the plain action it stands for, and add nothing the author did not write.
- **The round trip as a jargon detector.** Genuine terms (idempotent, deadlock, P99) come back unchanged. Coined jargon usually comes back as plain words ("drive alignment" → "agree"). The round trip also drifts (amortized O(1) → average O(1)), so patches always land on the original.
- **Two gated channels.** The runtime radar only measures sentence structure, with no jargon word list. A telegraphic sentence chain triggers a domain-triage question to the host. Technical context gets the stronger rules and type 12, [telegraph-stack]. Everything else gets the first-generation prompt verbatim.
- **Patch types**: the eleven base types in 3.2, plus [telegraph-stack] in the stronger channel. The patch guard in section 4 is shared with the Chinese edition.

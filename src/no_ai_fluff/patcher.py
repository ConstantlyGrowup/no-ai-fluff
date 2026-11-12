"""Stage 2b & 3：宿主模型审校提示词 + JSON 局部补丁的安全合并。

宿主大模型（运行在独立子上下文里）只被允许输出
    [{"find": "初稿原片段", "replace": "修改后片段", "reason": "原因"}]
这样的局部替换补丁；本模块用确定性代码逐条合并，任何一条补丁只要
- 在初稿里找不到 / 出现不止一次（有歧义）
- 跨越了结构边界（跨行、跨表格单元格、跨代码块）
- 切断、丢失或复制了被冻结的代码 / 公式 / 路径（{{Pn}}）
就整条拒收，其余补丁照常生效。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from .masker import _LINE_PREFIX_RE, PLACEHOLDER, PLACEHOLDER_RE, Document, check_placeholders

# ---------------------------------------------------------------------------
# 审校规则：只保留两个架构代际（其余实验轮次见 benchmarks/optimization_ledger.jsonl 与 git 历史）
#
#   第一代 v1（原 v8）：单通道。补丁打在原文上，往返稿只作“直白说法参考”，雷达报原文自己的疑点，
#       宿主按 10 类问题开补丁。
#   第二代 v2（原 v16，默认）：双通道门控。
#       - 雷达只看句法/统计结构（运行时不跑静态黑话词表），新增“电报式短句群”提示；
#       - 命中电报句时先做领域分诊（telegraph_router，宿主握手完成）：出自技术/工程语境 → 整篇走强化通道
#         AUDIT_INSTRUCTIONS_V16_DENSE（真术语 vs 包装黑话判据 + 往返稿照妖镜 + 第 11 类 [电报堆叠] 只拆不补）；
#         否则（或没有电报句）→ 提示词与第一代逐字相同（基础通道）；
#       - 定稿后清理补丁在两个汉字之间留下的多余空格。
# “v8”“v16”是评测账本与结果目录里的原名，作为兼容键保留，与“v1”“v2”指向同一份规则。
# ---------------------------------------------------------------------------

PROBLEM_TYPES_V8 = ("残句堆砌", "隐喻黑话", "自造代号", "中英混杂", "翻译腔", "空洞大词", "推理跳步", "冗余", "指代不清", "省略压缩")
PROBLEM_TYPES_V16 = PROBLEM_TYPES_V8 + ("电报堆叠",)

AUDIT_INSTRUCTIONS_V8 = """你是中文编辑。请逐段通读【原文】，把晦涩、句式不符合中文阅读习惯、读着费劲的地方，用补丁改成有经验的人当面讲给同事听的说法。
每一段都问一句：同事当面讲，会这样说吗？会换个说法的地方，就出补丁。真正自然的句子不动；只有通篇都经得起这个检验，才输出 []。需要大改的段落可以大改，改多少由问题决定，不设上限。

【往返参考稿】是原文经机器翻译成外语再译回中文的结果。它的句子往往更松、更直白，可以借用它的说法；
但它也会带进机翻错误：术语译错（例如“运维成本”被译成“运营成本”）、半中半英的句子、生硬的翻译腔。
借用前逐词对照原文：术语、专有名词、数字和语气以原文为准，参考稿里的错误不准带进补丁。

【雷达提示】里的每一条疑点都要核对一遍：确有问题就修，核对后没问题的可以不改。

找问题的依据（每条补丁的 type 必须是下面方括号里的名称之一，写不出属于哪一类，就说明不该改）：
1. [残句堆砌] 句子主谓不全，用名词短语堆砌代替句子；或一句话塞进太多信息，读者要停下来拆解——拆成两三句；
2. [隐喻黑话] 用“打通、闭环、下沉、赋能、抓手”这类空间动作或黑话充当逻辑，比喻对应不到实物实量；
3. [自造代号] 读者需要回溯上下文才能猜出意思的自造代号或压缩短语，把它说全；
4. [中英混杂] 有通行中文说法的地方夹英文，或半中半英的句子；没有通行中文译法的技术术语、产品名、代码相关内容保留英文；
5. [翻译腔] 长定语、被动堆叠、“对……进行……”“享受……”这类直译搭配；信息顺序别扭，例如先列一串名词、最后才说要做什么——先说动作或结论；
6. [空洞大词] 空洞大词、口号式排比、没有度量的夸张修饰；能给具体事实就给，给不出就删；
7. [推理跳步] 推理跳步，读者要自己补“因为、所以、这样”才能跟上；
8. [冗余] 重复说同一件事、复述提问的开场白、只预告下文的铺垫、口号式的收尾；没有信息的整句可以删掉；
9. [指代不清] “它、其、该”等指代不清，主体客体容易弄混；
10. [省略压缩] 像发电报一样省字：省掉了“的、了、在、把”或动词的宾语，用“/、+、—、→”代替“或、和、到、然后”，用“目标：……”这类标签代替句子。补回省掉的字，说成完整的话。

不变的底线：
- 读者读完定稿得到的认识必须和读原文完全相同：数字、单位、条件边界、语气强弱、因果方向、主客体、专业术语都不能变，也不能增删信息；
- 如果给了【用户问题】，问题里出现的术语、比喻和简称照用；
- 不改文档结构：标题层级、列表、表格、代码块保持原样，其中的文字可以改；
- 代码、路径、公式、URL、数字和反引号里的内容原样保留，每个只出现一次，可以调整它们在句中的位置。

输出格式：只输出 JSON 数组，每条补丁：
{"find": "原文片段", "replace": "修改后片段", "type": "上面十类之一", "reason": "这里读着费劲的具体原因"}
- find 逐字摘自【原文】，在全文中唯一，不跨行；可以是一个词、一句话或同一行内的整段（不超过 400 字）；
- 同一处问题只出一条补丁，补丁之间不要重叠。"""

AUDIT_INSTRUCTIONS_V16_DENSE = """你是中文编辑。请逐段通读【原文】，把晦涩、句式不符合中文阅读习惯、读着费劲的地方，用补丁改成有经验的人当面讲给同事听的说法。
每一段都问一句：同事当面讲，会这样说吗？会换个说法的地方，就出补丁。真正自然的句子不动；只有通篇都经得起这个检验，才输出 []。需要大改的段落可以大改，改多少由问题决定，不设上限。

【先分清：真术语和包装式行话】
- 真术语，必须原样保留：教科书、论文、标准协议或代码库里有严格唯一定义、换个说法就会产生技术歧义的概念，例如 SHA256、多臂老虎机、熵值、幂等、P99 尾延迟、Trace、Holdout、消融实验、Reward Hacking、分布偏移、Tag、事务、死锁。
- 包装式行话，不受保护，要改成直白的动作：借物理、水利、物业、军事、管理等领域的词，包装一个普通的工程操作或状态。例如“挂载契约门禁”（加上接口契约检查）、“控制并发水位”（控制并发量）、“协议对齐”（检查协议是否一致）、“触发策略收敛”（让策略收敛稳定）、“固化基线”（把基线固定下来）、“语义分簇”（按语义聚类），以及“收口、底座、兜底、心智、颗粒度、抓手、打通、闭环”。只要不改变事实，就拆成同事当面会说的动词短语。
- 把行话改直白，是说出这个词本身指代的动作（可借用参考稿的直白说法），绝不是替作者补充他没写的业务后果或分支。
- 拿不准时问自己：换一个普通说法，懂行的读者会不会误解成另一件事？会，就是真术语，保留；不会，就是行话，改掉。

【往返参考稿：行话照妖镜】
【往返参考稿】是原文经机器翻译成俄语、再译回中文的结果。真正的通用技术术语（SHA256、多臂老虎机、幂等）往返后基本不变；中式行话和生造的缩略说法在外语里没有对应习语，往返后常被洗成直白的意思（例如“语义分簇”变成“语义聚类”，“并发水位”变成“并行度”，“灰度放行”变成“逐步部署上线”）。
所以：原文里看起来很专业的词，如果在参考稿里变成了普通说法，它多半是行话而不是专有名词，请顺着参考稿的方向改直白，不要因为“原文用了这个词”就保留。
参考稿也会带进机翻错误：把真术语译偏（例如“运维成本”被译成“运营成本”）、半中半英的句子、生硬的翻译腔。真术语、专有名词、数字和语气以原文为准，这类错误不准带进补丁。

【雷达提示】里的每一条疑点都要核对一遍：确有问题就修，核对后没问题的可以不改。“电报式短句群”这一类，如果只是并列罗列事物（地名、字段名、测试项），可以不改；如果是把一串“动作 + 抽象名词”压成了电报，按第 11 类拆开，只拆不补。

找问题的依据（每条补丁的 type 必须是下面方括号里的名称之一，写不出属于哪一类，就说明不该改）：
1. [残句堆砌] 句子主谓不全，用名词短语堆砌代替句子；或一句话塞进太多信息，读者要停下来拆解——拆成两三句；
2. [隐喻黑话] 用空间动作、跨界比喻或上面说的包装式行话充当逻辑，比喻对应不到实物实量；改成具体做了什么；
3. [自造代号] 读者需要回溯上下文才能猜出意思的自造代号或压缩短语，把它说全；
4. [中英混杂] 有通行中文说法的地方夹英文，或半中半英的句子；没有通行中文译法的技术术语、产品名、代码相关内容保留英文；
5. [翻译腔] 长定语、被动堆叠、“对……进行……”“享受……”这类直译搭配；信息顺序别扭，例如先列一串名词、最后才说要做什么——先说动作或结论；
6. [空洞大词] 空洞大词、口号式排比、没有度量的夸张修饰；能给具体事实就给，给不出就删；
7. [推理跳步] 推理跳步，读者要自己补“因为、所以、这样”才能跟上；
8. [冗余] 重复说同一件事、复述提问的开场白、只预告下文的铺垫、口号式的收尾；没有信息的整句可以删掉；
9. [指代不清] “它、其、该”等指代不清，主体客体容易弄混；
10. [省略压缩] 像发电报一样省字：省掉了“的、了、在、把”或动词的宾语，用“/、+、—、→”代替“或、和、到、然后”，用“目标：……”这类标签代替句子。补回省掉的字，说成完整的话；
11. [电报堆叠] 一句里连排一串“动作 + 抽象名词”的短片段（例如“入口收紧准入门槛，挂载契约门禁和安全边界”“锁定版本、打 Tag、固化基线”），读者要自己拆。里面有真术语也要拆：拆成两三句主谓完整、一口气能读顺的话，补上上下文里本来就有的主语和“先、再、然后”等连接词，把里面的包装式行话改成具体动作；原文没交代的条件、分支和机制一律不补。

不变的底线：
- 读者读完定稿得到的认识必须和读原文完全相同：数字、单位、条件边界、语气强弱、因果方向、主客体、真术语都不能变，也不能增删信息；把行话改直白时，只说出原文本来的意思，不编造原文没有的机制和细节；正负、前后、方向、顺序这类细节照原文写，即使你觉得原文有误也不改；
- 如果给了【用户问题】，问题里出现的术语、比喻和简称照用；
- 不改文档结构：标题层级、列表、表格、代码块保持原样，其中的文字可以改；
- 代码、路径、公式、URL、数字和反引号里的内容原样保留，每个只出现一次，可以调整它们在句中的位置。

输出格式：只输出 JSON 数组，每条补丁：
{"find": "原文片段", "replace": "修改后片段", "type": "上面十一类之一", "reason": "这里读着费劲的具体原因"}
- find 逐字摘自【原文】，在全文中唯一，不跨行；可以是一个词、一句话或同一行内的整段（不超过 400 字）；
- 同一处问题只出一条补丁，补丁之间不要重叠。"""
assert "电报堆叠" not in AUDIT_INSTRUCTIONS_V8 and "上面十类之一" in AUDIT_INSTRUCTIONS_V8
assert AUDIT_INSTRUCTIONS_V16_DENSE.count("11. [电报堆叠]") == 1 and "只拆不补" in AUDIT_INSTRUCTIONS_V16_DENSE

TELEGRAPH_MARK = "[电报式短句群]"
RULE_ALIASES = {"v1": "v8", "v2": "v16"}  # 代际名 -> 账本原名
AUDIT_RULES = {"v1": AUDIT_INSTRUCTIONS_V8, "v8": AUDIT_INSTRUCTIONS_V8,
               "v2": AUDIT_INSTRUCTIONS_V16_DENSE, "v16": AUDIT_INSTRUCTIONS_V16_DENSE}  # 第二代无技术电报句时由 effective_rules 退回第一代
DEFAULT_AUDIT_RULES = "v16"  # 2026-11-12 定型（= 第二代 v2）
# 第二代门控：雷达提示里没有（经分诊保留的）电报式短句群时，提示词用第一代原文
GATED_RULES = {"v16": "v8", "v2": "v1"}
ROUTED_RULES = {"v16", "v2"}  # 电报提示先过领域分诊
DENSE_RADAR_RULES = {"v16", "v2"}  # 雷达加扫电报式短句群
NO_CLICHE_RADAR_RULES = {"v16", "v2"}  # 运行时雷达不跑静态黑话词表（评测台统计残留时仍用）
CJK_SPACE_RULES = {"v16", "v2"}  # 定稿后清理汉字间多余空格
ORIGINAL_BASE_RULES = set(AUDIT_RULES)  # 补丁打在原文上（往返稿只作参考）
PROBLEM_TYPES = {"v1": PROBLEM_TYPES_V8, "v8": PROBLEM_TYPES_V8, "v2": PROBLEM_TYPES_V16, "v16": PROBLEM_TYPES_V16}
AUDIT_INSTRUCTIONS = AUDIT_RULES[DEFAULT_AUDIT_RULES]  # 向后兼容
# 单条补丁 find 的长度上限：可以是同一行内的一整段，但不得跨行
MAX_FIND_CHARS = 400
MAX_FIND_BY_RULES = {r: MAX_FIND_CHARS for r in AUDIT_RULES}


def resolve_rules(rules: str) -> str:
    """把代际名（v1 / v2）换成账本原名（v8 / v16）；未知名称原样返回。"""
    return RULE_ALIASES.get(rules, rules)


def effective_rules(rules: str, radar_text: str) -> str:
    """第二代门控：雷达没有报“电报式短句群”时，提示词用第一代原文。其余规则原样返回。"""
    if rules in GATED_RULES and TELEGRAPH_MARK not in radar_text:
        return GATED_RULES[rules]
    return rules


_CJK = r"[㐀-鿿　-〿＀-￯]"
_CJK_SPACE_RE = re.compile(rf"({_CJK})[ \t]+(?={_CJK})")


def tidy_cjk_spaces(text: str, original: str) -> tuple[str, int]:
    """去掉两个中文字符（含全角标点）之间、原文里本来没有的空格。返回 (新文本, 清理处数)。"""
    n = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal n
        nxt = text[m.end()]
        if m.group(0) + nxt in original:
            return m.group(0)
        n += 1
        return m.group(1)

    return _CJK_SPACE_RE.sub(repl, text), n


def build_audit_prompt(
    original: str, rtt_draft: str, radar_text: str, rules: str = DEFAULT_AUDIT_RULES, question: str | None = None
) -> str:
    if rules not in AUDIT_RULES:
        raise ValueError(f"未知审校规则 {rules}，可选：{', '.join(AUDIT_RULES)}")
    rules = effective_rules(rules, radar_text)
    q = f"【用户问题】\n{question.strip()}\n\n" if question and question.strip() else ""
    return (
        f"{AUDIT_RULES[rules]}\n\n{q}【原文】\n{original}\n\n"
        f"【往返参考稿（可借用直白说法，但含机翻错误，借用前对照原文）】\n{rtt_draft}\n\n"
        f"【雷达提示（原文里机器扫出的疑点，仅供参考）】\n{radar_text}\n\n只输出 JSON 数组："
    )


# ---------------------------------------------------------------------------
# 补丁解析
# ---------------------------------------------------------------------------


class PatchFormatError(ValueError):
    pass


def _salvage_objects(s: str) -> list[dict]:
    """从格式损坏的 JSON 数组里逐个解析出含 find/replace 的完整对象。

    从每个 “{” 起用标准解码器尝试解析一个完整对象；成功则跳过该对象，失败则前进一个字符。
    字符串内容由解码器负责转义处理，不会被当作结构字符改写。
    """
    dec = json.JSONDecoder()
    out: list[dict] = []
    i = s.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(s, i)
        except json.JSONDecodeError:
            i = s.find("{", i + 1)
            continue
        if isinstance(obj, dict) and "find" in obj and "replace" in obj:
            out.append(obj)
        i = s.find("{", end)
    return out


@dataclass
class Patch:
    find: str
    replace: str
    reason: str = ""
    type: str = ""


def parse_patches(raw) -> list[Patch]:
    """接受 list / {"patches": [...]} / JSON 字符串（容忍 ```json 围栏和前后废话）。"""
    if isinstance(raw, str):
        s = raw.strip()
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
        if not s:
            return []
        try:
            raw = json.loads(s)
        except json.JSONDecodeError:
            a, b = s.find("["), s.rfind("]")
            if a == -1 or b <= a:
                raise PatchFormatError("找不到 JSON 数组") from None
            try:
                raw = json.loads(s[a : b + 1])
            except json.JSONDecodeError as e:
                # 模型偶尔输出多余的 }、漏逗号或尾逗号：逐个抢救出完整的补丁对象，
                # 而不是用正则改写原文（正则会误伤 replace 字符串里本来就有的 “}},”）
                salvaged = _salvage_objects(s[a : b + 1])
                if not salvaged:
                    raise PatchFormatError(f"JSON 解析失败：{e}") from None
                raw = salvaged
    if isinstance(raw, dict):
        if "patches" in raw:
            raw = raw["patches"]
        elif "find" in raw:
            raw = [raw]  # 单条补丁没包数组，宽容处理
        else:
            raise PatchFormatError("补丁对象缺少 patches 或 find 字段")
    if not isinstance(raw, list):
        raise PatchFormatError("补丁必须是 JSON 数组")
    out: list[Patch] = []
    for item in raw:
        if not isinstance(item, dict):
            raise PatchFormatError(f"补丁项必须是对象：{item!r}")
        find, replace = item.get("find"), item.get("replace")
        if not isinstance(find, str) or not isinstance(replace, str):
            raise PatchFormatError(f"补丁项缺少字符串 find/replace：{item!r}")
        out.append(Patch(find, replace, str(item.get("reason", "")), str(item.get("type", "") or "")))
    return out


# ---------------------------------------------------------------------------
# 补丁合并
# ---------------------------------------------------------------------------


@dataclass
class _Span:
    u_start: int
    u_end: int
    m_start: int
    m_end: int
    pid: int


def _expand(masked: str, placeholders: list[str]) -> tuple[str, list[_Span]]:
    """解掩码，同时记录每个冻结片段在明文/掩码文本中的位置。"""
    out: list[str] = []
    spans: list[_Span] = []
    pos = 0
    upos = 0
    for m in PLACEHOLDER_RE.finditer(masked):
        chunk = masked[pos : m.start()]
        out.append(chunk)
        upos += len(chunk)
        val = placeholders[int(m.group(1))]
        spans.append(_Span(upos, upos + len(val), m.start(), m.end(), int(m.group(1))))
        out.append(val)
        upos += len(val)
        pos = m.end()
    out.append(masked[pos:])
    return "".join(out), spans


def _u2m(p: int, spans: list[_Span]) -> int:
    """明文位置 -> 掩码位置（p 不得落在冻结片段内部）。"""
    shift = 0
    for sp in spans:
        if sp.u_end <= p:
            shift += (sp.m_end - sp.m_start) - (sp.u_end - sp.u_start)
    return p + shift


@dataclass
class PatchResult:
    patch: Patch
    applied: bool
    reason: str = ""
    slot: int | None = None


def _find_all(hay: str, needle: str) -> list[int]:
    res, i = [], hay.find(needle)
    while i != -1:
        res.append(i)
        i = hay.find(needle, i + 1)
    return res


def _find_outside_placeholders(hay: str, needle: str) -> int:
    taken = [m.span() for m in PLACEHOLDER_RE.finditer(hay)]
    for k in _find_all(hay, needle):
        if all(k + len(needle) <= a or k >= b for a, b in taken):
            return k
    return -1


def apply_patches(
    doc: Document, slot_texts: list[str], patches: list[Patch], max_find: int = MAX_FIND_CHARS,
    rejected: dict[int, str] | None = None,
    problem_types: tuple[str, ...] | None = None,
) -> tuple[list[str], list[PatchResult]]:
    """把补丁逐条合并进掩码槽位文本。返回 (新槽位文本, 每条补丁的处理结果)。

    rejected：调用方预先判定拒收的补丁（下标 -> 原因）；problem_types：给定时，type 不在其中的补丁拒收。
    """
    texts = list(slot_texts)
    results: list[PatchResult] = []
    doc.dropped = set()  # 每次合并重新计算被译掉的软占位符
    for idx, p in enumerate(patches):
        if rejected and idx in rejected:
            results.append(PatchResult(p, False, rejected[idx]))
            continue
        if problem_types and p.type.strip("[]【】 ") not in problem_types:
            results.append(PatchResult(p, False, f"补丁没有写明修的是哪类问题（type 须为 {'、'.join(problem_types)} 之一）"))
            continue
        r = _apply_one(doc, texts, p, max_find)
        results.append(r)
    return texts, results


def _strip_structure_prefix(p: Patch) -> tuple[Patch | None, str]:
    """宿主模型常把行首的 Markdown 标记（## 、1. 、- [x] 、> ）一起摘进 find。

    这些标记属于文档骨架，不在可改写的正文槽位里。find 与 replace 带同样的标记时去掉后再匹配；
    replace 改动了标记本身（如改标题级别、改列表编号）则拒收，结构只能由原文决定。
    返回 (去掉标记后的补丁或 None, 拒收原因)。
    """
    m = _LINE_PREFIX_RE.match(p.find)
    prefix = m.group(1) if m else ""
    if not prefix.strip():
        return None, ""
    if not p.replace.startswith(prefix):
        return None, f"补丁改动了行首结构标记「{prefix.strip()}」"
    return Patch(p.find[len(prefix):], p.replace[len(prefix):], p.reason, p.type), ""


_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def _table_cells(row: str) -> list[str] | None:
    row = row.strip()
    if not (row.startswith("|") and row.endswith("|") and row.count("|") >= 3):
        return None
    return [c.strip() for c in _CELL_SPLIT_RE.split(row)[1:-1]]


def _apply_table_row(doc: Document, texts: list[str], p: Patch, max_find: int = MAX_FIND_CHARS) -> PatchResult | None:
    """宿主按整行改写 Markdown 表格时，拆成逐个单元格的补丁；任一格失败则整条拒收（不留半改状态）。"""
    fc, rc = _table_cells(p.find), _table_cells(p.replace)
    if fc is None or rc is None:
        return None
    if len(fc) != len(rc):
        return PatchResult(p, False, "补丁改变了表格列数")
    trial = list(texts)
    dropped_before = set(doc.dropped)
    for f, r in zip(fc, rc):
        if f == r:
            continue
        if not f:
            return PatchResult(p, False, "补丁向空单元格填写内容")
        sub = _apply_core(doc, trial, Patch(f, r, p.reason, p.type), max_find)
        if not sub.applied:
            doc.dropped = dropped_before  # 整行拒收：前面单元格译掉的软占位符一并撤销
            return PatchResult(p, False, f"表格单元格「{f[:20]}」：{sub.reason}")
    texts[:] = trial
    return PatchResult(p, True, "")


def _apply_one(doc: Document, texts: list[str], p: Patch, max_find: int = MAX_FIND_CHARS) -> PatchResult:
    r = _apply_core(doc, texts, p, max_find)
    if r.applied or not r.reason.startswith("find 在初稿中不存在"):
        return r
    t = _apply_table_row(doc, texts, p, max_find)
    if t is not None:
        return t
    if p.find != p.find.strip() and p.find.strip():
        # 宿主常把行尾空格一起摘进 find（原文行尾的空格不属于正文槽位）；去掉首尾空白再试一次
        r3 = _apply_core(doc, texts, Patch(p.find.strip(), p.replace.strip(), p.reason, p.type), max_find)
        if r3.applied:
            return PatchResult(p, True, "", r3.slot)
    stripped, why = _strip_structure_prefix(p)
    if why:
        return PatchResult(p, False, why)
    if stripped is None:
        return r
    r2 = _apply_core(doc, texts, stripped, max_find)
    return PatchResult(p, r2.applied, r2.reason, r2.slot)


def _apply_core(doc: Document, texts: list[str], p: Patch, max_find: int = MAX_FIND_CHARS) -> PatchResult:
    if not p.find:
        return PatchResult(p, False, "find 为空")
    if p.find == p.replace:
        return PatchResult(p, False, "find 与 replace 相同")
    if "\n" in p.find or "\n" in p.replace:
        return PatchResult(p, False, "补丁不得跨行")
    if len(p.find) > max_find:
        return PatchResult(p, False, f"find 超过 {max_find} 字，超出本规则允许的改写范围")
    if PLACEHOLDER_RE.search(p.replace) or PLACEHOLDER_RE.search(p.find):
        return PatchResult(p, False, "补丁中不得出现占位符 {{Pn}}")

    views = [_expand(t, doc.placeholders) for t in texts]
    hits = [(i, pos) for i, (u, _) in enumerate(views) for pos in _find_all(u, p.find)]
    if not hits:
        return PatchResult(p, False, "find 在初稿中不存在（可能跨越了结构边界或不是逐字摘录）")
    if len(hits) > 1:
        return PatchResult(p, False, f"find 在初稿中出现 {len(hits)} 次，有歧义")
    i, s = hits[0]
    e = s + len(p.find)
    _, spans = views[i]

    inner: list[_Span] = []
    for sp in spans:
        if sp.u_end <= s or sp.u_start >= e:
            continue
        if s <= sp.u_start and sp.u_end <= e:
            inner.append(sp)
        else:
            return PatchResult(p, False, f"补丁切断了受保护内容「{doc.placeholders[sp.pid]}」", i)

    # 把 replace 中的受保护原文重新掩码回原占位符；每个受保护片段必须恰好保留一次
    rep = p.replace
    drop: list[int] = []
    for sp in sorted(inner, key=lambda x: -len(doc.placeholders[x.pid])):
        val = doc.placeholders[sp.pid]
        k = _find_outside_placeholders(rep, val)
        if k == -1:
            if sp.pid in doc.soft and p.type.strip("[]【】 ") == "中英混杂":
                drop.append(sp.pid)  # 散装英文被换成中文：占位符留在原处、渲染为空，校验照常通过
                continue
            return PatchResult(p, False, f"补丁丢失了受保护内容「{val}」", i)
        rep = rep[:k] + PLACEHOLDER.format(sp.pid) + rep[k + len(val):]
    for sp in inner:
        val = doc.placeholders[sp.pid]
        if sp.pid not in drop and _find_outside_placeholders(rep, val) != -1:
            return PatchResult(p, False, f"补丁复制了受保护内容「{val}」", i)
    rep += "".join(PLACEHOLDER.format(pid) for pid in drop)

    ms, me = _u2m(s, spans), _u2m(e, spans)
    new = texts[i][:ms] + rep + texts[i][me:]
    if not check_placeholders(new, doc.slots[i].ids).ok:
        return PatchResult(p, False, "合并后占位符校验失败", i)
    texts[i] = new
    doc.dropped.update(drop)
    return PatchResult(p, True, "", i)


def results_to_dicts(results: list[PatchResult]) -> list[dict]:
    return [{**asdict(r.patch), "applied": r.applied, "reject_reason": r.reason or None} for r in results]

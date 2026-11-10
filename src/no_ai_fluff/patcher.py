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

AUDIT_INSTRUCTIONS_V1 = """你是中文技术文本审校员。【RTT 初稿】由【原文】经机器往返翻译得到，句式已变松弛。请对照原文全量审校初稿，只修下列问题：
1. 术语翻偏：专业术语被误译或直译（如“正定矩阵→积极确定矩阵”“逃逸分析→逃跑分析”），改回中文标准名；首次出现的硬核学科概念写成“中文标准名（English Term）”。
2. 逻辑失真：语气被弱化（“仅在…时”变“通常在…时”）、因果倒置、充要条件改变、推理步骤丢失、数字或量级与原文不符。
3. 隐喻与指代：空间动作隐喻改回直白因果；指代不明的“它/其”换成明确的名词。
4. 雷达提示里确实成立的问题（套话、散装英文、过长分句）可顺手修；误报不必理会。
输出规则：
- 只输出 JSON 数组：[{"find": "初稿原片段", "replace": "修改后片段", "reason": "原因"}]；初稿准确通顺就输出 []。
- find 必须逐字摘自【RTT 初稿】，在全文中唯一，不跨行，尽量短。
- 严禁重写全文或润色本来正确的句子；保持初稿松弛的短句，不要压缩回长句。
- 代码、路径、公式、URL、数字原样保留。"""


# v2（2026-11-08）：M2 双轴评测显示，v1 “只修错、不润色”让翻译腔与断句粘连原样保留，
# 补丁版易读度比原文低 0.31。v2 允许用局部补丁修明显的翻译腔，但仍禁止整句以上的重写。
AUDIT_INSTRUCTIONS_V2 = """你是中文审校员。【RTT 初稿】由【原文】经机器往返翻译得到，句式已变松弛。请对照原文全量审校初稿，修下列问题：
1. 术语翻偏：专业术语或固定说法被误译、直译或换了说法（如“正定矩阵→积极确定矩阵”“竞态条件→竞争状态”），改回原文用词；首次出现的硬核学科概念可写成“中文标准名（English Term）”。
2. 逻辑失真：语气被弱化（“仅在…时”变“通常在…时”）、因果倒置、条件改变、主客体颠倒、推理步骤丢失、数字或量级与原文不符、原文信息被遗漏或凭空添加。
3. 隐喻与指代：空间动作隐喻改回直白因果；指代不明的“它/其”换成明确的名词。
4. 明显的翻译腔与断句问题：生硬直译的搭配（如“享受更安全的授权流程”“对数据进行处理”）、欧化长定语、丢标点导致两句粘连。改成同事之间说话那样的完整句子：有主语、有动词，该用的“的、了、会、可以”不省。不要改成公文腔或名词堆叠（如“支持签名校验，保障回调安全”这种也算问题）。
5. 雷达提示里确实成立的问题（套话、散装英文、过长分句）可顺手修；误报不必理会。
输出规则：
- 只输出 JSON 数组：[{"find": "初稿原片段", "replace": "修改后片段", "type": "术语|逻辑|指代|翻译腔|其他", "reason": "原因"}]；初稿准确通顺就输出 []。
- find 必须逐字摘自【RTT 初稿】，在全文中唯一，不跨行，最长一句话。
- 只改有问题的局部；读起来自然、意思正确的句子不要动。保持初稿松弛的短句，不要合并成长句，也不要扩写。
- 代码、路径、公式、URL、数字原样保留。"""

# v3（2026-11-08）：M2/M3 的“最小改动”规则是为选型服务的；面向最终效果，宿主按 SKILL 十条原则
# 获得整句 / 整段的改写权限，也负责清理原文自带的 AI 腔。保真仍是第一优先级，受保护内容规则不变。
AUDIT_INSTRUCTIONS_V3 = """你是中文编辑。【RTT 初稿】由【原文】经机器往返翻译得到。请交付一份“说人话”的定稿：事实与原文完全一致，读起来像有经验的人当面讲给同事听。按优先级做两件事：

一、保真（必须先做）。对照原文修正初稿里的事实偏差，只改出错的那个词或短语，保留初稿已经拆松的句式：
- 术语译错或换了说法，只把这个术语改回原文用词；硬核学科概念首次出现可写成“中文标准名（English Term）”；
- 语气与条件强弱不能变：“尽量、通常、可能、建议、很”不能变成硬性要求，反之亦然；“以内、之间、仅当、至少”等边界不能变；
- 因果方向、主体与客体、数字与量级必须与原文一致；原文信息不得遗漏，也不得凭空添加。
- 严禁把整句改回原文的写法：往返翻译的目的就是拆掉原文的压缩句式和套话，整句复制原文会抵消这一步（这类补丁会被代码拒收）。

二、说人话（放宽改写）。读着累的地方可以改写到一两句话的范围，包括原文本身就有的 AI 腔。依据：
1. 句子主谓完整，不用名词短语堆砌代替句子；
2. 因果直说，不用“打通、闭环、下沉、赋能”这类空间动作充当逻辑连词；比喻要能对应到实物实量，否则删掉；
3. 不用读者需要回溯才能看懂的自造代号和压缩短语；
4. 非必要不夹英文，只保留没有通行中文译法的技术术语；
5. 去掉翻译腔：长定语拆开，被动句改主动，“对……进行……”“享受……”这类直译搭配改成自然说法；
6. 删掉空洞大词、口号式排比和没有度量的夸张修饰（如“沉淀、抓手、底层逻辑、极其、全方位”），能给具体事实或数量就给；
7. 推理不跳步，必要的“因为、所以、这样”不省；
8. 不扩写、不加背景、不升华，不加原文没有的信息；
9. 指代不清的“它、其、该”换成明确的名词；
10. 原文作者明确使用的说法、比喻和术语照用。
好读不等于字数少：为了说清楚多用几个字可以，但不要把句子压成名词短语。

编辑范围与输出格式：
- 只输出 JSON 数组：[{"find": "初稿原片段", "replace": "修改后片段", "type": "保真|说人话", "reason": "原因"}]；初稿已经准确又好读就输出 []。
- 仍然是局部补丁：find 逐字摘自【RTT 初稿】，在全文中唯一，不跨行，范围是一个词、一个短语或一两句话（不超过 300 字），不要整段重写。
- 不改文档结构：标题层级、列表、表格、代码块保持原样，但其中的文字可以改。
- 代码、路径、公式、URL、数字和反引号里的内容必须原样保留，每个只出现一次；可以调整它们在句中的位置。"""

# v4（2026-11-08）：v3 冒烟里宿主大量以“原文是 xx，所以改成 xx”为由把句子抄回原文。
# 原因是“保真 = 与原文一致”的定义本身：原文就在手边，照抄是最省事、最不会错的做法。
# v4 把保真改为以读者为中心的判据——只有读者会因此产生错误认识时才算保真问题，
# 保真补丁必须写明 misread（读者会误以为什么），说不出就不准改；措辞、语体、句式差异一律不算。
AUDIT_INSTRUCTIONS_V4 = """你是中文编辑。【RTT 初稿】由【原文】经机器往返翻译得到，句子已经被拆松。请交付一份“说人话”的定稿：读者读完得到的认识与读原文完全相同，读起来像有经验的人当面讲给同事听。

一、保真：只防“读者会被误导”。
判断标准只有一个：读者读了初稿，会不会得出与原文不同的错误认识，或者据此做错事？会，才算保真问题；不会，就不是。
以下情况会误导读者，必须修：
- 数字、单位、量级变了；
- 语气强弱变了：“尽量、通常、可能、建议、很”被说成硬性要求，或者反过来；
- 条件边界变了：“以内、之间、仅当、至少、除非”等；
- 因果方向、主体与客体颠倒；
- 专业术语变成了另一个东西（例如 compaction 被说成“集约化战略”），或工程内部的专有名词、代号被改得认不出来；
- 原文的关键信息丢了，或者凭空多出原文没有的说法。
以下情况**不是**保真问题，不准以“保真”为由修改：
- 用词、语体、句子结构和原文不同；把书面词换成了口语词；
- 句子被拆短、语序调整、多用了几个字把话说清楚；
- 原文里的套话、口号、空洞修饰被去掉了（这本来就是我们要的）。
修保真问题时，只改出错的那个词或短语，保留初稿拆松的句式。严禁把整句改回原文的写法（这类补丁会被代码拒收）。
如果给了【用户问题】，用它判断哪些内容是回答问题必不可少的、有没有段落跑题；但事实细节仍以原文为准。

二、说人话（放宽改写）。读着累的地方可以改写到一两句话的范围，包括原文本身就有的 AI 腔。依据：
1. 句子主谓完整，不用名词短语堆砌代替句子；
2. 因果直说，不用“打通、闭环、下沉、赋能”这类空间动作充当逻辑连词；比喻要能对应到实物实量，否则删掉；
3. 不用读者需要回溯才能看懂的自造代号和压缩短语；
4. 非必要不夹英文，只保留没有通行中文译法的技术术语；
5. 去掉翻译腔：长定语拆开，被动句改主动，“对……进行……”“享受……”这类直译搭配改成自然说法；
6. 删掉空洞大词、口号式排比和没有度量的夸张修饰，能给具体事实或数量就给；
7. 推理不跳步，必要的“因为、所以、这样”不省；
8. 不扩写、不加背景、不升华，不加原文没有的信息；
9. 指代不清的“它、其、该”换成明确的名词；
10. 原文作者明确使用的说法、比喻和术语照用。
好读不等于字数少：为了说清楚多用几个字可以，但不要把句子压成名词短语。

输出格式：
- 只输出 JSON 数组；初稿已经准确又好读就输出 []。每条补丁：
  {"find": "初稿原片段", "replace": "修改后片段", "type": "保真|说人话", "misread": "读者读初稿会误以为什么（type 为保真时必填，写具体的错误认识）", "reason": "原因"}
- misread 要写读者的错误认识，例如“读者会以为通勤必须在 45~60 分钟之间”；不能写“与原文不一致”“原文是 xx”这类话——写不出具体误解，就说明不是保真问题。
- 仍然是局部补丁：find 逐字摘自【RTT 初稿】，在全文中唯一，不跨行，范围是一个词、一个短语或一两句话（不超过 300 字），不要整段重写。
- 不改文档结构：标题层级、列表、表格、代码块保持原样，但其中的文字可以改。
- 代码、路径、公式、URL、数字和反引号里的内容必须原样保留，每个只出现一次；可以调整它们在句中的位置。"""

# v5（2026-11-08）：拔掉 v4 的三个“回抄诱导源”——删除“原文说法照用”、初稿在前底稿在后、保真补丁 ≤20 字；
# 配合 reversion.py 的词对微筛拦截短词行话倒退。
AUDIT_INSTRUCTIONS_V5 = """你是中文编辑。【RTT 初稿】由【AI 原始底稿】经机器往返翻译得到，句子已经被拆松，很多行话和套话也随之被洗成了直白说法。请以初稿为底本交付一份“说人话”的定稿：读者读完得到的认识与读底稿完全相同，读起来像有经验的人当面讲给同事听。
【AI 原始底稿】只用来核对事实，它本身就带着要去掉的行话和套话，不要抄回它的用词。

一、保真：只防“读者会被误导”。
判断标准只有一个：读者读了初稿，会不会得出与底稿不同的错误认识，或者据此做错事？会，才算保真问题；不会，就不是。
以下情况会误导读者，必须修：
- 数字、单位、量级变了；
- 语气强弱变了：“尽量、通常、可能、建议、很”被说成硬性要求，或者反过来；
- 条件边界变了：“以内、之间、仅当、至少、除非”等；
- 因果方向、主体与客体颠倒；
- 专业术语变成了另一个东西（例如 compaction 被说成“集约化战略”），或工程内部的专有名词、代号被改得认不出来；
- 底稿的关键信息丢了，或者凭空多出底稿没有的说法。
以下情况**不是**保真问题，不准以“保真”为由修改：
- 用词、语体、句子结构和底稿不同；把书面词换成了口语词；
- 句子被拆短、语序调整、多用了几个字把话说清楚；
- 底稿里的套话、口号、空洞修饰被去掉了（这本来就是我们要的）。
外延判据：只有初稿说法和底稿说法**指的不是同一个东西**时（例如“自动吸尘”与“自动集尘”、“侧刷”与“边刷”），才准把这个词改回底稿的词；
只要指的是同一件事、初稿已经说直白了（例如“人员分析”与“人力盘点”、“降低成本、提高效率”与“降本增效”），无论以保真还是说人话为由，都不准改回底稿的行话或简称。
修保真问题时，find 只框出错的那个最小词组（不超过 20 字），不要把整句连带换掉；超过 20 字的保真补丁会被代码拒收。严禁把整句改回底稿的写法。
如果给了【用户问题】，用它判断哪些内容是回答问题必不可少的、有没有段落跑题；但事实细节仍以底稿为准。

二、说人话（放宽改写）。读着累的地方可以改写到一两句话的范围，包括底稿遗留下来的 AI 腔。依据：
1. 句子主谓完整，不用名词短语堆砌代替句子；
2. 因果直说，不用“打通、闭环、下沉、赋能”这类空间动作充当逻辑连词；比喻要能对应到实物实量，否则删掉；
3. 不用读者需要回溯才能看懂的自造代号和压缩短语；
4. 非必要不夹英文，只保留没有通行中文译法的技术术语；
5. 去掉翻译腔：长定语拆开，被动句改主动，“对……进行……”“享受……”这类直译搭配改成自然说法；
6. 删掉空洞大词、口号式排比和没有度量的夸张修饰，能给具体事实或数量就给；
7. 推理不跳步，必要的“因为、所以、这样”不省；
8. 不扩写、不加背景、不升华，不加底稿没有的信息；
9. 指代不清的“它、其、该”换成明确的名词；
10. 改写时用你自己的直白说法，不要从【AI 原始底稿】里搬词；底稿里的专业术语只在初稿把它译错时才按外延判据改回。
好读不等于字数少：为了说清楚多用几个字可以，但不要把句子压成名词短语。

输出格式：
- 只输出 JSON 数组；初稿已经准确又好读就输出 []。每条补丁：
  {"find": "初稿原片段", "replace": "修改后片段", "type": "保真|说人话", "misread": "读者读初稿会误以为什么（type 为保真时必填，写具体的错误认识）", "reason": "原因"}
- misread 要写读者的错误认识，例如“读者会以为通勤必须在 45~60 分钟之间”；不能写“与原文不一致”“原文是 xx”这类话——写不出具体误解，就说明不是保真问题。
- 改回底稿用词的短词补丁会再被单独复核，凡是把直白说法换回行话的都会被撤销。
- 仍然是局部补丁：find 逐字摘自【RTT 初稿】，在全文中唯一，不跨行，范围是一个词、一个短语或一两句话（不超过 300 字），不要整段重写。
- 不改文档结构：标题层级、列表、表格、代码块保持原样，但其中的文字可以改。
- 代码、路径、公式、URL、数字和反引号里的内容必须原样保留，每个只出现一次；可以调整它们在句中的位置。"""

# v6（M4 盲测之后）：底本换回原文。v5 以往返初稿为底本，改动有八成落在本来没有问题的句子上，
# 往返带进的错词（运维→运营）和中英混杂句也跟着进了定稿；人工盲测里 Humanizer-zh 以 6/10 胜出。
# v6 只修原文里确实读着费劲的地方，往返稿降为“直白说法参考”；不设改写幅度预算。
PROBLEM_TYPES_V6 = ("残句堆砌", "隐喻黑话", "自造代号", "中英混杂", "翻译腔", "空洞大词", "推理跳步", "冗余", "指代不清")

AUDIT_INSTRUCTIONS_V6 = """你是中文编辑。请找出【原文】里晦涩、句式不符合中文阅读习惯、读着费劲的地方，用补丁把它们改成有经验的人当面讲给同事听的说法。
没有问题的句子一个字都不要动；原文整体已经自然好读，就输出 []。需要大改的段落可以大改，改多少由问题决定，不设上限。

【往返参考稿】是原文经机器翻译成外语再译回中文的结果。它的句子往往更松、更直白，可以借用它的说法；
但它也会带进机翻错误：术语译错（例如“运维成本”被译成“运营成本”）、半中半英的句子、生硬的翻译腔。
借用前逐词对照原文：术语、专有名词、数字和语气以原文为准，参考稿里的错误不准带进补丁。参考稿和原文一样好时，以原文为准，不要为改而改。

找问题的依据（每条补丁的 type 必须是下面方括号里的名称之一，写不出属于哪一类，就说明不该改）：
1. [残句堆砌] 句子主谓不全，或用名词短语堆砌代替句子；
2. [隐喻黑话] 用“打通、闭环、下沉、赋能、抓手”这类空间动作或黑话充当逻辑，比喻对应不到实物实量；
3. [自造代号] 读者需要回溯上下文才能猜出意思的自造代号或压缩短语；
4. [中英混杂] 有通行中文说法的地方夹英文，或半中半英的句子；没有通行中文译法的技术术语、产品名、代码相关内容保留英文；
5. [翻译腔] 长定语、被动堆叠、“对……进行……”“享受……”这类直译搭配；
6. [空洞大词] 空洞大词、口号式排比、没有度量的夸张修饰；能给具体事实就给，给不出就删；
7. [推理跳步] 推理跳步，读者要自己补“因为、所以、这样”才能跟上；
8. [冗余] 重复说同一件事、只预告下文的铺垫、没有信息的收尾；
9. [指代不清] “它、其、该”等指代不清，主体客体容易弄混。

不变的底线：
- 读者读完定稿得到的认识必须和读原文完全相同：数字、单位、条件边界、语气强弱、因果方向、主客体、专业术语都不能变，也不能增删信息；
- 如果给了【用户问题】，问题里出现的术语、比喻和简称照用；
- 不改文档结构：标题层级、列表、表格、代码块保持原样，其中的文字可以改；
- 代码、路径、公式、URL、数字和反引号里的内容原样保留，每个只出现一次，可以调整它们在句中的位置。

输出格式：只输出 JSON 数组，每条补丁：
{"find": "原文片段", "replace": "修改后片段", "type": "上面九类之一", "reason": "这里读着费劲的具体原因"}
- find 逐字摘自【原文】，在全文中唯一，不跨行；可以是一个词、一句话或同一行内的整段（不超过 400 字）；
- 同一处问题只出一条补丁，补丁之间不要重叠。"""

# v7（优化闭环第 1 轮）：v6 在 Train 上偏保守——15 篇里 5 篇零补丁（含雷达报 16 处散装英文的一篇），
# 改写幅度中位数约为 Humanizer-zh 的五分之一；人工盲测备注集中在“信息密度太高要停下来拆”“自造缩略说法”“先列项目后说动作”。
# 改动只在“改多少”的姿态和问题清单：逐段检查、逐条核对雷达；残句堆砌 / 翻译腔 / 冗余三类补充具体表现。保真底线不变。
AUDIT_INSTRUCTIONS_V7 = """你是中文编辑。请逐段通读【原文】，把晦涩、句式不符合中文阅读习惯、读着费劲的地方，用补丁改成有经验的人当面讲给同事听的说法。
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
9. [指代不清] “它、其、该”等指代不清，主体客体容易弄混。

不变的底线：
- 读者读完定稿得到的认识必须和读原文完全相同：数字、单位、条件边界、语气强弱、因果方向、主客体、专业术语都不能变，也不能增删信息；
- 如果给了【用户问题】，问题里出现的术语、比喻和简称照用；
- 不改文档结构：标题层级、列表、表格、代码块保持原样，其中的文字可以改；
- 代码、路径、公式、URL、数字和反引号里的内容原样保留，每个只出现一次，可以调整它们在句中的位置。

输出格式：只输出 JSON 数组，每条补丁：
{"find": "原文片段", "replace": "修改后片段", "type": "上面九类之一", "reason": "这里读着费劲的具体原因"}
- find 逐字摘自【原文】，在全文中唯一，不跨行；可以是一个词、一句话或同一行内的整段（不超过 400 字）；
- 同一处问题只出一条补丁，补丁之间不要重叠。"""

# v8（优化闭环第 3 轮）：Train 上 Humanizer-zh 赢在日常场景的，多是全篇的小幅“解压”——补回省掉的“的、了、在”和动词宾语
# （“固定8:30起”→“固定8:30起床”“下午2点后不咖啡因”→“不碰咖啡因”），把夹在中文里当词用的“/、+、—”换成“或、和、到”，
# 把“目标：……”这类标签式短语说成整句。v7 九类里没有对应项，宿主不改。v8 = v7 + 第 10 类 [省略压缩]，其余不变。
PROBLEM_TYPES_V8 = PROBLEM_TYPES_V6 + ("省略压缩",)
AUDIT_INSTRUCTIONS_V8 = AUDIT_INSTRUCTIONS_V7.replace(
    "9. [指代不清] “它、其、该”等指代不清，主体客体容易弄混。",
    "9. [指代不清] “它、其、该”等指代不清，主体客体容易弄混；\n"
    "10. [省略压缩] 像发电报一样省字：省掉了“的、了、在、把”或动词的宾语，用“/、+、—、→”代替“或、和、到、然后”，"
    "用“目标：……”这类标签代替句子。补回省掉的字，说成完整的话。",
).replace("\"type\": \"上面九类之一\"", "\"type\": \"上面十类之一\"")
assert AUDIT_INSTRUCTIONS_V8 != AUDIT_INSTRUCTIONS_V7 and "上面十类之一" in AUDIT_INSTRUCTIONS_V8

AUDIT_RULES = {"v1": AUDIT_INSTRUCTIONS_V1, "v2": AUDIT_INSTRUCTIONS_V2, "v3": AUDIT_INSTRUCTIONS_V3,
               "v4": AUDIT_INSTRUCTIONS_V4, "v5": AUDIT_INSTRUCTIONS_V5, "v6": AUDIT_INSTRUCTIONS_V6,
               "v7": AUDIT_INSTRUCTIONS_V7, "v8": AUDIT_INSTRUCTIONS_V8}
DEFAULT_AUDIT_RULES = "v8"  # 优化闭环第 3 轮 Keep（2026-11-10）
# v6 起：补丁打在原文上（往返稿只作参考）
ORIGINAL_BASE_RULES = {"v6", "v7", "v8"}
# v6 起：每条补丁必须写明修的是哪一类问题
PROBLEM_TYPES = {"v6": PROBLEM_TYPES_V6, "v7": PROBLEM_TYPES_V6, "v8": PROBLEM_TYPES_V8}
AUDIT_INSTRUCTIONS = AUDIT_RULES[DEFAULT_AUDIT_RULES]  # 向后兼容
# 单条补丁 find 的长度上限，按规则版本区分：v1/v2 只修局部，超过 200 字视为整段重写而拒收；
# v3 允许整段改写，上限放宽到一个段落的量级（仍不得跨行，结构由原文决定）
MAX_FIND_BY_RULES = {"v1": 200, "v2": 200, "v3": 300, "v4": 300, "v5": 300, "v6": 400, "v7": 400, "v8": 400}
# v5：保真补丁只准框选出错的最小词组，避免“真修复 + 整句回抄”连坐
FIDELITY_MAX_FIND = {"v5": 20}
# v5：启用词对微筛（见 reversion.py）
TERM_REVIEW_RULES = {"v5"}
# v3 冒烟发现：宿主以“保真”为名把整句复制回原文，往返解压被完全抵消（三篇与原文差异降到 0%）。
# replace 中与原文逐字相同的连续片段达到这个长度，就视为“改回原文”并拒收；短术语不受影响。
REVERT_MIN_CHARS = {"v3": 25, "v4": 25, "v5": 25}
# v4：保真补丁必须写出读者的具体误解；空着或只是“与原文不一致 / 原文是…”之类的套话，一律拒收
REQUIRE_MISREAD = {"v4", "v5"}
_TAUTOLOGY_RE = re.compile(r"^(?:与|和|跟)?原文(?:不一致|不同|有出入|不符|有差异)|^原文(?:是|为|写的是|说的是)|^(?:与|和)原文表述")
MAX_FIND_CHARS = MAX_FIND_BY_RULES["v2"]


def build_audit_prompt(
    original: str, rtt_draft: str, radar_text: str, rules: str = DEFAULT_AUDIT_RULES, question: str | None = None
) -> str:
    if rules not in AUDIT_RULES:
        raise ValueError(f"未知审校规则 {rules}，可选：{', '.join(AUDIT_RULES)}")
    q = f"【用户问题】\n{question.strip()}\n\n" if question and question.strip() and rules in ("v4", "v5", "v6", "v7", "v8") else ""
    if rules in ORIGINAL_BASE_RULES:
        return (
            f"{AUDIT_RULES[rules]}\n\n{q}【原文】\n{original}\n\n"
            f"【往返参考稿（可借用直白说法，但含机翻错误，借用前对照原文）】\n{rtt_draft}\n\n"
            f"【雷达提示（原文里机器扫出的疑点，仅供参考）】\n{radar_text}\n\n只输出 JSON 数组："
        )
    if rules == "v5":
        # 初稿在前、底稿在后：先读满篇公文腔会把注意力锚定在原文词汇上
        return (
            f"{AUDIT_RULES[rules]}\n\n{q}【RTT 初稿】\n{rtt_draft}\n\n"
            f"【AI 原始底稿（含行话套话，仅供核对事实，严禁抄回其用词）】\n{original}\n\n"
            f"【雷达提示】\n{radar_text}\n\n只输出 JSON 数组："
        )
    return (
        f"{AUDIT_RULES[rules]}\n\n{q}【原文】\n{original}\n\n【RTT 初稿】\n{rtt_draft}\n\n"
        f"【雷达提示】\n{radar_text}\n\n只输出 JSON 数组："
    )


def misread_problem(p: "Patch") -> str:
    """v4：保真补丁的 misread 检查。返回拒收原因，合格返回空串。"""
    if not p.type.startswith("保真"):
        return ""
    m = p.misread.strip()
    if len(m) < 6:
        return "保真补丁没有写出读者会误以为什么（misread），不是保真问题就不应修改"
    if _TAUTOLOGY_RE.search(m):
        return "保真补丁的 misread 只是“与原文不一致”之类的说法，没有说明读者的具体误解"
    return ""


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
    misread: str = ""


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
        out.append(Patch(find, replace, str(item.get("reason", "")), str(item.get("type", "") or ""),
                         str(item.get("misread", "") or "")))
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


_NORM_WS = re.compile(r"\s+")


def _longest_common_run(a: str, b: str) -> int:
    """a 与 b 的最长公共连续子串长度（忽略空白）。"""
    from difflib import SequenceMatcher

    a, b = _NORM_WS.sub("", a), _NORM_WS.sub("", b)
    if not a or not b:
        return 0
    m = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return m.size


def apply_patches(
    doc: Document, slot_texts: list[str], patches: list[Patch], max_find: int = MAX_FIND_CHARS,
    revert_min: int | None = None,
    require_misread: bool = False,
    fidelity_max_find: int | None = None,
    rejected: dict[int, str] | None = None,
    problem_types: tuple[str, ...] | None = None,
) -> tuple[list[str], list[PatchResult]]:
    """把补丁逐条合并进掩码槽位文本。返回 (新槽位文本, 每条补丁的处理结果)。

    revert_min：给定时，replace 与原文的最长逐字相同片段达到该长度即视为“改回原文”并拒收
    （以初稿里本来就有的同样片段为准，不重复计）。
    """
    texts = list(slot_texts)
    results: list[PatchResult] = []
    doc.dropped = set()  # 每次合并重新计算被译掉的软占位符
    original = doc.render([s.masked for s in doc.slots]) if revert_min else ""
    for idx, p in enumerate(patches):
        if rejected and idx in rejected:
            results.append(PatchResult(p, False, rejected[idx]))
            continue
        if problem_types and p.type.strip("[]【】 ") not in problem_types:
            results.append(PatchResult(p, False, f"补丁没有写明修的是哪类问题（type 须为 {'、'.join(problem_types)} 之一）"))
            continue
        if fidelity_max_find and p.type.startswith("保真") and len(p.find) > fidelity_max_find:
            results.append(PatchResult(p, False, f"保真补丁只能框选出错的最小词组（不超过 {fidelity_max_find} 字）"))
            continue
        if require_misread:
            why = misread_problem(p)
            if why:
                results.append(PatchResult(p, False, why))
                continue
        if revert_min:
            run = _longest_common_run(p.replace, original)
            if run >= revert_min and run > _longest_common_run(p.find, original):
                results.append(PatchResult(p, False, f"补丁把 {run} 字改回了原文写法，抵消了往返解压"))
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
    return Patch(p.find[len(prefix):], p.replace[len(prefix):], p.reason, p.type, p.misread), ""


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
        sub = _apply_core(doc, trial, Patch(f, r, p.reason, p.type, p.misread), max_find)
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
        r3 = _apply_core(doc, texts, Patch(p.find.strip(), p.replace.strip(), p.reason, p.type, p.misread), max_find)
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

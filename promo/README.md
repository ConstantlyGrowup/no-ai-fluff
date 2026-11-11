# no-ai-fluff 发布宣传片

纯矢量 SVG 时间轴动画，1920×1080，163 秒（9 秒冷开场 + 154 秒正片），中文版和英文版各一部。中文版 6 章（1. AI的“方言” / 2. 方言现象的猜想 / 3. 中文场景的探索 / 4. 实际对比 / 5. 和主流热门SKILL的对比 / 6. 结尾）。视觉是暗房电影感（参照 `docs/video-skills-ref/opus-dark-ref-*.jpg`）：深曜石黑底加径向微光、象牙白文字、暖珊瑚红批注、翠绿补丁、琥珀锁定；片头三行设问后第一次亮出主标题与金句，第 6 章结尾第二次点题。音效由页面里的 `buildAudio` 用 WebAudio 合成，没有配乐（播放器有声音开关，快捷键 M），导出 MP4 时一起混入。画面是 `seek(t)` 的纯函数：每章有相机层，动效用闭式阻尼弹簧，镜头之间模糊溶解，规范参照 `docs/video-skills-ref/`。片中不出现安装命令、密钥或网址。画面不画外框、不挂章节眉标、不画进度线；字幕不画底框，只用文字暗影，每句最多一个核心词跳色；场景内容整体放大 1.07 倍铺满画面。分镜与口径见 [`docs/launch-narrative-pack.md`](../docs/launch-narrative-pack.md) 第 4 节。

## 播放

```bash
python -m http.server 8765 --directory promo
```

浏览器打开 `http://localhost:8765/`：

- 空格播放 / 暂停，← → 前后跳 5 秒，数字键 0 回到冷开场、1–6 跳到对应幕；
- 进度条可拖动，下方按钮按幕跳转；
- `?autoplay=1` 自动播放，`?clean=1` 隐藏控制条（全屏录屏用）；
- `?t=<毫秒>` 定格到某一帧并隐藏控制条，例如 `?t=6800` 是冷开场第一次亮出标题的画面。

## 英文版（`promo/en/`）

浏览器打开 `http://localhost:8765/en/`，操作与中文版相同。英文版用同一套引擎、配色和自检，6 章为 1. AI's dialect / 2. The hypothesis / 3. Building it for English / 4. Real examples / 5. The judge / 6. Wrap-up。数据由 `promo/build_en.py` 从英文评测结果生成 `promo/en/data.js`：

```bash
python promo/build_en.py
```

| 画面 | 结果文件 |
|---|---|
| 第 1 章状态更新案例 | `results/en-r2/` 的 en-tune-probe-showcase-001（调参集里人工构造的黑话段落，用 en2 跑出） |
| 第 3 章三个翻译模型、回译走样 | `results/en_probe/rtt_report.json`、`rtt.jsonl` |
| 第 3 章 en1 → en2 | `results/en-r1/metrics.json`、`results/en-r2/metrics.json` |
| 第 3 章测试集构成 | `benchmarks/corpus/en_test.jsonl`、`benchmarks/splits/en_test.lock.json` |
| 第 4 章三个案例与六组词对 | `results/en-test/rows.jsonl`、`results/en-r2/rows.jsonl`，`build_en.py` 逐条核对补丁确实生效 |
| 第 5、6 章 JEV 与客观指标 | `results/en-test/jev_summary.json`、`metrics.json` |

英文版没有对比任何第三方英文工具（只和原文比），第 5 章与第 6 章的说明文字也写明了 JEV 的英文问题集未经英文人工校准。

## 数据从哪来

画面里的数字和文字由 `promo/build.py` 从 `benchmarks/results/` 读出，写入 `promo/data.js`，不手抄：

```bash
python promo/build.py
```

| 画面 | 结果文件 |
|---|---|
| 第 1 章网关案例 | `results/showcase/` 的 showcase-004（`benchmarks/showcase.py` 用锁定配置 v8-locked 跑出） |
| 第 3 章语料划分 | `benchmarks/splits/*.json` |
| 第 3 章桥接语、漂移、推理强度、r0–r6 | `promo/build.py` 里的 `DOC_FACTS`，逐条注明出自 `docs/rtt-model-comparison.md` / `docs/evaluation-report.md` 哪一节 |
| 第 4 章四个案例 | `results/holdout/patch.jsonl`、`results/r3-dev/patch.jsonl`，逐字核对 |
| 第 5 章盲测、JEV、存活率 | `results/holdout-blind/blind_votes.json`、`results/holdout/jev_summary.json`、`summary.json` |
| 第 5 章 17 → 15 → 0、16 → 14 → 2 | `build.py` 的 `jargon_stats()` 按原文 / v8 / Humanizer-zh 逐字计数 |

## 排版自检

页面内置两个自检：

- `__audit(stepMs)`：按步长扫过整条时间轴，检查可见文字是否互相重叠或离画面边缘不足 16px（擦除线两侧按裁剪框计算，飞入途中的卡片不计出框）。控制台运行 `__audit(100)`，返回空数组表示通过。
- `__fontAudit()`：列出字号低于 32px 的文字；字幕固定 56px。返回空数组表示通过。

## 导出 MP4

`capture.py` 用 Playwright 逐帧调用 `window.seekMs(ms)` 截图，离线渲染音效声轨，再用 ffmpeg 合成 60fps 带音效的 MP4（加 `--mute` 只要画面）。它需要额外安装 `playwright` 和 `ffmpeg`：

```bash
python promo/capture.py --url http://localhost:8765/ --out promo/no-ai-fluff.mp4
python promo/capture.py --url http://localhost:8765/en/ --out promo/no-ai-fluff-en.mp4
```

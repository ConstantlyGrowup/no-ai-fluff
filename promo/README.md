# no-ai-fluff 发布宣传片

纯矢量 SVG 时间轴动画，1920×1080，154 秒，6 章（1. AI的“方言” / 2. 方言现象的猜想 / 3. 中文场景的探索 / 4. 实际对比 / 5. 和主流热门SKILL的对比 / 6. 结尾）。视觉是手稿审校美学：暖纸底、碳墨字、朱砂红批注、翠绿补丁、古铜锁定。音效由页面里的 `buildAudio` 用 WebAudio 合成，没有配乐（播放器有声音开关，快捷键 M），导出 MP4 时一起混入。画面是 `seek(t)` 的纯函数：每章有相机层，动效用闭式阻尼弹簧，镜头之间模糊溶解，规范参照 `docs/video-skills-ref/`。片中不出现安装命令、密钥或网址。分镜与口径见 [`docs/launch-narrative-pack.md`](../docs/launch-narrative-pack.md) 第 4 节。

## 播放

```bash
python -m http.server 8765 --directory promo
```

浏览器打开 `http://localhost:8765/`：

- 空格播放 / 暂停，← → 前后跳 5 秒，数字键 1–6 跳到对应幕；
- 进度条可拖动，下方按钮按幕跳转；
- `?autoplay=1` 自动播放，`?clean=1` 隐藏控制条（全屏录屏用）；
- `?t=<毫秒>` 定格到某一帧并隐藏控制条，例如 `?t=72000` 是幕 4 的补丁定稿画面。

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

- `__audit(stepMs)`：按步长扫过整条时间轴，检查可见文字是否互相重叠或超出外框（擦除线两侧按裁剪框计算，飞入途中的卡片不计出框）。控制台运行 `__audit(100)`，返回空数组表示通过。
- `__fontAudit()`：列出字号低于 32px 的文字；字幕固定 56px。返回空数组表示通过。

## 导出 MP4

`capture.py` 用 Playwright 逐帧调用 `window.seekMs(ms)` 截图，离线渲染音效声轨，再用 ffmpeg 合成 60fps 带音效的 MP4（加 `--mute` 只要画面）。它需要额外安装 `playwright` 和 `ffmpeg`：

```bash
python promo/capture.py --url http://localhost:8765/ --out promo/no-ai-fluff.mp4
```

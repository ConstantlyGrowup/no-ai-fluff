"""逐帧录制宣传片为 MP4（60fps，1920×1080）。

依赖（本仓库其余部分不需要）：
    pip install playwright && playwright install chromium
    以及 PATH 里的 ffmpeg

用法（先在仓库根目录起一个静态服务）：
    python -m http.server 8765 --directory promo
    python promo/capture.py --url http://localhost:8765/ --out promo/no-ai-fluff.mp4

原理：页面支持 ?t=<毫秒> 定格；这里只加载一次页面，再逐帧调用 window.seekMs(ms) 截图；
音效由页面里的 window.buildAudio 离线渲染（与画面同一时间轴），最后用 ffmpeg 合成。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8765/")
    ap.add_argument("--out", default="promo/no-ai-fluff.mp4")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--start", type=float, default=0.0, help="起始秒")
    ap.add_argument("--end", type=float, default=None, help="结束秒（默认整片）")
    ap.add_argument("--mute", action="store_true", help="不混入音效")
    args = ap.parse_args()
    if not shutil.which("ffmpeg"):
        raise SystemExit("找不到 ffmpeg，请先安装并加入 PATH")
    from playwright.sync_api import sync_playwright

    frames = Path(tempfile.mkdtemp(prefix="promo_frames_"))
    pcm = frames / "audio.pcm"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(f"{args.url}?t={int(args.start * 1000)}")
        page.wait_for_function("window.__promoReady === true", timeout=20000)
        if args.end is None:
            args.end = page.evaluate("window.VIDEO.duration")
        n = int(round((args.end - args.start) * args.fps))
        if not args.mute:   # 页面里的 buildAudio 在离线音频上下文里渲染整条声轨，转成 16 位 PCM 带回来
            b64 = page.evaluate("""async () => {
                const sr = 48000, ctx = new OfflineAudioContext(2, Math.ceil(sr * window.VIDEO.duration), sr);
                await window.buildAudio(ctx);
                const b = await ctx.startRendering(), L = b.getChannelData(0), R = b.getChannelData(1);
                const i16 = new Int16Array(L.length * 2);
                for (let i = 0; i < L.length; i++) { i16[2 * i] = Math.max(-1, Math.min(1, L[i])) * 32767; i16[2 * i + 1] = Math.max(-1, Math.min(1, R[i])) * 32767; }
                const u8 = new Uint8Array(i16.buffer); let s = "";
                for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
                return btoa(s);
            }""")
            import base64
            pcm.write_bytes(base64.b64decode(b64))
        for i in range(n + 1):
            ms = args.start * 1000 + i * 1000 / args.fps
            page.evaluate(f"window.seekMs({ms})")
            page.screenshot(path=str(frames / f"{i:06d}.png"))
            if i % args.fps == 0:
                print(f"  {ms / 1000:6.1f}s", flush=True)
        browser.close()
    cmd = ["ffmpeg", "-y", "-framerate", str(args.fps), "-i", str(frames / "%06d.png")]
    if pcm.exists():
        cmd += ["-f", "s16le", "-ar", "48000", "-ac", "2", "-ss", str(args.start), "-i", str(pcm), "-c:a", "aac", "-b:a", "192k", "-shortest"]
    subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", args.out], check=True)
    shutil.rmtree(frames, ignore_errors=True)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

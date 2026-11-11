"""逐帧录制宣传片为 MP4（默认 60fps，1920×1080，带音效）。

依赖（本仓库其余部分不需要）：
    pip install playwright imageio-ffmpeg
    浏览器默认用本机已装的 Edge（--channel msedge）；也可 `playwright install chromium` 后传 --channel chromium
    ffmpeg：优先用 PATH 里的，找不到就用 imageio-ffmpeg 自带的

用法（先在仓库根目录起一个静态服务）：
    python -m http.server 8765 --directory promo
    python promo/capture.py --url http://localhost:8765/ --out promo/no-ai-fluff.mp4

原理：页面支持 ?t=<毫秒> 定格；这里只加载一次页面，再逐帧调用 window.seekMs(ms) 截图，
截图直接从管道喂给 ffmpeg（不落盘）；音效由页面里的 window.buildAudio 离线渲染（与画面同一时间轴），一起合成。
"""
from __future__ import annotations

import argparse
import base64
import shutil
import subprocess
import tempfile
from pathlib import Path


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise SystemExit("找不到 ffmpeg：请把它加入 PATH，或 pip install imageio-ffmpeg")


AUDIO_JS = """async () => {
    const sr = 48000, ctx = new OfflineAudioContext(2, Math.ceil(sr * window.VIDEO.duration), sr);
    await window.buildAudio(ctx);
    const b = await ctx.startRendering(), L = b.getChannelData(0), R = b.getChannelData(1);
    const i16 = new Int16Array(L.length * 2);
    for (let i = 0; i < L.length; i++) { i16[2 * i] = Math.max(-1, Math.min(1, L[i])) * 32767; i16[2 * i + 1] = Math.max(-1, Math.min(1, R[i])) * 32767; }
    const u8 = new Uint8Array(i16.buffer); let s = "";
    for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
    return btoa(s);
}"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8765/")
    ap.add_argument("--out", default="promo/no-ai-fluff.mp4")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--start", type=float, default=0.0, help="起始秒")
    ap.add_argument("--end", type=float, default=None, help="结束秒（默认整片）")
    ap.add_argument("--mute", action="store_true", help="不混入音效")
    ap.add_argument("--channel", default="msedge", help="浏览器：msedge / chrome / chromium")
    args = ap.parse_args()
    ffmpeg = find_ffmpeg()
    from playwright.sync_api import sync_playwright

    tmp = Path(tempfile.mkdtemp(prefix="promo_capture_"))
    pcm = tmp / "audio.pcm"
    with sync_playwright() as p:
        launch = {} if args.channel == "chromium" else {"channel": args.channel}
        browser = p.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(f"{args.url}?t={int(args.start * 1000)}")
        page.wait_for_function("window.__promoReady === true", timeout=20000)
        page.evaluate("document.fonts.ready")
        if args.end is None:
            args.end = page.evaluate("window.VIDEO.duration")
        n = int(round((args.end - args.start) * args.fps))
        if not args.mute:
            pcm.write_bytes(base64.b64decode(page.evaluate(AUDIO_JS)))
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(args.fps), "-i", "-"]
        if pcm.exists():
            cmd += ["-f", "s16le", "-ar", "48000", "-ac", "2", "-ss", str(args.start), "-i", str(pcm),
                    "-c:a", "aac", "-b:a", "192k", "-shortest"]
        part = Path(args.out).with_suffix(".part.mp4")   # 写完才改名，中途被打断不会留下打不开的成品文件
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-movflags", "+faststart", str(part)]
        enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        try:
            for i in range(n + 1):
                ms = args.start * 1000 + i * 1000 / args.fps
                page.evaluate(f"window.seekMs({ms})")
                enc.stdin.write(page.screenshot(type="png"))
                if i % (args.fps * 5) == 0:
                    print(f"  {ms / 1000:6.1f}s / {args.end:.0f}s", flush=True)
        finally:
            enc.stdin.close()
            code = enc.wait()
            browser.close()
            shutil.rmtree(tmp, ignore_errors=True)
    if code != 0:
        raise SystemExit(f"ffmpeg 退出码 {code}")
    part.replace(args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

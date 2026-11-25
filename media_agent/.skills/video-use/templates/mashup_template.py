"""video-use 模板：口播 + 素材带 + SRT 字幕 + BGM 混音。

用法（命令行参数全部可选，缺省读同名环境变量）：
    python mashup_template.py --video a.mp4 --materials "b.jpg;c.png" --bgm d.mp3 \
        --srt subs.srt --opening "xxx" --climax "yyy" --out out.mp4

硬约束（改代码前先读 .skills/video-use/SKILL.md）：
1. 画布 = (w, h + h//3)，口播在 (0,0)，素材带在 (0,h) 高 h//3；
2. 字幕只用 SubtitlesClip + SRT，禁止 for 循环 TextClip；
3. 禁止 with_mask/set_mask/to_mask，禁止 resized((w,h))；
4. BGM 音量 0.1。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy import (
    AudioFileClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    VideoFileClip,
    afx,
    concatenate_videoclips,
    vfx,
)
from moviepy.video.tools.subtitles import SubtitlesClip

FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_REGULAR = r"C:\Windows\Fonts\msyh.ttc"


def arg(name: str, default: str = "") -> str:
    """--name 优先，其次环境变量 NAME。"""
    flag = f"--{name}"
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return os.environ.get(name.upper().replace("-", "_"), default)


def wrap_text(text: str, size: int, max_chars: int = 13) -> str:
    """按显示宽度折行：中文按 2 计、ASCII 按 1 计。"""
    out, line, width = [], [], 0
    for ch in text:
        w = 2 if ord(ch) > 127 else 1
        if width + w > max_chars * 2 and line:
            out.append("".join(line))
            line, width = [], 0
        line.append(ch)
        width += w
    if line:
        out.append("".join(line))
    return "\n".join(out)


def text_clip(text: str, width: int, size: int = 34, bg: bool = True, duration: float = 3.5):
    """一条文案：可选半透明底条，避免压在花哨画面上看不清。

    moviepy 2.x 删掉了 TextClip.on_color，底条用 PIL 画好再包成 ImageClip。
    """
    body = wrap_text(text, size)
    font = ImageFont.truetype(FONT_BOLD if bg else FONT_REGULAR, size)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    lines = body.split("\n")
    line_h = int(size * 1.4)
    text_w = max(probe.textlength(line, font=font) for line in lines)
    text_h = line_h * len(lines)
    pad = 14 if bg else 0
    canvas = Image.new("RGBA", (int(text_w) + pad * 2, text_h + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    if bg:
        draw.rounded_rectangle(
            [(0, 0), (canvas.width - 1, canvas.height - 1)],
            radius=18,
            fill=(0, 0, 0, 150),
        )
    for i, line in enumerate(lines):
        draw.text(
            (pad, pad + i * line_h + (line_h - size) // 2),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 255),
        )
    clip = ImageClip(np.array(canvas), transparent=True).with_duration(duration)
    if clip.w > width:  # 太宽就整体缩一下，别越界
        clip = clip.resized(width=width)
    return clip


def build_band(materials: list[str], width: int, height: int, duration: float, top: int = 0):
    """底部素材带：每张图铺满 (width, height) 的条带，按顺序轮播覆盖全片。

    返回一组已经带好 start/position 的 ImageClip，直接塞进 layers。
    不用 CompositeVideoClip 套娃——嵌套合成在 moviepy 2.x 里很容易丢位置。
    """
    usable = []
    for path in materials:
        if not os.path.exists(path):
            print(f"[跳过] 素材不存在：{path}")
            continue
        try:
            with Image.open(path) as im:
                im.load()
                usable.append(im.convert("RGB").copy())
        except Exception as exc:  # 坏图直接跳过，别让整条片子失败
            print(f"[跳过] 素材打不开：{path}（{type(exc).__name__}）")
    if not usable:
        return []

    seg = min(4.0, max(2.0, duration / max(1, len(usable))))
    clips = []
    t = 0.0
    i = 0
    while t < duration - 1e-6:
        img = usable[i % len(usable)]
        # 等比缩放 + 居中裁剪成条带尺寸
        scale = max(width / img.width, height / img.height)
        resized = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
        left = (resized.width - width) // 2
        top = (resized.height - height) // 2
        band = resized.crop((left, top, left + width, top + height))
        clips.append(
            ImageClip(np.array(band))
            .with_start(t)
            .with_duration(min(seg, duration - t))
            .with_position((0, top))  # 素材带贴在口播下方
        )
        t += seg
        i += 1
    return clips


def main() -> int:
    video_path = arg("video")
    out_path = arg("out", "mashup_out.mp4")
    materials = [p for p in arg("materials").split(";") if p.strip()]
    bgm_path = arg("bgm")
    srt_path = arg("srt")
    opening = arg("opening")
    climax = arg("climax")
    extra = arg("extra")
    climax_at = float(arg("climax-at", "0") or 0)

    if not video_path or not os.path.exists(video_path):
        print(f"口播视频不存在：{video_path}")
        return 1

    voice = VideoFileClip(video_path)
    w, h = voice.size
    duration = voice.duration
    canvas_size = (w, h + h // 3)  # ← 硬约束 1
    print(f"[探针] 口播 {w}x{h} / {duration:.2f}s / {voice.fps}fps → 画布 {canvas_size}")

    layers = [voice.with_position((0, 0))]

    band_clips = build_band(materials, w, h // 3, duration, top=h)
    if band_clips:
        layers.extend(band_clips)
    else:
        print("[提示] 没有可用素材，素材带留空")

    # 字幕：只允许 SubtitlesClip + SRT（硬约束 2）
    if srt_path and os.path.exists(srt_path):

        def make_st(txt: str):
            """每句字幕画在一张全画布透明图上，定位在口播区域内。

            SubtitlesClip 用 TextClip 的 mask 做透明，这里改用 PIL 画 RGBA 图，
            既能精确定位，也不会触发任何 with_mask/set_mask 调用。
            """
            canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(canvas)
            size = max(28, int(w * 0.030))
            font = ImageFont.truetype(FONT_BOLD, size)
            body = wrap_text(txt, size)
            lines = body.split("\n")
            line_h = int(size * 1.35)
            y0 = int(h * 0.80) - (line_h * len(lines)) // 2
            for i, line in enumerate(lines):
                tw = draw.textlength(line, font=font)
                draw.text(
                    ((w - tw) / 2, y0 + i * line_h),
                    line,
                    font=font,
                    fill=(255, 255, 255, 255),
                    stroke_width=2,
                    stroke_fill=(0, 0, 0, 255),
                )
            return ImageClip(np.array(canvas), transparent=True).with_duration(1.0)

        subs = SubtitlesClip(srt_path, encoding="utf-8", make_textclip=make_st)
        layers.append(subs)
    elif srt_path:
        print(f"[提示] SRT 不存在，跳过字幕：{srt_path}")

    if opening:
        layers.append(
            text_clip(opening, int(w * 0.62), size=max(26, int(w * 0.028)), duration=3.2)
            .with_start(0.6)
            .with_position(("center", int(h * 0.10)))
            .with_effects([vfx.CrossFadeIn(0.6), vfx.CrossFadeOut(0.6)])
        )
    if climax:
        at = climax_at if climax_at > 0 else max(0.0, duration * 0.68)
        layers.append(
            text_clip(climax, int(w * 0.70), size=max(28, int(w * 0.032)), duration=4.0)
            .with_start(at)
            .with_position(("center", int(h * 0.55)))
            .with_effects([vfx.CrossFadeIn(0.8), vfx.CrossFadeOut(0.8)])
        )
    if extra:
        layers.append(
            text_clip(extra, int(w * 0.62), size=max(24, int(w * 0.026)), duration=3.6)
            .with_start(max(0.0, duration - 4.0))
            .with_position(("center", int(h * 0.12)))
            .with_effects([vfx.CrossFadeIn(0.6), vfx.CrossFadeOut(0.6)])
        )

    # 硬约束 1：size 必须覆盖全部层高
    final = CompositeVideoClip(layers, size=canvas_size).with_duration(duration)

    audio_layers = []
    if voice.audio is not None:
        audio_layers.append(voice.audio)
    if bgm_path and os.path.exists(bgm_path):
        bgm = AudioFileClip(bgm_path)
        looped = bgm.with_effects([afx.AudioLoop(duration=duration), afx.MultiplyVolume(0.1)])  # 硬约束 4
        audio_layers.append(looped)
    if audio_layers:
        final = final.with_audio(CompositeAudioClip(audio_layers))

    out_file = str(Path(out_path).resolve())
    os.makedirs(os.path.dirname(out_file) or ".", exist_ok=True)
    final.write_videofile(
        out_file,
        codec="libx264",
        audio_codec="aac",
        fps=voice.fps,
        preset="medium",
        threads=4,
        logger=None,
    )
    final.close()
    voice.close()
    print(f"[完成] {out_file} ({os.path.getsize(out_file) / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

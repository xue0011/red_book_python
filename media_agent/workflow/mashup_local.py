"""视频剪辑 —— 本地确定性渲染（不依赖 deepagents）

给一段口播视频 + 若干张素材图 + 一首 BGM，按"治愈系混剪"的规则合成：

1. 素材图做成"卡片"：圆角 + 白色柔光边，上下交替出现在画面里（上下排布）
2. 出入场用淡入淡出 + 轻微上浮做光晕过渡
3. 开头与高潮各压一句文案
4. 高潮处切一个"手机屏 5:20"特效
5. BGM 循环铺底，压低到人声之下

为什么不用 deepagent：deepagent 让模型现写 moviepy 脚本，慢且不可复现；
这条链路是固定工序，直接用 moviepy 组合即可，稳定在秒级产出。

对外入口：
    render_mashup(input_video, materials, bgm_path, ...) -> dict
"""
from __future__ import annotations

import os
import time
import uuid

import numpy as np
from typing import Callable, Iterable, Sequence

from moviepy import (
    AudioFileClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    VideoFileClip,
    concatenate_audioclips,
)
from moviepy.video.fx import CrossFadeIn, CrossFadeOut, Resize
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config import settings

# ── 画面参数 ──────────────────────────────────────────────────────────
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_NORMAL = r"C:\Windows\Fonts\msyh.ttc"

CARD_WIDTH_RATIO = 0.44       # 素材卡宽度占画面比例（不挡脸）
CARD_HEIGHT_RATIO = 0.42      # 素材卡最大高度占画面比例
CARD_INSET = 0.055            # 上下留白（占画面高度）
CARD_FADE = 0.6               # 光晕过渡时长
CARD_DEFAULT_DUR = 3.6        # 单张素材停留时长

BGM_VOLUME = 0.14             # BGM 铺底音量（人声之下）
OPEN_CAPTION = "不知何时起，我开始迷恋上了天空"
CLIMAX_CAPTION = "我们永远碰不到星星，或我们已成为了星星"

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


# ── 工具 ──────────────────────────────────────────────────────────────
def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold else FONT_NORMAL
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return mask


def _prepare_card(src: str, width: int, max_height: int, cache_dir: str) -> str:
    """把素材图做成"圆角 + 柔光"的 PNG（带透明通道），返回缓存路径。

    文件损坏/不是真图片时返回空串（调用方跳过），避免整条渲染挂掉。
    """
    stem = os.path.splitext(os.path.basename(src))[0]
    out = os.path.join(cache_dir, f"card_{stem}_{width}.png")
    if os.path.exists(out) and os.path.getmtime(out) > os.path.getmtime(src):
        return out

    try:
        im = Image.open(src)
        im.load()
        im = im.convert("RGB")
    except Exception:
        return ""
    im.thumbnail((width, max_height), Image.LANCZOS)
    w, h = im.size
    radius = max(16, int(min(w, h) * 0.06))
    mask = _rounded_mask((w, h), radius)

    pad = int(min(w, h) * 0.09)          # 柔光留白
    canvas = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))

    # 白色柔光：把卡片剪影放大模糊后当光晕
    glow_mask = Image.new("L", canvas.size, 0)
    glow_mask.paste(mask, (pad, pad))
    glow_mask = glow_mask.filter(ImageFilter.GaussianBlur(pad * 0.55))
    glow = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
    glow.putalpha(glow_mask.point(lambda v: int(v * 0.55)))
    canvas = Image.alpha_composite(canvas, glow)

    body = im.convert("RGBA")
    body.putalpha(mask)
    canvas.paste(body, (pad, pad), body)

    # 细白边，让卡片边界更干净
    edge = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle(
        (pad, pad, pad + w - 1, pad + h - 1), radius, outline=(255, 255, 255, 210), width=max(2, pad // 12)
    )
    canvas = Image.alpha_composite(canvas, edge)

    os.makedirs(cache_dir, exist_ok=True)
    canvas.save(out)
    return out


def _prepare_phone(cache_dir: str, width: int = 430) -> str:
    """生成"手机屏显示 5:20"的透明 PNG。"""
    out = os.path.join(cache_dir, f"phone_520_{width}.png")
    if os.path.exists(out):
        return out

    w = width
    h = int(w * 1.98)
    pad = int(w * 0.18)
    canvas = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))

    # 外发光
    glow = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(glow).rounded_rectangle(
        (pad, pad, pad + w, pad + h), int(w * 0.14), fill=255
    )
    glow = glow.filter(ImageFilter.GaussianBlur(pad * 0.5))
    halo = Image.new("RGBA", canvas.size, (255, 240, 250, 0))
    halo.putalpha(glow.point(lambda v: int(v * 0.75)))
    canvas = Image.alpha_composite(canvas, halo)

    # 机身
    body = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(body)
    d.rounded_rectangle((pad, pad, pad + w, pad + h), int(w * 0.13), fill=(18, 18, 26, 255))

    # 屏幕（夜空渐变）
    sw, sh = int(w * 0.88), int(h * 0.9)
    sx, sy = pad + (w - sw) // 2, pad + (h - sh) // 2
    screen = Image.new("RGB", (sw, sh), (10, 14, 34))
    sd = ImageDraw.Draw(screen)
    for y in range(sh):
        t = y / max(1, sh - 1)
        sd.line([(0, y), (sw, y)], fill=(int(12 + 40 * t), int(16 + 30 * t), int(38 + 60 * t)))

    # 星星
    import random

    random.seed(520)
    for _ in range(140):
        x, y = random.randint(4, sw - 5), random.randint(4, sh - 5)
        r = random.choice([1, 1, 1, 2])
        alpha = random.randint(120, 255)
        sd.ellipse((x, y, x + r, y + r), fill=(255, 255, 255, alpha))

    # 时间
    f_big = _font(int(sw * 0.42))
    sd.text((sw / 2, sh * 0.42), "5:20", font=f_big, fill=(255, 255, 255), anchor="mm")
    f_small = _font(int(sw * 0.075), bold=False)
    sd.text((sw / 2, sh * 0.60), "我在 5:20 睡觉  13:14 准时醒", font=f_small,
            fill=(214, 222, 255), anchor="mm")
    f_tiny = _font(int(sw * 0.058), bold=False)
    sd.text((sw / 2, sh * 0.70), "AM 5:20 · 早安", font=f_tiny, fill=(160, 172, 210), anchor="mm")

    mask = _rounded_mask((sw, sh), int(w * 0.10))
    canvas.paste(screen, (sx, sy), mask)

    # 刘海
    ImageDraw.Draw(canvas).rounded_rectangle(
        (pad + w // 2 - int(w * 0.16), pad + int(h * 0.035),
         pad + w // 2 + int(w * 0.16), pad + int(h * 0.075)),
        int(w * 0.03), fill=(8, 8, 12, 255),
    )

    os.makedirs(cache_dir, exist_ok=True)
    canvas.save(out)
    return out


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: float) -> list[str]:
    """按像素宽度折行（中文没有空格，只能逐字量）；已有的换行优先保留。"""
    out: list[str] = []
    for para in text.split("\n"):
        lines, cur = [], ""
        for ch in para:
            if font.getlength(cur + ch) <= max_w:
                cur += ch
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        out.extend(lines or [""])
    return out


def _text_clip(text: str, width: int, size: int = 44, color: str = "white",
               duration: float = 3.0, bg: bool = False):
    """一句居中文案；bg=True 时用 PIL 画一层圆角半透明底条，任何画面上都读得清。"""
    if not bg:
        return TextClip(
            font=FONT_BOLD, text=text, font_size=size, color=color,
            method="caption", size=(int(width), None), text_align="center",
        ).with_duration(duration)

    font = _font(size)
    # 长句在第一个逗号处断成两行，避免出现"星星"这种孤字
    if "\n" not in text and len(text) > 13 and "，" in text:
        text = text.replace("，", "，\n", 1)
    lines = _wrap(text, font, width * 0.90)
    line_h = int(size * 1.38)
    pad_y = int(size * 0.42)
    w = int(width)
    h = line_h * len(lines) + pad_y * 2

    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w - 1, h - 1), int(size * 0.55), fill=(6, 10, 24, 140))
    for i, line in enumerate(lines):
        d.text((w / 2, pad_y + line_h * i + line_h / 2), line, font=font,
               fill=(255, 255, 255, 255), anchor="mm")
    return ImageClip(np.array(im), transparent=True).with_duration(duration)


def _scaled(clip, factor: float):
    """等比缩放（moviepy 2.x 的 Resize 用 width/height，这里给一个便捷包装）。"""
    return clip.with_effects([Resize(width=int(clip.w * factor))])


# ── 主流程 ────────────────────────────────────────────────────────────
def render_mashup(
    input_video: str,
    materials: Sequence[str] = (),
    bgm_path: str = "",
    *,
    open_caption: str = OPEN_CAPTION,
    climax_caption: str = CLIMAX_CAPTION,
    extra_caption: str = "",
    climax_at: float | None = None,
    speed: float = 1.0,
    card_duration: float = CARD_DEFAULT_DUR,
    out_dir: str = "",
    progress: Callable[[float, str], None] | None = None,
) -> dict:
    """渲染一条"口播 + 素材穿插 + BGM"的成片。

    返回 {"ok", "output", "duration", "clips", "cards", "error", "log"}。
    """
    started = time.time()
    log: list[str] = []

    def note(msg: str):
        log.append(f"[{time.time() - started:6.1f}s] {msg}")
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    if not input_video or not os.path.exists(input_video):
        return {"ok": False, "error": f"口播视频不存在：{input_video}", "log": "\n".join(log)}

    out_dir = out_dir or os.path.join(settings.video_output_dir or ".cache/videos")
    os.makedirs(out_dir, exist_ok=True)
    work = os.path.join(".cache", "mashup", "prepared")

    note(f"载入口播：{os.path.basename(input_video)}")
    base = VideoFileClip(input_video)
    if speed and abs(speed - 1.0) > 0.01:
        base = base.with_speed_scaled(speed)
        note(f"倍速 {speed}x → {base.duration:.1f}s")

    W, H = base.size
    duration = float(base.duration)

    images = [m for m in materials if m and os.path.exists(m)
              and os.path.splitext(m)[1].lower() in IMAGE_EXT]
    skipped = [os.path.basename(m) for m in materials if m and m not in images]
    if skipped:
        note(f"跳过非图片/已失效素材：{', '.join(skipped)}")

    # 损坏的图片在这里就被剔除
    usable: list[str] = []
    for m in images:
        if _prepare_card(m, int(W * CARD_WIDTH_RATIO), int(H * CARD_HEIGHT_RATIO), work):
            usable.append(m)
        else:
            note(f"素材无法读取，已跳过：{os.path.basename(m)}")
    images = usable
    note(f"画面 {W}x{H}，时长 {duration:.1f}s，可用素材 {len(images)} 张")

    overlay = []

    # 1) 素材卡：左上 / 右下交替，避开人脸与烧死的字幕区
    if images:
        card_w = int(W * CARD_WIDTH_RATIO)
        card_h_max = int(H * CARD_HEIGHT_RATIO)
        card_dur = min(card_duration, max(2.0, duration / max(1, len(images)) * 0.6))

        span_start = duration * 0.10 if duration > 20 else duration * 0.05
        span_end = duration * 0.86
        span = max(0.1, span_end - span_start - card_dur)
        step = span / max(1, len(images) - 1) if len(images) > 1 else 0

        for i, src in enumerate(images):
            card_png = _prepare_card(src, card_w, card_h_max, work)
            card = ImageClip(card_png)
            t0 = span_start + step * i
            margin_x = int(W * 0.035)
            x0 = margin_x if i % 2 == 0 else W - card.w - margin_x
            y0 = int(H * 0.055) if i % 2 == 0 else int(H * 0.60)

            # 上浮 8px 的入场，配合淡入淡出做"光晕过渡"
            anim = (
                card.with_duration(card_dur)
                .with_start(t0)
                .with_position(lambda t, x0=x0, y0=y0: (int(x0), int(y0 - 8 * min(1.0, t / 1.2))))
                .with_effects([CrossFadeIn(CARD_FADE), CrossFadeOut(CARD_FADE)])
                .with_opacity(0.97)
            )
            overlay.append(anim)
            note(f"素材 {i + 1}/{len(images)} → {t0:.1f}s（{'上' if i % 2 == 0 else '下'}）")

    # 2) 高潮点
    climax = climax_at if climax_at else min(duration * 0.68, max(0.0, duration - 8))

    # 3) 手机屏 5:20
    if duration > 6:
        phone_png = _prepare_phone(work, int(H * 0.36))
        pim = Image.open(phone_png)
        pw, ph = pim.size
        phone = (
            ImageClip(phone_png)
            .with_duration(3.2)
            .with_start(climax)
            .with_position((int(W - pw - W * 0.05), max(0, int((H - ph) / 2))))
            .with_effects([CrossFadeIn(0.8), CrossFadeOut(0.6)])
        )
        overlay.append(phone)
        note(f"手机屏 5:20 → {climax:.1f}s")

    # 4) 文案
    if open_caption:
        cut = min(4.5, max(2.5, duration * 0.06))
        cap = (
            _text_clip(open_caption, int(W * 0.86), size=max(30, int(W * 0.036)), duration=cut, bg=True)
            .with_start(0.6)
            .with_position(("center", int(H * 0.80)))
            .with_effects([CrossFadeIn(0.7), CrossFadeOut(0.7)])
        )
        overlay.append(cap)
        note(f"开场文案 → 0.6s")

    if climax_caption:
        cap2 = (
            _text_clip(climax_caption, int(W * 0.54), size=max(26, int(W * 0.028)),
                       duration=4.2, bg=True)
            .with_start(climax + 0.6)
            .with_position((int(W * 0.05), int(H * 0.72)))
            .with_effects([CrossFadeIn(0.8), CrossFadeOut(0.8)])
        )
        overlay.append(cap2)
        note(f"高潮文案 → {climax + 0.6:.1f}s")

    if extra_caption:
        cap3 = (
            _text_clip(extra_caption, int(W * 0.84), size=max(26, int(W * 0.030)),
                       duration=3.2, bg=True)
            .with_start(max(0.6, duration - 4.2))
            .with_position(("center", int(H * 0.80)))
            .with_effects([CrossFadeIn(0.7), CrossFadeOut(0.7)])
        )
        overlay.append(cap3)

    # 5) 合成
    note("合成画面…")
    final = CompositeVideoClip([base, *overlay], size=(W, H)).with_duration(duration)

    # 6) 声音：原声 + BGM 铺底
    audio_parts = []
    if base.audio is not None:
        audio_parts.append(base.audio)
    if bgm_path and os.path.exists(bgm_path):
        bgm = AudioFileClip(bgm_path)
        if bgm.duration < duration:
            loops = int(duration / bgm.duration) + 1
            bgm = concatenate_audioclips([bgm] * loops)
        bgm = bgm.subclipped(0, duration).with_volume_scaled(BGM_VOLUME)
        audio_parts.append(bgm)
        note(f"BGM：{os.path.basename(bgm_path)} @ {int(BGM_VOLUME * 100)}%")
    elif bgm_path:
        note(f"BGM 文件不存在，跳过：{bgm_path}")

    if audio_parts:
        final = final.with_audio(CompositeAudioClip(audio_parts))

    # 7) 渲染
    out = os.path.join(
        out_dir, f"mashup_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}.mp4"
    )
    note(f"渲染 → {out}（{len(overlay)} 个叠加层）")
    try:
        final.write_videofile(
            out, codec="libx264", audio_codec="aac", fps=min(30, int(base.fps or 30)),
            preset="veryfast", threads=4, logger=None,
            ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"渲染失败：{exc}", "log": "\n".join(log)}
    finally:
        for c in (final, base):
            try:
                c.close()
            except Exception:
                pass

    size_mb = os.path.getsize(out) / 1024 / 1024
    note(f"完成：{size_mb:.1f} MB / {time.time() - started:.1f}s")
    return {
        "ok": True,
        "output": out,
        "duration": duration,
        "clips": len(overlay),
        "cards": len(images),
        "log": "\n".join(log),
    }

---
name: video-use
description: 用 moviepy 把「口播视频 + 图片素材 + BGM」合成一条竖版/横版混剪成片：底部素材带 + 顶部口播 + SRT 字幕 + BGM 混音。包含画布尺寸、字幕、蒙版、混音四条硬约束，违反会出黑屏/裁切/字幕重复。
license: MIT
---

# video-use：口播 + 素材 + BGM 合成

## 什么时候用
用户给一段口播（本地文件或链接）、若干图片素材、一首 BGM，要求合成一条能直接发的成片：
上下排布（上面口播、下面素材带）、自动字幕、BGM 混音、封面文案。

## 输入
| 名字 | 说明 |
| --- | --- |
| `input_video` | 口播视频绝对路径（mp4/mov） |
| `materials` | 图片绝对路径列表（jpg/png/webp） |
| `bgm` | 音频绝对路径（mp3/wav/m4a），可为空 |
| `captions` | 文案 dict：`opening`（开头 3 秒）、`climax`（高潮）、`extra`（结尾） |

## 输出
一条 mp4，帧率与口播一致，分辨率 `(w, h + h//3)`，音频为「口播 100% + BGM 10%」。

## 硬约束（违反任何一条，成片一定出问题）

1. **画布尺寸必须是 `(w, h + h // 3)`**
   - `w, h` 取口播视频原始分辨率；
   - 口播放 `(0, 0)`，素材带放 `(0, h)`，高 `h // 3`；
   - `CompositeVideoClip(..., size=(w, h + h // 3))` —— 写成 `(w, h)` 会把底部素材整条裁掉。

2. **字幕只能用 `SubtitlesClip` 加载 SRT**
   - 严禁 `for` 循环里逐个 `TextClip` 拼字幕（会重复、会错帧）；
   - 必须给 `make_textclip` 显式指定位置，否则默认贴到画布最底部（素材区）被裁：
     ```python
     def make_st(txt):
         return (TextClip(text=txt, font="Microsoft YaHei", font_size=52,
                          color="white", stroke_color="black", stroke_width=2,
                          size=(w, None), method="caption")
                 .with_position(("center", int(h * 0.82))))
     subs = SubtitlesClip("subtitles.srt", encoding="utf-8", make_textclip=make_st)
     ```

3. **禁止隐式蒙版**
   - 不许 `.with_mask()` / `.set_mask()` / `.to_mask()`；
   - 不许 `clip.resized((w, h))` 这种把宽高比改掉的写法（用 `resized(height=...)` 或 `resized(width=...)` 单边缩放）；
   - 需要圆角/透明效果时，用 PIL 生成带 alpha 的 PNG，再 `ImageClip(np.array(im), transparent=True)`。

4. **混音比例固定**
   - BGM `volume=0.1`（10%），口播 `volume=1.0`；
   - `CompositeAudioClip([voice, bgm.with_duration(voice.duration)])`，BGM 要 `afx.AudioLoop` 循环到位。

## 步骤
1. **先探针**：`VideoFileClip(input_video)` 读 `size / duration / fps`，对外打印，别猜。
2. **生成 SRT**：有 ASR 就转写口播（本项目 `workflow/replicate.transcribe_only` 或 `tools.autodl_funasr.transcribe`），拿到 `segments[start,end,text]` 后写标准 SRT：
   ```
   1
   00:00:01,200 --> 00:00:04,500
   今天想跟你聊一个事
   ```
   没有 ASR 文本就跳过字幕层（**不要伪造字幕**）。
3. **素材带**：每张图 `resized(height=h//3)`，按 `concatenate_videoclips` 顺序铺满整条，或用 `CompositeVideoClip` 逐张定时出现。
4. **叠加文案**：三张 `TextClip`（opening/climax/extra），`with_start` + `CrossFadeIn/Out`，位置在口播区域内（不要压到 `y >= h`）。
5. **合成**：`CompositeVideoClip([voice, band, subs, captions...], size=(w, h + h//3))`。
6. **输出**：`write_videofile(out, codec="libx264", audio_codec="aac", fps=src.fps, threads=4, preset="medium", logger=None)`。

## 模板
`templates/mashup_template.py` 是可直接跑的骨架：把里面的 `INPUT` 路径换成任务给的绝对路径，改文案 dict，然后
`python templates/mashup_template.py`。**优先复用模板**，不要从零手写 moviepy 代码。

## 自检（交付前必做）
- [ ] 输出分辨率 == `(w, h + h//3)`
- [ ] 成片时长 ≈ 口播时长
- [ ] 有音轨（`clip.audio is not None`）
- [ ] 抽 3 帧确认字幕在口播区域内、素材带没被裁
- [ ] 没有 `mask` 相关调用（`grep -n "with_mask\|set_mask\|to_mask" script.py` 为空）

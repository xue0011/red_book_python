"""show_review页面"""
import streamlit as st
import json
import os
import shutil
import sqlite3
import tempfile

from config import settings as _settings


_DOUYIN_PROJECT = (
    _settings.douyin_project_dir
    or os.path.expanduser("~/Documents/project/douyin")
)
DOUYIN_SETTINGS_FILE = os.path.join(_DOUYIN_PROJECT, "config", "settings.json")


EDGE_COOKIES_DIR = os.path.expandvars(
    r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Network"
)
EDGE_COOKIES_FILE = os.path.join(EDGE_COOKIES_DIR, "Cookies")




def _load_douyin_cookie() -> str:
    """从 douyin 配置文件读取 Cookie"""
    try:
        if os.path.exists(DOUYIN_SETTINGS_FILE):
            with open(DOUYIN_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("cookie", "")
    except Exception:
        pass
    return ""




def _save_douyin_cookie(cookie: str):
    """保存 Cookie 到 douyin 配置文件"""
    os.makedirs(os.path.dirname(DOUYIN_SETTINGS_FILE), exist_ok=True)
    data = {}
    if os.path.exists(DOUYIN_SETTINGS_FILE):
        try:
            with open(DOUYIN_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    data["cookie"] = cookie
    with open(DOUYIN_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)




def _read_edge_cookies() -> tuple[str, str | None]:
    """通过 CDP (Chrome DevTools Protocol) 从 Edge 浏览器直接读取 douyin.com Cookie


    自动处理 Edge 文件锁定和 v20 加密，通过浏览器自身 API 读取无需解密。


    Returns:
        (cookie_string, error_message)
    """
    import subprocess
    import time


    # Step 1: 查找或启动带调试端口的 Edge
    # 尝试常见调试端口
    debug_port = None
    for port in [9222, 9223, 9224, 9225]:
        try:
            import urllib.request
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2)
            debug_port = port
            break
        except Exception:
            continue


    edge_launched = False
    edge_proc = None


    if debug_port is None:
        # 需要自己启动 Edge（先杀后台进程释放 profile 锁）
        for proc_name in ["msedge.exe", "msedgewebview2.exe"]:
            try:
                subprocess.run(
                    ["taskkill", "/f", "/im", proc_name],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass
        time.sleep(1.5)


        user_data = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data")
        debug_port = 9223
        try:
            edge_proc = subprocess.Popen(
                [
                    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    f"--remote-debugging-port={debug_port}",
                    f"--user-data-dir={user_data}",
                    "--headless=new",  # 无头模式，不显示窗口
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            edge_launched = True
            # 等待 Edge 启动
            for _ in range(15):
                time.sleep(0.5)
                try:
                    import urllib.request
                    urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json/version", timeout=2)
                    break
                except Exception:
                    continue
        except FileNotFoundError:
            # 尝试其他 Edge 安装路径
            alt_paths = [
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            ]
            for alt in alt_paths:
                if os.path.exists(alt):
                    try:
                        edge_proc = subprocess.Popen(
                            [alt, f"--remote-debugging-port={debug_port}",
                             f"--user-data-dir={user_data}", "--headless=new"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        )
                        edge_launched = True
                        for _ in range(15):
                            time.sleep(0.5)
                            try:
                                import urllib.request
                                urllib.request.urlopen(
                                    f"http://127.0.0.1:{debug_port}/json/version", timeout=2
                                )
                                break
                            except Exception:
                                continue
                        break
                    except Exception:
                        continue


    if debug_port is None:
        return "", "无法连接到 Edge 调试端口，请确认 Edge 已安装"


    # Step 2: 通过 CDP 获取 Cookie
    try:
        import urllib.request
        import json as _json


        # 获取 WebSocket 端点
        resp = urllib.request.urlopen(
            f"http://127.0.0.1:{debug_port}/json/version", timeout=5
        )
        version_info = _json.loads(resp.read().decode())
        ws_url = version_info.get("webSocketDebuggerUrl", "")
        if not ws_url:
            return "", "无法获取 Edge CDP WebSocket URL"


        # 用 websocket-client 连接
        try:
            from websocket import create_connection
        except ImportError:
            return "", "缺少 websocket-client 库，请执行: uv add websocket-client"


        ws = create_connection(ws_url, timeout=10)


        # 发送 Network.getCookies 命令（可指定域名）
        # 获取所有 douyin 相关域名的 cookie
        all_cookies = []
        domains = ["www.douyin.com", ".douyin.com", ".iesdouyin.com"]
        for domain in domains:
            msg = _json.dumps({
                "id": 1,
                "method": "Network.getCookies",
                "params": {"domain": domain},
            })
            ws.send(msg)
            resp_data = _json.loads(ws.recv())
            cookies = resp_data.get("result", {}).get("cookies", [])
            all_cookies.extend(cookies)


        ws.close()


        if not all_cookies:
            return "", (
                "Edge 中未找到 douyin.com 的 Cookie。\n"
                "请先在 Edge 浏览器中打开 douyin.com 并登录"
            )


        # 去重、拼成 cookie 字符串
        seen = set()
        parts = []
        for c in all_cookies:
            key = c["name"]
            if key not in seen:
                seen.add(key)
                parts.append(f"{key}={c['value']}")


        cookie_str = "; ".join(parts)
        return cookie_str, None


    except Exception as e:
        return "", f"CDP 读取 Cookie 失败: {e}"


    finally:
        # Step 3: 清理
        if edge_launched and edge_proc:
            try:
                edge_proc.terminate()
                edge_proc.wait(timeout=5)
            except Exception:
                try:
                    edge_proc.kill()
                except Exception:
                    pass




def _add_history(action: str, summary: str):
    from datetime import datetime
    st.session_state.history.append({
        "time": datetime.now().strftime("%H:%M"),
        "action": action,
        "summary": summary[:200],
    })




def show_review():
    """数据复盘 — 接入真实抖音数据"""
    st.title("📊 数据复盘智能体")
    st.markdown("输入抖音主页链接 → 采集真实作品数据 → AI 多维度诊断分析")


    #   Cookie 配置区
    with st.expander("🍪 抖音 Cookie 配置", expanded=False):
        st.caption(
            "两种方式获取 Cookie：① 点按钮自动从 Edge 读取  ② 手动复制粘贴"
        )


        # 方式一：自动从 Edge 读取
        col_auto, col_tip = st.columns([1, 3])
        with col_auto:
            if st.button("🔍 从 Edge 浏览器获取", use_container_width=True,
                         help="自动读取 Edge 中已登录的抖音 Cookie"):
                cookie_str, err = _read_edge_cookies()
                if err:
                    st.error(err)
                else:
                    _save_douyin_cookie(cookie_str)
                    st.success(f"✅ 已从 Edge 获取并保存 Cookie（{len(cookie_str)} 字符）")
                    st.rerun()


        with col_tip:
            saved_cookie = _load_douyin_cookie()
            if saved_cookie:
                st.caption(f"✅ 已配置 Cookie（{len(saved_cookie)} 字符）")
            else:
                st.caption("⚠️ 尚未配置 Cookie，请点击按钮获取或手动粘贴")


        # 方式二：手动粘贴
        cookie_input = st.text_area(
            "或手动粘贴 Cookie 字符串",
            value=saved_cookie,
            placeholder="从浏览器 F12 → Application → Cookies → 复制完整 Cookie...",
            height=100,
            key="douyin_cookie_input",
        )
        if st.button("💾 手动保存 Cookie"):
            _save_douyin_cookie(cookie_input)
            st.success("Cookie 已保存")
            st.rerun()


    st.markdown("---")


    url = st.text_input(
        "🔗 抖音个人主页链接",
        value="https://www.douyin.com/user/self",
        placeholder="https://www.douyin.com/user/xxxxx...",
    )


    if st.button("🔍 获取数据并分析", type="primary", use_container_width=True):
        if not url:
            st.warning("请输入抖音主页链接")
            return


        # 检查 Cookie 是否配置
        cookie = _load_douyin_cookie()
        if not cookie:
            st.error(
                "请先在上方 🍪 抖音 Cookie 配置 中粘贴 Cookie 并保存"
            )
            return


        with st.spinner("正在通过本地 douyin 项目采集数据..."):
            from workflow.review import review_graph
            result = review_graph.invoke({"profile_url": url})


        # 存入 session_state 防止页面刷新后丢失
        st.session_state["review_result"] = result
        st.session_state["review_url"] = url


    #   从 session_state 恢复结果
    result = st.session_state.get("review_result")
    url = st.session_state.get("review_url", "")


    if result:
        error = result.get("error_msg", "")
        if error:
            st.error(error)
            return


        data_str = result.get("video_data", "[]")
        try:
            items = json.loads(data_str)
        except Exception:
            items = []


        if not items:
            st.warning("未获取到作品数据。请检查：\n"
                       "1. douyin 项目是否已配置 Cookie\n"
                       "2. 链接是否正确\n"
                       "3. 网络是否正常")
            return


        funnel_diag = result.get("funnel_diagnosis", "")
        content_eval = result.get("content_assessment", "")
        suggestions = result.get("suggestions", "")


        # 数据概览卡片
        total_likes = sum(i.get("点赞", 0) for i in items)
        total_comments = sum(i.get("评论", 0) for i in items)
        total_shares = sum(i.get("分享", 0) for i in items)
        total_collects = sum(i.get("收藏", 0) for i in items)
        total_plays = sum(i.get("播放", 0) for i in items)
        has_plays = total_plays > 0


        if has_plays:
            cols = st.columns(6)
            cols[0].metric("📊 作品数", len(items))
            cols[1].metric("👀 总播放", f"{total_plays:,}")
            cols[2].metric("❤️ 总点赞", f"{total_likes:,}")
            cols[3].metric("💬 总评论", f"{total_comments:,}")
            cols[4].metric("🔁 总分享", f"{total_shares:,}")
            cols[5].metric("⭐ 总收藏", f"{total_collects:,}")
        else:
            cols = st.columns(5)
            cols[0].metric("📊 作品数", len(items))
            cols[1].metric("❤️ 总点赞", f"{total_likes:,}")
            cols[2].metric("💬 总评论", f"{total_comments:,}")
            cols[3].metric("🔁 总分享", f"{total_shares:,}")
            cols[4].metric("⭐ 总收藏", f"{total_collects:,}")


        # 作品详情表
        with st.expander("📋 作品数据明细", expanded=False):
            st.dataframe(items, use_container_width=True)


        # 分析 tabs
        tab1, tab2, tab3 = st.tabs(["📉 漏斗诊断", "📝 内容评估", "💡 优化策略"])
        with tab1:
            st.markdown(funnel_diag)
        with tab2:
            st.markdown(content_eval)
        with tab3:
            st.markdown(suggestions)


        #   完整报告下载
        st.markdown("---")


        # 生成完整报告
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M")


        report = f"""# 📊 抖音数据复盘报告


> 生成时间：{now}
> 分析链接：{url}
> 作品数量：{len(items)} 条


---


## 📈 总体数据概览


| 指标 | 数值 |
|------|------|
| 作品数 | {len(items)} |
"""
        if has_plays:
            report += f"| 👀 总播放 | {total_plays:,} |\n"
        report += f"""| ❤️ 总点赞 | {total_likes:,} |
| 💬 总评论 | {total_comments:,} |
| 🔁 总分享 | {total_shares:,} |
| ⭐ 总收藏 | {total_collects:,} |
"""


        # 作品数据明细
        report += "\n---\n\n## 📋 作品数据明细\n\n"
        if items:
            keys = list(items[0].keys())
            header = "| " + " | ".join(keys) + " |\n"
            sep = "|" + "|".join(["------"] * len(keys)) + "|\n"
            report += header + sep
            for item in items:
                row = "| " + " | ".join(str(item.get(k, "")) for k in keys) + " |\n"
                report += row


        # 漏斗诊断
        report += "\n---\n\n## 📉 漏斗诊断\n\n"
        report += funnel_diag + "\n"


        # 内容评估
        report += "\n---\n\n## 📝 内容评估\n\n"
        report += content_eval + "\n"


        # 优化策略
        report += "\n---\n\n## 💡 优化策略\n\n"
        report += suggestions + "\n"


        st.download_button(
            "📥 下载完整复盘报告",
            data=report,
            file_name=f"抖音复盘报告_{datetime.now().strftime('%Y%m%d_%H%M')}.md",
            mime="text/markdown",
            use_container_width=True,
        )


        _add_history("数据复盘", url[:60])


show_review()
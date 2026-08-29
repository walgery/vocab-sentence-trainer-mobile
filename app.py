"""
英语生词造句复习器（移动版）
- 手机 / 平板触屏适配 + PWA（可添加到主屏幕）
- 多格式导入生词表 / 导入已有例句
- 分批并行调用大模型造句，实时进度
- 中文翻译 + 词义高亮 + 卡片复习
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from src.generator import (
    SentenceItem,
    generate_batch_for_words,
    highlight_word_html,
)
from src.importers import import_vocab_file, parse_manual_input
from src.review import ReviewState
from src.sentence_io import import_sentences_file
from src.vocab_scope import level_label

load_dotenv()

ROOT = Path(__file__).resolve().parent

st.set_page_config(
    page_title="英语生词造句复习器",
    page_icon="📘",
    layout="wide",
    initial_sidebar_state="collapsed",  # 移动端默认收起侧边栏
)

# —— PWA：manifest + 主题色（配合 static/ 目录实现「添加到主屏幕」App 体验）——
PWA_HEAD = """
<link rel="manifest" href="app/static/manifest.json">
<meta name="theme-color" content="#1e3a4c">
<link rel="apple-touch-icon" href="app/static/icon.svg">
"""

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Source+Serif+4:opsz,wght@8..60,500;8..60,700&display=swap');

html, body, [class*="css"] {
  font-family: 'Noto Sans SC', sans-serif;
  -webkit-text-size-adjust: 100%;
}
.main-title {
  font-family: 'Source Serif 4', serif;
  font-size: 1.8rem;
  font-weight: 700;
  letter-spacing: 0.02em;
  margin-bottom: 0.2rem;
}
.sub-title {
  color: #5a6570;
  margin-bottom: 1.2rem;
}
.sentence-card {
  background: linear-gradient(145deg, #f7fafc 0%, #eef4f8 100%);
  border-left: 4px solid #2f6f8f;
  border-radius: 0 12px 12px 0;
  padding: 1.1rem 1.2rem;
  margin: 0.7rem 0;
}
.sentence-en {
  font-family: 'Source Serif 4', Georgia, serif;
  font-size: 1.2rem;
  line-height: 1.55;
  color: #1a2330;
  margin-bottom: 0.55rem;
}
.sentence-zh {
  font-size: 1rem;
  color: #3d4a57;
  margin-bottom: 0.45rem;
}
.sense-tag {
  display: inline-block;
  background: #d6ebf5;
  color: #1e5a75;
  padding: 0.15rem 0.6rem;
  border-radius: 4px;
  font-size: 0.9rem;
  font-weight: 500;
}
mark.target-word {
  background: #ffe08a;
  color: #1a2330;
  padding: 0 0.15em;
  border-radius: 3px;
  font-weight: 700;
}
.review-shell {
  background: radial-gradient(ellipse at top left, #e8f2f7 0%, #f6f4ef 45%, #f0ebe3 100%);
  border-radius: 16px;
  padding: 1.5rem 1.25rem;
  min-height: 240px;
}
.stat-pill {
  display: inline-block;
  background: #fff;
  border: 1px solid #d5dde5;
  border-radius: 8px;
  padding: 0.35rem 0.8rem;
  margin-right: 0.4rem;
  margin-bottom: 0.4rem;
  font-size: 0.88rem;
}
div[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #1e3a4c 0%, #2a5166 100%);
}
div[data-testid="stSidebar"] * {
  color: #eef5f8 !important;
}
div[data-testid="stSidebar"] .stSelectbox label,
div[data-testid="stSidebar"] .stRadio label,
div[data-testid="stSidebar"] .stTextInput label,
div[data-testid="stSidebar"] .stCheckbox label {
  color: #c5d8e4 !important;
}

/* —— 移动端触屏适配 —— */
@media (max-width: 820px) {
  .main-title { font-size: 1.5rem; }
  .sub-title { font-size: 0.95rem; }
  .sentence-card { padding: 0.9rem 0.9rem; margin: 0.6rem 0; }
  .sentence-en { font-size: 1.05rem; }
  .sentence-zh { font-size: 0.95rem; }
  .review-shell { padding: 1.15rem 1rem; min-height: 190px; }
  /* 触屏按钮：加高、加大字号 */
  .stButton > button {
    min-height: 3.1rem;
    font-size: 1.02rem;
    border-radius: 10px;
  }
  /* 导航标签更紧凑 */
  div[role="radiogroup"] label {
    padding: 0.3rem 0.6rem;
  }
}

/* 手机竖屏：复习区英文再放大一点，突出主内容 */
@media (max-width: 480px) {
  .review-shell .sentence-en { font-size: 1.25rem; }
}
</style>
"""
st.markdown(PWA_HEAD, unsafe_allow_html=True)
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# 把 Streamlit 框架自带的英文界面（右上角 ⋮ 菜单、运行状态栏等）替换为中文
UI_TRANSLATE_JS = """
<script>
(function () {
  const map = [
    ["Running", "运行中"],
    ["STOP", "停止"],
    ["Deploy", "部署"],
    ["Rerun", "重新运行"],
    ["Settings", "设置"],
    ["Print", "打印"],
    ["About", "关于"],
    ["Get help", "获取帮助"],
    ["Report a bug", "报告问题"],
    ["Clear cache", "清除缓存"],
    ["Developer options", "开发者选项"],
    ["Run on save", "保存时自动运行"],
    ["Always rerun from source", "始终从源码重新运行"],
    ["Enable static serving", "启用静态服务"],
    ["Fast URL", "快速 URL"],
    ["Wide mode", "宽屏模式"],
    ["Screencast", "屏幕录制"],
    ["Theme", "主题"],
    ["OK", "正常"]
  ];
  function translate(root) {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    let node;
    while ((node = walker.nextNode())) {
      let v = node.nodeValue;
      if (!v || !v.trim()) continue;
      let changed = false;
      for (const [en, zh] of map) {
        if (v.includes(en)) {
          v = v.split(en).join(zh);
          changed = true;
        }
      }
      if (changed) node.nodeValue = v;
    }
  }
  const observer = new MutationObserver((muts) => {
    for (const m of muts) {
      if (m.type === "characterData") translate(m.target.parentElement);
      else if (m.addedNodes.length) translate(m.target);
    }
  });
  function start() {
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    translate(document.body);
  }
  if (document.body) start();
  else document.addEventListener("DOMContentLoaded", start);
})();
</script>
"""
st.markdown(UI_TRANSLATE_JS, unsafe_allow_html=True)


LLM_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_config.json")


def load_llm_config() -> dict:
    """读取大模型配置：本地 llm_config.json 优先，其次云端 secrets（[llm] 段）。"""
    data: dict = {}
    try:
        with open(LLM_CONFIG_FILE, encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        pass
    # 云端部署兜底：.streamlit/secrets.toml 中配置 [llm] api_key = "..."
    if not any(str(data.get(k) or "").strip() for k in ("api_key", "base_url", "model")):
        try:
            secrets = st.secrets.get("llm", {})
            if isinstance(secrets, dict):
                for k in ("api_key", "base_url", "model"):
                    if secrets.get(k) and not str(data.get(k) or "").strip():
                        data[k] = secrets[k]
        except Exception:
            pass  # 本地无 secrets 文件属正常
    return {k: str(data.get(k) or "") for k in ("api_key", "base_url", "model")}


def save_llm_config(api_key: str, base_url: str, model: str) -> None:
    with open(LLM_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"api_key": api_key, "base_url": base_url, "model": model}, f, ensure_ascii=False, indent=2)


def init_state() -> None:
    defaults = {
        "words": [],
        "sentences": [],
        "level": "middle",
        "review": None,
        "gen_fallback": [],
        "gen_llm_error": "",
        "gen_elapsed": "",  # 最近一次生成的耗时（秒），完成后填入
        "gen_result": None,  # 后台生成线程的结果容器（dict），生成中不为 None
        "gen_error": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def sidebar_settings() -> dict:
    with st.sidebar:
        st.markdown("### 设置")
        level = st.radio(
            "目标难度",
            options=["middle", "high"],
            format_func=lambda x: "初中" if x == "middle" else "高中",
            index=0 if st.session_state.level == "middle" else 1,
            help="造句时其他词汇尽量控制在对应阶段课标范围内",
        )
        st.session_state.level = level

        count = st.slider("每个单词造句数", min_value=1, max_value=10, value=1, step=1)

        st.markdown("---")
        st.markdown("### 大模型（推荐）")
        saved_llm = load_llm_config()
        has_saved = bool(saved_llm["api_key"] or saved_llm["base_url"] or saved_llm["model"])
        use_llm = st.checkbox(
            "使用大模型造句",
            value=has_saved or bool(os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")),
        )
        api_key = st.text_input(
            "API 密钥",
            value=saved_llm["api_key"] or os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY") or "",
            type="password",
            disabled=not use_llm,
        )
        base_url = st.text_input(
            "接口地址（可选）",
            value=saved_llm["base_url"] or os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL") or "",
            placeholder="如 https://open.bigmodel.cn/api/paas/v4",
            disabled=not use_llm,
            help="兼容 OpenAI 接口的服务地址，如智谱、DeepSeek、通义等；留空则使用官方默认地址",
        )
        model = st.text_input(
            "模型名",
            value=saved_llm["model"] or os.getenv("LLM_MODEL") or "gpt-4o-mini",
            disabled=not use_llm,
        )
        batch_size = st.slider(
            "每批单词数",
            min_value=10,
            max_value=50,
            value=25,
            step=5,
            disabled=not use_llm,
            help="每次大模型请求携带的单词数，多批并行发送（并发 3 路）。"
            "批数凑成 3 的倍数可让并行槽满载，总体最省时；推荐 20~25",
        )

        thinking = st.checkbox(
            "深度思考模式（high 档，更慢）",
            value=False,
            disabled=not use_llm,
            help="智谱 glm-5.3 等模型始终思考，只能调档位：默认 low 档（较快），"
            "勾选后用 high 档（质量可能更高但明显变慢）",
        )

        btn_col1, btn_col2 = st.columns(2)
        if btn_col1.button("保存配置", disabled=not use_llm, use_container_width=True):
            save_llm_config(api_key.strip(), base_url.strip(), model.strip())
            st.toast("已保存，下次启动自动填入")
        if btn_col2.button("清除", disabled=not has_saved, use_container_width=True):
            try:
                os.remove(LLM_CONFIG_FILE)
            except OSError:
                pass
            st.toast("已清除保存的配置")
        if use_llm:
            st.caption("保存后下次启动自动填入（明文存储在服务端 llm_config.json）。")
        else:
            st.caption("未启用大模型时，将使用本地模板造句（质量有限，仅供演示）。")

        st.markdown("---")
        st.caption("支持 TXT / Excel / Word / Markdown")
    return {
        "level": level,
        "count": count,
        "batch_size": batch_size,
        "use_llm": use_llm,
        "api_key": api_key.strip() or None,
        "base_url": base_url.strip() or None,
        "model": model.strip() or "gpt-4o-mini",
        "thinking": thinking,
    }


def page_import(settings: dict) -> None:
    st.markdown('<div class="main-title">英语生词造句复习器</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="sub-title">导入生词 → {level_label(settings["level"])}难度造句 → 复习掌握</div>',
        unsafe_allow_html=True,
    )

    tab_file, tab_paste, tab_sentences = st.tabs(["文件导入", "粘贴导入", "导入例句"])

    words: list[str] = []

    with tab_file:
        uploaded = st.file_uploader(
            "选择生词表文件",
            type=["txt", "csv", "xlsx", "xls", "xlsm", "docx", "md", "markdown"],
            help="Excel 支持「单词 / 英文 / word」列，也支持「abandon 放弃」「放弃 abandon」混排",
        )
        if uploaded is not None:
            try:
                words = import_vocab_file(uploaded.name, uploaded.getvalue())
                st.session_state.words = words
                if words:
                    st.success(f"已从「{uploaded.name}」解析 **{len(words)}** 个单词")
                else:
                    st.warning(
                        "文件已读取，但没有识别到英文单词。"
                        "请确认有一列英文生词（表头可为：单词 / 英文 / word），"
                        "或单元格形如 `abandon`、`abandon 放弃`。"
                    )
            except Exception as e:
                st.error(f"导入失败：{e}")

    with tab_paste:
        text = st.text_area(
            "粘贴单词（每行一个，或用逗号分隔）",
            height=160,
            placeholder="abandon\nachieve\nbenefit\n...",
        )
        if text.strip():
            words = parse_manual_input(text)
            st.session_state.words = words
            st.info(f"识别到 **{len(words)}** 个单词")

    with tab_sentences:
        st.caption(
            "导入之前导出的例句文件（Excel / JSON / Markdown），"
            "导入后**无需重新生成**，直接切换到「复习模式」即可开始。"
        )
        sent_file = st.file_uploader(
            "选择例句文件",
            type=["xlsx", "xlsm", "json", "md", "markdown"],
            key="sentences_upload",
        )
        if sent_file is not None:
            try:
                sitems = import_sentences_file(sent_file.name, sent_file.getvalue())
                if sitems:
                    st.session_state.sentences = [it.to_dict() for it in sitems]
                    st.session_state.review = None  # 旧的复习进度基于旧句子，重置
                    st.success(
                        f"已导入 **{len(sitems)}** 个例句（{len({i.word for i in sitems})} 个词）。"
                        "现在可以切换到「例句一览」或「复习模式」。"
                    )
                else:
                    st.warning(
                        "文件已读取，但没有解析到例句。请确认是本应用导出的"
                        " Excel / JSON / Markdown 例句文件。"
                    )
            except Exception as e:
                st.error(f"导入失败：{e}")

    current = st.session_state.words
    if current:
        st.markdown("#### 当前生词表")
        cols = st.columns([3, 1])
        with cols[0]:
            preview = ", ".join(current[:40])
            if len(current) > 40:
                preview += f" … 共 {len(current)} 个"
            st.write(preview)
        with cols[1]:
            st.metric("单词数", len(current))
            expected = len(current) * settings["count"]
            st.caption(f"将生成约 **{expected}** 个句子")

        generating = st.session_state.gen_result is not None
        if generating:
            st.info(
                f"正在生成例句：**{len(current)}** 个单词将按每批 {settings['batch_size']} 词"
                f"分为 {(len(current) + settings['batch_size'] - 1) // settings['batch_size']} 批并行发给大模型…\n\n"
                "生成在后台进行，**切换页面不会丢失进度**，完成后回到本页自动展示。"
            )
            if st.button("停止生成", type="secondary", use_container_width=True):
                # 标记取消并放弃结果：后台线程本次返回的造句将被丢弃，可重新开始
                st.session_state.gen_result["cancelled"] = True
                st.session_state.gen_result = None
                st.toast("已停止生成")
                st.rerun()
        elif st.button("开始生成例句", type="primary", use_container_width=True):
            st.session_state.sentences = []
            st.session_state.review = None
            st.session_state.gen_fallback = []
            st.session_state.gen_error = ""
            st.session_state.gen_llm_error = ""
            st.session_state.gen_elapsed = ""
            st.session_state.gen_result = {
                "done": False,
                "items": [],
                "fallback": [],
                "error": None,
                "cancelled": False,
                "done_words": 0,
                "chars": 0,
                "phase": "waiting",
                "reasoning_chars": 0,
                "total": len(current),
                "start_time": time.time(),
            }
            thread = threading.Thread(
                target=_run_generation_background,
                args=(st.session_state.gen_result, list(current), dict(settings)),
                daemon=True,
            )
            thread.start()
            st.rerun()
    else:
        st.info("请先导入或粘贴生词表。")

    if st.session_state.gen_result is not None:
        _generation_fragment()
    elif st.session_state.sentences:
        elapsed_text = ""
        if st.session_state.gen_elapsed != "":
            total_sec = int(round(float(st.session_state.gen_elapsed)))
            elapsed_text = (
                f"{total_sec} 秒" if total_sec < 60 else f"{total_sec // 60} 分 {total_sec % 60:02d} 秒"
            )
        st.success(
            f"已生成 **{len(st.session_state.sentences)}** 个句子"
            + (f" · 耗时 **{elapsed_text}**" if elapsed_text else "")
        )
        fallback = st.session_state.gen_fallback
        if fallback:
            st.warning(
                f"有 {len(fallback)} 个词的大模型调用失败，已用本地模板兜底："
                f"{', '.join(fallback[:10])}" + ("…" if len(fallback) > 10 else "")
            )
            if st.session_state.gen_llm_error:
                st.caption(f"失败原因：{st.session_state.gen_llm_error}")
    elif st.session_state.gen_error:
        st.error(f"生成失败：{st.session_state.gen_error}")


def _run_generation_background(holder: dict, words: list[str], settings: dict) -> None:
    """后台线程：分批并行发给大模型，结果写入 holder（随 session_state 存活）。"""
    try:
        stats: dict = {"fallback": []}

        def progress(info: dict) -> None:
            # 各字段只增不减，避免流式回调与收尾兜底互相覆盖
            if info.get("done", 0) > holder.get("done_words", 0):
                holder["done_words"] = info["done"]
            if info.get("chars", 0) > holder.get("chars", 0):
                holder["chars"] = info["chars"]
            if info.get("reasoning_chars", 0) > holder.get("reasoning_chars", 0):
                holder["reasoning_chars"] = info["reasoning_chars"]
            if info.get("phase"):
                holder["phase"] = info["phase"]

        items = generate_batch_for_words(
            words,
            settings["level"],
            settings["count"],
            use_llm=settings["use_llm"] and bool(settings["api_key"]),
            api_key=settings["api_key"],
            base_url=settings["base_url"],
            model=settings["model"],
            stats=stats,
            progress_callback=progress,
            thinking=settings.get("thinking", False),
            batch_size=int(settings.get("batch_size", 25)),
        )
        holder["items"] = items
        holder["fallback"] = stats.get("fallback", [])
        holder["llm_error"] = stats.get("llm_error", "")
    except Exception as e:
        holder["error"] = str(e)
    finally:
        holder["done"] = True


@st.fragment(run_every=1)
def _generation_fragment() -> None:
    """每秒轮询后台生成线程：进行中刷新进度条，完成后写入结果并刷新页面。

    切换页面时只是停止轮询，后台线程继续运行；回到本页后 fragment
    重新挂载并自动继续轮询，生成进度与结果不会丢失。
    """
    holder = st.session_state.gen_result
    if not holder:
        return
    if not holder["done"]:
        total = holder.get("total") or 1
        done = holder.get("done_words", 0)
        chars = holder.get("chars", 0)
        phase = holder.get("phase", "waiting")
        reasoning = holder.get("reasoning_chars", 0)
        elapsed = int(time.time() - holder.get("start_time", time.time()))
        if phase == "waiting":
            text = f"已发送请求，等待服务器首包…（已用时 {elapsed} 秒）"
        elif phase == "thinking":
            text = f"模型深度思考中…已思考 {reasoning} 字符（已用时 {elapsed} 秒）"
        else:
            text = f"已完成 {done}/{total} 个单词 · 已接收 {chars} 字符 · 已用时 {elapsed} 秒"
        st.progress(min(done / total, 1.0), text=text)
        return
    if holder.get("cancelled"):
        st.session_state.gen_result = None
        return
    if holder["error"]:
        st.session_state.gen_error = holder["error"]
    else:
        st.session_state.sentences = [it.to_dict() for it in holder["items"]]
        st.session_state.gen_fallback = holder.get("fallback", [])
        st.session_state.gen_llm_error = holder.get("llm_error", "")
        st.session_state.gen_elapsed = time.time() - holder.get("start_time", time.time())
    st.session_state.gen_result = None
    st.rerun(scope="app")


def _load_sentences() -> list[SentenceItem]:
    return [SentenceItem.from_dict(d) for d in st.session_state.sentences]


def page_sentences() -> None:
    items = _load_sentences()
    if not items:
        st.warning("还没有例句。请先在「导入与生成」页生成或导入。")
        return

    st.markdown('<div class="main-title">例句一览</div>', unsafe_allow_html=True)
    st.caption(f"共 {len(items)} 句 · 难度：{level_label(st.session_state.level)}")

    words = sorted({it.word for it in items})
    filter_word = st.selectbox("按单词筛选", ["全部"] + words)
    show = items if filter_word == "全部" else [it for it in items if it.word == filter_word]

    # 导出
    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button(
            "导出 Excel",
            data=_to_excel_bytes(items),
            file_name="vocab_sentences.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with c2:
        st.download_button(
            "导出 JSON",
            data=json.dumps(st.session_state.sentences, ensure_ascii=False, indent=2),
            file_name="vocab_sentences.json",
            mime="application/json",
        )
    with c3:
        md = _to_markdown(items)
        st.download_button(
            "导出 Markdown",
            data=md,
            file_name="vocab_sentences.md",
            mime="text/markdown",
        )

    for it in show:
        en_html = highlight_word_html(it.english, it.word)
        sense = it.sense or "（语境义）"
        st.markdown(
            f"""
            <div class="sentence-card">
              <div style="font-size:0.85rem;color:#6a7a88;margin-bottom:0.35rem;">
                <strong>{it.word}</strong> · 第 {it.index} 句
              </div>
              <div class="sentence-en">{en_html}</div>
              <div class="sentence-zh">{it.chinese}</div>
              <span class="sense-tag">本义：{sense}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _to_excel_bytes(items: list[SentenceItem]) -> bytes:
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "sentences"
    ws.append(["word", "english", "chinese", "sense", "index"])
    for it in items:
        ws.append([it.word, it.english, it.chinese, it.sense, it.index])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _to_markdown(items: list[SentenceItem]) -> str:
    lines = ["# 生词例句复习\n"]
    current = None
    for it in items:
        if it.word != current:
            current = it.word
            lines.append(f"\n## {it.word}\n")
        lines.append(f"{it.index}. **{it.english}**")
        lines.append(f"   - 译文：{it.chinese}")
        lines.append(f"   - 词义：{it.sense}\n")
    return "\n".join(lines)


def page_review() -> None:
    items = _load_sentences()
    if not items:
        st.warning("还没有例句。请先生成或导入句子后再进入复习模式。")
        return

    st.markdown('<div class="main-title">复习模式</div>', unsafe_allow_html=True)
    st.caption(
        f"共导入 **{len({it.word for it in items})}** 个单词，生成 **{len(items)}** 个句子。"
        "先看英文，再揭晓译文与词义。"
    )

    if st.session_state.review is None:
        if st.button("开始复习（打乱顺序）", type="primary", use_container_width=True):
            state = ReviewState(items=items)
            state.shuffle()
            st.session_state.review = state
            st.rerun()
        st.info("点击上方按钮进入卡片式复习。")
        return

    state: ReviewState = st.session_state.review
    # session 里可能是序列化问题；确保类型
    if not isinstance(state, ReviewState):
        state = ReviewState(items=items)
        state.shuffle()
        st.session_state.review = state

    cur = state.current()
    known_n = len(state.known)
    unk_n = len(state.unknown)
    round_total = len(state.order)

    st.markdown(
        f"""
        <span class="stat-pill">进度 {min(state.cursor + 1, round_total)} / {round_total}</span>
        <span class="stat-pill">已掌握 {known_n}</span>
        <span class="stat-pill">需再看 {unk_n}</span>
        """,
        unsafe_allow_html=True,
    )
    st.progress(state.progress)

    if cur is None:
        st.success("本轮复习结束！")
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("再来一轮（全部）", use_container_width=True):
                state.known.clear()
                state.unknown.clear()
                state.shuffle()
                st.rerun()
        with c2:
            if st.button("只复习「还需再看」", use_container_width=True, disabled=not state.unknown):
                state.restart_unknown_only()
                st.rerun()
        with c3:
            if st.button("退出复习", use_container_width=True):
                st.session_state.review = None
                st.rerun()
        return

    en_html = highlight_word_html(cur.english, cur.word)
    st.markdown(
        f"""
        <div class="review-shell">
          <div style="font-size:0.9rem;color:#5a6a78;margin-bottom:0.8rem;">
            目标词 <strong>{cur.word}</strong> · 第 {cur.index} 句
          </div>
          <div class="sentence-en" style="font-size:1.45rem;">{en_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not state.show_translation:
        if st.button("显示译文与词义", type="primary", use_container_width=True):
            state.reveal()
            st.rerun()
    else:
        st.markdown(
            f"""
            <div class="sentence-card">
              <div class="sentence-zh" style="font-size:1.1rem;">{cur.chinese}</div>
              <span class="sense-tag">本义：{cur.sense or "（语境义）"}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        b1, b2 = st.columns(2)
        with b1:
            if st.button("认识 ✓", type="primary", use_container_width=True):
                state.mark_known()
                st.rerun()
        with b2:
            if st.button("还需再看", use_container_width=True):
                state.mark_unknown()
                st.rerun()
        b3, b4 = st.columns(2)
        with b3:
            if st.button("上一句", use_container_width=True):
                state.back()
                st.rerun()
        with b4:
            if st.button("跳过", use_container_width=True):
                state.skip()
                st.rerun()

    with st.expander("复习控制"):
        if st.button("重新打乱"):
            state.known.clear()
            state.unknown.clear()
            state.shuffle()
            st.rerun()
        if st.button("结束复习"):
            st.session_state.review = None
            st.rerun()


def main() -> None:
    init_state()
    settings = sidebar_settings()

    page = st.radio(
        "导航",
        ["导入与生成", "例句一览", "复习模式"],
        horizontal=True,
        label_visibility="collapsed",
    )

    if page == "导入与生成":
        page_import(settings)
    elif page == "例句一览":
        page_sentences()
    else:
        page_review()


if __name__ == "__main__":
    main()

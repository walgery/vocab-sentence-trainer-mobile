"""为生词生成例句：优先使用大模型，无密钥时使用本地模板引擎。"""

from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

from .vocab_scope import get_allowed_vocab, level_label


@dataclass
class SentenceItem:
    word: str
    english: str
    chinese: str
    sense: str  # 该句中目标词的中文释义（用于高亮）
    index: int  # 该词第几句（1-10）

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "SentenceItem":
        return SentenceItem(
            word=d["word"],
            english=d["english"],
            chinese=d["chinese"],
            sense=d.get("sense", ""),
            index=int(d.get("index", 1)),
        )


def _extract_json_array(text: str) -> list[dict]:
    text = text.strip()
    # 去掉 markdown 代码围栏
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "sentences" in data:
            return data["sentences"]
    except json.JSONDecodeError:
        pass
    # 尝试截取第一个 [...] 数组
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    # 兜底：逐个提取 {...} 对象（容忍输出被截断、数组不完整）
    rows: list[dict] = []
    for obj in re.findall(r"\{[^{}]*\}", text):
        try:
            row = json.loads(obj)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    if rows:
        return rows
    raise ValueError("模型返回无法解析为 JSON")


def _build_batch_prompt(words: list[str], level: str, count: int) -> str:
    label = level_label(level)
    word_list = "、".join(words)
    return f"""你是一名中国{label}英语老师。请为下列 {len(words)} 个英语单词各造 {count} 个不同的英文例句：{word_list}

硬性要求：
1. 每个句子必须包含所属目标词（可用合理词形变化：第三人称、过去式、现在分词、复数等）。
2. 句子中的其他词汇尽量控制在中国{label}英语课标词汇范围内，不要出现超纲生词。
3. 句式难度符合{label}水平；语义自然、场景多样（学校、家庭、运动、旅行、科技日常等）。
4. 为每个句子提供准确的中文翻译。
5. 指出该句中目标词的具体中文含义（简短，如「坚持」「认为」），方便学生理解一词多义。

只输出 JSON 数组，不要其他说明。每个单词一个对象，格式：
[
  {{"word": "{words[0]}", "sentences": [{{"english": "...", "chinese": "...", "sense": "..."}}, ...]}},
  {{"word": "{words[-1]}", "sentences": [{{"english": "...", "chinese": "...", "sense": "..."}}, ...]}}
]
"""


def _make_stream_progress_counter(on_complete) -> "callable":
    """流式输出中逐字符扫描 JSON，每完成一个单词对象（顶层 {...}）就回调一次。"""
    state = {"depth": 0, "in_str": False, "esc": False}
    buf: list[str] = []

    def feed(text: str) -> None:
        for ch in text:
            if state["in_str"]:
                if state["esc"]:
                    state["esc"] = False
                elif ch == "\\":
                    state["esc"] = True
                elif ch == '"':
                    state["in_str"] = False
                if state["depth"] > 0:
                    buf.append(ch)
                continue
            if ch == '"':
                state["in_str"] = True
                if state["depth"] > 0:
                    buf.append(ch)
            elif ch == "{":
                state["depth"] += 1
                buf.append(ch)
            elif ch == "}":
                if state["depth"] > 0:
                    buf.append(ch)
                    state["depth"] -= 1
                    if state["depth"] == 0:
                        obj_text = "".join(buf).strip()
                        buf.clear()
                        try:
                            obj = json.loads(obj_text)
                        except json.JSONDecodeError:
                            obj = None
                        if isinstance(obj, dict) and obj.get("word"):
                            on_complete()
            elif state["depth"] > 0:
                buf.append(ch)

    return feed


def generate_with_llm_batch(
    words: list[str],
    level: str,
    count: int = 10,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    progress_callback=None,
    stream: bool = True,
    thinking: bool = False,
) -> dict[str, list[SentenceItem]]:
    """一次 LLM 调用为一组单词造句，返回 {word: [SentenceItem]}。

    模型漏掉或返回不足的单词不由本函数补齐，由调用方统一兜底。
    progress_callback(info)：流式过程中持续回调，info 为 dict：
      {"done": 已完成词数, "chars": 已接收正文字符数,
       "phase": waiting/thinking/generating, "reasoning_chars": 思考字符数}
    thinking=True 时对智谱等支持的服务端开启深度思考（更慢）。
    """
    from openai import OpenAI

    api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("未配置 API Key")

    base_url = base_url or os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL")
    model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")

    client_kwargs: dict = {"api_key": api_key, "timeout": 300.0}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    messages = [
        {
            "role": "system",
            "content": "你是严谨的英语教学助手，只输出合法 JSON。",
        },
        {"role": "user", "content": _build_batch_prompt(words, level, count)},
    ]

    # 智谱 GLM 系列支持思考强度档位；glm-5.3 等模型始终思考、不支持 disabled，
    # 只能选 effort 档位（low / high / max）。低档获得更快首字响应。
    extra_body: dict | None = None
    if base_url and "bigmodel" in base_url:
        effort = "high" if thinking else "low"
        extra_body = {"thinking": {"type": "enabled", "effort": effort}}

    completed = 0

    def report(info: dict) -> None:
        if progress_callback:
            try:
                progress_callback(info)
            except Exception:
                pass

    def on_obj_complete() -> None:
        nonlocal completed
        completed += 1

    if stream:
        feed = _make_stream_progress_counter(on_obj_complete)
        stream_resp = client.chat.completions.create(
            model=model,
            temperature=0.7,
            messages=messages,
            stream=True,
            extra_body=extra_body,
        )
        parts: list[str] = []
        chars = 0
        reasoning_chars = 0
        report({"done": 0, "chars": 0, "phase": "waiting", "reasoning_chars": 0})
        for chunk in stream_resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None) if delta else None
            if reasoning:
                reasoning_chars += len(reasoning)
                report({"done": completed, "chars": chars, "phase": "thinking", "reasoning_chars": reasoning_chars})
                continue
            delta_text = getattr(delta, "content", None) if delta else None
            if delta_text:
                parts.append(delta_text)
                chars += len(delta_text)
                feed(delta_text)
                report({"done": completed, "chars": chars, "phase": "generating", "reasoning_chars": reasoning_chars})
        content = "".join(parts) or "[]"
    else:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.7,
            messages=messages,
            extra_body=extra_body,
        )
        content = resp.choices[0].message.content or "[]"
    rows = _extract_json_array(content)
    lower_map = {w.lower(): w for w in words}
    by_word: dict[str, list[SentenceItem]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        target = lower_map.get(str(row.get("word", "")).strip().lower())
        if target is None:
            continue
        sentences = row.get("sentences")
        if not isinstance(sentences, list):
            continue
        items: list[SentenceItem] = []
        for i, s in enumerate(sentences[:count], start=1):
            if not isinstance(s, dict):
                continue
            english = str(s.get("english", "")).strip()
            if not english:
                continue
            items.append(
                SentenceItem(
                    word=target,
                    english=english,
                    chinese=str(s.get("chinese", "")).strip(),
                    sense=str(s.get("sense", "")).strip(),
                    index=len(items) + 1,
                )
            )
        if items:
            by_word[target] = items
    return by_word


# —— 离线模板引擎（无 API 时可用；质量有限，正式学习请启用大模型）——

_TEMPLATES_MIDDLE = [
    ("I learned the word '{w}' carefully in today's English class.", "我在今天的英语课上仔细学习了单词「{w}」。", "学习"),
    ("Can you use '{w}' in a short sentence for me?", "你能用「{w}」给我造一个短句吗？", "使用"),
    ("She wrote '{w}' on the blackboard and explained it.", "她把「{w}」写在黑板上并做了讲解。", "书写/展示"),
    ("We practice '{w}' every morning before school starts.", "我们每天早上在上课前练习「{w}」。", "练习"),
    ("My friend asked me the meaning of '{w}' yesterday.", "昨天我朋友问我「{w}」的意思。", "含义"),
    ("Please remember how to spell '{w}' in the test.", "请记住考试中如何拼写「{w}」。", "拼写"),
    ("This story helps students understand '{w}' better.", "这个故事帮助学生更好地理解「{w}」。", "理解"),
    ("He looked up '{w}' in his dictionary after class.", "放学后他在词典里查阅了「{w}」。", "查阅"),
    ("Our teacher gave us five sentences with '{w}'.", "老师给了我们五个含有「{w}」的句子。", "包含"),
    ("I will review '{w}' with my classmates tomorrow.", "明天我会和同学一起复习「{w}」。", "复习"),
]

_TEMPLATES_HIGH = [
    ("A clear example can show how to use '{w}' in context.", "清晰的例子能说明如何在语境中使用「{w}」。", "使用"),
    ("Students should notice the exact meaning of '{w}' here.", "学生应注意此处「{w}」的准确含义。", "含义"),
    ("The passage explains why '{w}' matters in daily communication.", "这篇短文解释了「{w}」在日常交流中为何重要。", "重要性"),
    ("Before the exam, she made notes about '{w}' and related forms.", "考前她整理了「{w}」及相关词形的笔记。", "相关词形"),
    ("Teachers often ask learners to compare '{w}' with similar words.", "老师常要求学习者把「{w}」与近义词比较。", "比较"),
    ("Reading widely helps you remember when to choose '{w}'.", "广泛阅读有助于你记住何时选用「{w}」。", "选用"),
    ("He tried to express the idea without overusing '{w}'.", "他试图表达这个意思，同时不过度使用「{w}」。", "表达"),
    ("A good dictionary entry for '{w}' includes examples and notes.", "词典中「{w}」的优质词条会包含例句与注释。", "词条"),
    ("Group discussion can deepen understanding of '{w}'.", "小组讨论能加深对「{w}」的理解。", "理解"),
    ("She checked whether '{w}' fitted the formal writing style.", "她检查了「{w}」是否适合正式写作风格。", "适配"),
]


def generate_offline(word: str, level: str, count: int = 10) -> list[SentenceItem]:
    templates = _TEMPLATES_MIDDLE if level == "middle" else _TEMPLATES_HIGH
    items: list[SentenceItem] = []
    for i in range(count):
        eng_t, zh_t, sense = templates[i % len(templates)]
        english = eng_t.format(w=word)
        chinese = zh_t.format(w=word)
        items.append(
            SentenceItem(word=word, english=english, chinese=chinese, sense=sense, index=i + 1)
        )
    return items


def generate_batch_for_words(
    words: list[str],
    level: str,
    count: int = 10,
    *,
    use_llm: bool = True,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    stats: dict | None = None,
    progress_callback=None,
    thinking: bool = False,
    batch_size: int = 25,
    max_concurrency: int = 3,
) -> list[SentenceItem]:
    """为一组单词生成例句：切成多批并行请求大模型，失败或缺失的词回退离线模板。

    progress_callback(info)：info 为 dict，含 done / phase（可选 chars / reasoning_chars）。
    batch_size：每批单词数；max_concurrency：并行请求数上限。
    """
    if not words:
        return []
    if not use_llm:
        result: list[SentenceItem] = []
        for i, w in enumerate(words, start=1):
            result.extend(generate_offline(w, level, count))
            if progress_callback:
                try:
                    progress_callback({"done": i, "phase": "generating"})
                except Exception:
                    pass
        return result

    batch_size = max(1, int(batch_size))
    batches = [words[i : i + batch_size] for i in range(0, len(words), batch_size)]
    by_word: dict[str, list[SentenceItem]] = {}
    errors: list[str] = []
    lock = threading.Lock()
    done_counter = {"n": 0}

    def report_done() -> None:
        if progress_callback:
            try:
                progress_callback({"done": done_counter["n"], "phase": "generating"})
            except Exception:
                pass

    def run_batch(batch: list[str]) -> None:
        try:
            res = generate_with_llm_batch(
                batch,
                level,
                count,
                api_key=api_key,
                base_url=base_url,
                model=model,
                progress_callback=progress_callback,
                thinking=thinking,
            )
        except Exception as e:
            with lock:
                errors.append(f"{type(e).__name__}: {e}")
                if stats is not None:
                    stats.setdefault("fallback", []).extend(batch)
                done_counter["n"] += len(batch)
                report_done()
            return
        with lock:
            by_word.update(res)
            if stats is not None:
                missed = [w for w in batch if w not in res]
                stats.setdefault("fallback", []).extend(missed)
            done_counter["n"] += len(batch)
            report_done()

    if len(batches) == 1:
        run_batch(batches[0])
    else:
        with ThreadPoolExecutor(max_workers=min(max(1, max_concurrency), len(batches))) as ex:
            list(ex.map(run_batch, batches))

    if stats is not None and errors:
        stats["llm_error"] = "；".join(dict.fromkeys(errors))

    items: list[SentenceItem] = []
    for i, w in enumerate(words, start=1):
        got = by_word.get(w)
        if not got:
            # 模型漏掉或整批失败的词：回退离线
            got = generate_offline(w, level, count)
        elif len(got) < count:
            # 句数不足：用本地模板补齐
            extras = generate_offline(w, level, count - len(got))
            for ex in extras:
                ex.index = len(got) + 1
                got.append(ex)
        items.extend(got)
        if progress_callback:
            try:
                progress_callback({"done": i, "phase": "generating"})
            except Exception:
                pass
    return items


def _word_variants(word: str) -> list[str]:
    """生成目标词的常见词形变体，按长度降序以优先匹配更长词形。"""
    w = word.lower()
    variants = {w}
    if len(w) >= 2:  # 单字母词（a / i）不加词形变体，避免误中 as、ad 等
        variants.add(w + "s")
        if w.endswith("e"):
            variants.update({w + "d", w[:-1] + "ing"})  # use -> used / using
        else:
            variants.update({w + "ed", w + "ing"})
            if w.endswith(("s", "x", "z", "ch", "sh", "o")):
                variants.add(w + "es")  # box -> boxes / go -> goes
        if w.endswith("y") and len(w) >= 3 and w[-2] not in "aeiou":
            variants.add(w[:-1] + "ies")  # study -> studies
    return sorted(variants, key=len, reverse=True)


def highlight_word_html(sentence: str, word: str) -> str:
    """在英文句中高亮目标词（含常见词形）。"""
    import html as html_mod

    pattern = re.compile(
        r"\b(" + "|".join(re.escape(v) for v in _word_variants(word)) + r")\b",
        re.IGNORECASE,
    )
    parts: list[str] = []
    last = 0
    for m in pattern.finditer(sentence):
        parts.append(html_mod.escape(sentence[last:m.start()]))
        parts.append(f'<mark class="target-word">{html_mod.escape(m.group(0))}</mark>')
        last = m.end()
    parts.append(html_mod.escape(sentence[last:]))
    return "".join(parts)


def out_of_scope_tokens(sentence: str, level: str) -> list[str]:
    """粗略检查句中可能超纲的词（供参考，不阻断）。"""
    allowed = get_allowed_vocab(level)
    tokens = re.findall(r"[A-Za-z']+", sentence.lower())
    stop = {"mr", "mrs", "ms", "dr"}
    bad = []
    for t in tokens:
        if t in stop or "'" in t:
            continue
        if t not in allowed and t.rstrip("s") not in allowed and t.rstrip("ed") not in allowed:
            # 忽略很短功能词已在词表中；这里只收集明显外来词
            if len(t) > 3:
                bad.append(t)
    return sorted(set(bad))

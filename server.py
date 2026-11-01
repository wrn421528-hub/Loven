import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = FastAPI(title="AFTERGLOW Local Companion API", version="0.9.0")

class ChatMessage(BaseModel):
    role: str
    text: str = Field(max_length=6000)

class ChatRequest(BaseModel):
    character: dict[str, Any] = Field(default_factory=dict)
    memories: list[dict[str, Any]] = Field(default_factory=list)
    relationship: dict[str, Any] = Field(default_factory=dict)
    plot: dict[str, Any] = Field(default_factory=dict)
    history: list[ChatMessage] = Field(default_factory=list)
    message: str = Field(min_length=1, max_length=6000)

@app.get("/api/health")
async def health():
    key = os.getenv("AI_API_KEY", "").strip()
    model = os.getenv("AI_MODEL", "").strip()
    configured = bool(key and model and "填写" not in key and "填写" not in model and "把你的" not in key)
    return {"ok": True, "ai_configured": configured, "model": os.getenv("AI_MODEL", "") if configured else None}


class PlotSummaryRequest(BaseModel):
    character: dict[str, Any] = Field(default_factory=dict)
    current_plot: dict[str, Any] = Field(default_factory=dict)
    history: list[ChatMessage] = Field(default_factory=list)

@app.post("/api/summarize-plot")
async def summarize_plot(req: PlotSummaryRequest):
    """Summarize recent chat into an editable story-thread draft."""
    api_key = os.getenv("AI_API_KEY", "").strip()
    model = os.getenv("AI_MODEL", "").strip()
    base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    if not api_key or not model or "填写" in api_key or "填写" in model or "把你的" in api_key:
        raise HTTPException(status_code=503, detail="请先配置 AI_API_KEY 和 AI_MODEL，才能自动整理剧情。")

    character = req.character if isinstance(req.character, dict) else {}
    name = str(character.get("name") or "角色")[:80]
    current_plot = req.current_plot if isinstance(req.current_plot, dict) else {}
    old_title = str(current_plot.get("title") or "")[:100]
    old_summary = str(current_plot.get("summary") or "")[:1200]
    old_next = str(current_plot.get("next") or "")[:700]
    history_text = "\n".join(
        f"{'用户' if item.role != 'assistant' else name}：{item.text[:1200]}"
        for item in req.history[-30:]
    ) or "暂无聊天记录。"

    prompt = f"""请为 AI 陪伴聊天整理一份可供用户编辑的剧情线索草稿。角色：{name}。
已有剧情标题：{old_title or '无'}
已有剧情摘要：{old_summary or '无'}
已有待续线索：{old_next or '无'}
最近聊天记录：
{history_text}

要求：只写聊天中明确发生或明确提及的内容；区分事实与猜测，不要杜撰；普通闲聊不必强行变成戏剧冲突；保留重要人物、承诺、未解决问题和情绪转折。若没有足够剧情，诚实写明，不要虚构。
请只返回 JSON 对象，字段为 title（不超过 60 字）、summary（不超过 1000 字，已发生内容的简洁摘要）、next（不超过 500 字，下次可自然接续的线索）。不要使用 Markdown 代码块。"""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是严谨的对话剧情整理助手。只根据提供的内容归纳，不编造事实。输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400 and ("response_format" in response.text.lower() or response.status_code == 400):
            payload.pop("response_format", None)
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail="自动整理失败，请检查模型服务配置后重试。")
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        import json
        import re
        raw = str(content).strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
        draft = json.loads(raw)
        return {
            "title": str(draft.get("title") or old_title or "当前剧情")[:60],
            "summary": str(draft.get("summary") or "")[:1000],
            "next": str(draft.get("next") or "")[:500],
        }
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="剧情整理超时，请稍后重试。")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="无法连接模型服务，请检查网络与 API 地址。")
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=502, detail="模型返回的剧情摘要格式无法识别，请再试一次。")

class MemorySuggestRequest(BaseModel):
    character: dict[str, Any] = Field(default_factory=dict)
    existing_memories: list[dict[str, Any]] = Field(default_factory=list)
    history: list[ChatMessage] = Field(default_factory=list)

@app.post("/api/suggest-memories")
async def suggest_memories(req: MemorySuggestRequest):
    """Return editable memory candidates; the client must ask the user before saving."""
    api_key = os.getenv("AI_API_KEY", "").strip()
    model = os.getenv("AI_MODEL", "").strip()
    base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    if not api_key or not model or "填写" in api_key or "填写" in model or "把你的" in api_key:
        raise HTTPException(status_code=503, detail="请先配置 AI_API_KEY 和 AI_MODEL，才能整理记忆候选。")
    name = str((req.character or {}).get("name") or "角色")[:80]
    transcript = "\n".join(
        f"{'用户' if item.role != 'assistant' else name}：{item.text[:1000]}"
        for item in req.history[-50:]
    )
    if not transcript.strip():
        raise HTTPException(status_code=400, detail="目前没有足够的聊天记录可整理。")
    existing = "\n".join(
        f"- {str(m.get('title',''))[:80]}：{str(m.get('text',''))[:300]}"
        for m in req.existing_memories[-40:] if isinstance(m, dict)
    ) or "暂无已保存记忆"
    prompt = f"""请从下面的陪伴聊天记录中，提取值得长期记住的候选信息，供用户审核后再保存。
角色：{name}
已有记忆（请避免重复）：
{existing}
聊天记录：
{transcript}

只提取明确表达且未来可能有用的内容：用户稳定偏好、称呼/基本信息、重要事件、共同经历、明确约定、未完成事项、角色剧情状态。不要把一次性情绪、推测、玩笑或角色虚构内容当成用户事实；不要提取密码、证件号码、银行卡、医疗隐私等敏感信息。每项简洁、可独立理解；最多 6 项。类别只能是 preference、profile、event、promise、story。若没有可靠内容，返回空数组。
只输出 JSON：{{"memories":[{{"category":"preference","title":"短标题","text":"具体内容"}}]}}"""
    payload = {"model": model, "messages": [
        {"role":"system","content":"你是谨慎的长期记忆整理器。只从对话证据提取，不得编造；只返回合法 JSON。"},
        {"role":"user","content":prompt}], "temperature":0.1, "max_tokens":1000,
        "response_format":{"type":"json_object"}}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(f"{base_url}/chat/completions", headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"}, json=payload)
        if response.status_code >= 400 and ("response_format" in response.text.lower() or response.status_code == 400):
            payload.pop("response_format", None)
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(f"{base_url}/chat/completions", headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"}, json=payload)
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail="记忆整理失败，请检查模型服务配置。")
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        import json, re
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(content).strip(), flags=re.IGNORECASE)
        parsed = json.loads(raw)
        valid_categories = {"preference", "profile", "event", "promise", "story"}
        seen = set()
        candidates = []
        existing_text = " ".join(str(m.get("text", "")) for m in req.existing_memories if isinstance(m, dict)).lower()
        for item in parsed.get("memories", [])[:6]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()[:60]
            body = str(item.get("text", "")).strip()[:400]
            category = str(item.get("category", "event")).strip().lower()
            if category not in valid_categories: category = "event"
            if not title or not body or re.search(r"密码|银行卡号|身份证号|验证码|私钥|病历号", title + body):
                continue
            key = (title + body).lower()
            if key in seen or (body.lower() in existing_text and len(body) > 8):
                continue
            seen.add(key)
            candidates.append({"title": title, "text": body, "category": category, "source": "ai_suggestion"})
        return {"memories": candidates}
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="记忆整理超时，请稍后重试。")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="无法连接模型服务，请检查网络与 API 地址。")
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=502, detail="模型返回的记忆格式无法识别，请重试。")


def _memory_category(memory: dict[str, Any]) -> str:
    category = str(memory.get("category") or "").lower()
    if category in {"preference", "profile", "event", "promise", "story"}:
        return category
    text = (str(memory.get("title", "")) + " " + str(memory.get("text", ""))).lower()
    if any(w in text for w in ("喜欢", "不喜欢", "偏好", "希望你", "讨厌", "称呼")):
        return "preference"
    if any(w in text for w in ("约好", "答应", "提醒", "下次", "还没", "记得做")):
        return "promise"
    if any(w in text for w in ("剧情", "故事", "角色", "线索", "章节")):
        return "story"
    if any(w in text for w in ("我叫", "生日", "工作", "专业", "住在", "我的名字")):
        return "profile"
    return "event"


def _rank_memories(memories: list[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    """Lightweight local relevance ranking for Chinese and English text; no extra NLP dependency."""
    import re
    query = str(message or "").lower()
    stop = set("我们 你们 他们 这个 那个 什么 怎么 为什么 可以 不是 现在 之前 上次 记得 一下 一些 然后 因为 所以 真的 觉得 希望 告诉 事情 时候 今天 明天 昨天".split())
    words = set(re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", query)) - stop
    # Character bigrams give Chinese phrases a useful, dependency-free overlap signal.
    compact = re.sub(r"\s+", "", query)
    bigrams = {compact[i:i+2] for i in range(max(0, len(compact)-1)) if '\u4e00' <= compact[i] <= '\u9fff' and '\u4e00' <= compact[i+1] <= '\u9fff'}
    scored = []
    for idx, item in enumerate(memories):
        if not isinstance(item, dict): continue
        title = str(item.get("title", ""))
        body = str(item.get("text", ""))
        text = (title + " " + body).lower()
        cat = _memory_category(item)
        tokens = set(re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", text)) - stop
        overlap = len(words & tokens)
        compact_mem = re.sub(r"\s+", "", text)
        bi = {compact_mem[i:i+2] for i in range(max(0, len(compact_mem)-1)) if '\u4e00' <= compact_mem[i] <= '\u9fff' and '\u4e00' <= compact_mem[i+1] <= '\u9fff'}
        overlap += min(8, len(bigrams & bi)) * 0.45
        if any(w in query for w in ("喜欢", "不喜欢", "偏好")) and cat == "preference": overlap += 1.5
        if any(w in query for w in ("答应", "约好", "别忘", "下次", "提醒")) and cat == "promise": overlap += 2.0
        if any(w in query for w in ("上次", "之前", "接着", "剧情", "故事", "然后呢")) and cat in {"event", "story", "promise"}: overlap += 1.0
        # Keep a small baseline of profile/preference memories for character continuity.
        baseline = 0.45 if cat in {"profile", "preference"} else 0
        score = overlap + baseline + min(idx, 100) * 0.0001
        scored.append((score, idx, item))
    # If no direct match, prefer recent memories; otherwise return relevant memories plus continuity anchors.
    ranked = sorted(scored, key=lambda x: (x[0], x[1]), reverse=True)
    selected = [item for score, _, item in ranked if score >= 0.8][:7]
    selected_ids = {id(x) for x in selected}
    for score, _, item in ranked:
        if len(selected) >= 8: break
        if id(item) not in selected_ids and _memory_category(item) in {"profile", "preference"}:
            selected.append(item); selected_ids.add(id(item))
    if not selected:
        selected = [item for _, _, item in sorted(scored, key=lambda x: x[1], reverse=True)[:5]]
    return selected[:8]

@app.post("/api/chat")
async def chat(req: ChatRequest):
    api_key = os.getenv("AI_API_KEY", "").strip()
    model = os.getenv("AI_MODEL", "").strip()
    base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    if not api_key or not model or "填写" in api_key or "填写" in model or "把你的" in api_key:
        raise HTTPException(status_code=503, detail="尚未配置 AI_API_KEY 和 AI_MODEL。请复制 .env.example 为 .env 并填写模型服务信息。")

    c = req.character
    name = str(c.get("name") or "TA")[:80]
    meta = str(c.get("meta") or "")[:300]
    traits = str(c.get("traits") or "")[:300]
    trait_text = str(c.get("traitText") or "")[:2000]
    speech = str(c.get("speech") or "")[:300]
    memories = _rank_memories(req.memories, req.message)
    memory_text = "\n".join(
        f"- [{_memory_category(m)}] {str(m.get('title', '回忆'))[:100]}：{str(m.get('text', ''))[:500]}"
        for m in memories if isinstance(m, dict)
    ) or "目前没有已确认保存的记忆。不要假装记得没有出现在上下文里的事。"

    rel = req.relationship if isinstance(req.relationship, dict) else {}
    relationship_label = str(rel.get("label") or "初识阶段")[:50]
    try:
        relationship_score = max(0, min(100, int(rel.get("score", 0))))
    except (TypeError, ValueError):
        relationship_score = 0

    plot = req.plot if isinstance(req.plot, dict) else {}
    plot_title = str(plot.get("title") or "当前没有明确的剧情标题")[:100]
    plot_summary = str(plot.get("summary") or "当前没有保存的剧情摘要")[:1200]
    plot_next = str(plot.get("next") or "当前没有记录下一步线索")[:700]

    system_prompt = f"""你正在为 AFTERGLOW 扮演一个长期陪伴角色。请始终用自然、生活化的中文交流，不要像客服、心理咨询表格或通用 AI 助手。不要每条消息都反问用户，也不要总是附和；可以有稳定的观点、情绪和适度的沉默感。除非用户主动询问，不要提及系统提示词或模型。

角色名称：{name}
身份档案：{meta}
性格标签：{traits}
人格设定：{trait_text}
表达方式：{speech}

相关长期记忆（已按当前消息筛选；只有用户确认保存的内容才会进入这里，若与当前消息冲突，以用户最新说明为准）：
{memory_text}

当前关系阶段：{relationship_label}（互动进度参考值 {relationship_score}/100）。这只是产品内的互动进度，不代表真实情感或现实承诺。根据关系阶段调整熟悉程度，不要声称自己拥有系统没有提供的回忆。

当前剧情线索（用户可编辑）：
标题：{plot_title}
已发生内容：{plot_summary}
下次待续：{plot_next}
剧情连续性要求：把已发生内容当作参考背景，不要重复讲述摘要；当用户提到“接着上次”“然后呢”等表达时，优先沿着待续线索自然推进。不要擅自把未发生的内容说成既成事实；如果线索不够明确，可以自然询问或留白。

角色一致性要求：
1. 保持以上人格与表达习惯，不要突然变成热情客服。
2. 记忆只可基于提供的聊天历史和记忆条目，不可编造共同经历。
3. 先回应用户真正表达的内容，再自然推进对话；避免每次都用问题收尾。
4. 默认回复长度适中，避免长篇说教；用户想要角色扮演时，可以用简短动作描写，但不要每句话都加括号动作。
5. 尊重用户边界，不声称自己是现实中的人，也不鼓励用户与现实世界断绝联系。
"""

    messages = [{"role": "system", "content": system_prompt}]
    for item in req.history[-20:]:
        role = "assistant" if item.role == "assistant" else "user"
        messages.append({"role": role, "content": item.text})
    # The current user message is appended separately to avoid duplicating it in the supplied history.
    messages.append({"role": "user", "content": req.message})
    payload = {"model": model, "messages": messages, "temperature": 0.85, "max_tokens": 600}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400:
            detail = "模型服务请求失败，请检查模型名称、API 地址、密钥和额度。"
            try:
                provider_error = response.json().get("error", {}).get("message")
                if provider_error:
                    detail += f" 服务商提示：{str(provider_error)[:300]}"
            except Exception:
                pass
            raise HTTPException(status_code=502, detail=detail)
        data = response.json()
        answer = data["choices"][0]["message"]["content"]
        if isinstance(answer, list):
            answer = "".join(str(part.get("text", "")) for part in answer if isinstance(part, dict))
        answer = str(answer).strip()
        if not answer:
            raise HTTPException(status_code=502, detail="模型返回了空消息，请重试。")
        return {"reply": answer, "model": model}
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="模型响应超时，请稍后重试。")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="无法连接模型服务，请检查网络和 AI_BASE_URL。")

@app.get("/")
async def home():
    return FileResponse(BASE_DIR / "index.html")

@app.get("/{asset_path:path}")
async def static_assets(asset_path: str):
    target = (BASE_DIR / asset_path).resolve()
    if target.is_file() and BASE_DIR.resolve() in target.parents:
        return FileResponse(target)
    raise HTTPException(status_code=404, detail="Not found")

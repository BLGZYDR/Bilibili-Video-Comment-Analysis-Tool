"""DeepSeek API 评论分析模块 — 支持大批量评论的批次分析+合成"""

import json
import os
import time
import requests
from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_API_URL,
    DEEPSEEK_MODEL,
    MAX_ANALYSIS_COMMENTS,
    BATCH_SIZE,
    MAX_BATCH_RETRIES,
    BATCH_MAX_TOKENS,
    SYNTHESIS_MAX_TOKENS,
    OUTPUT_DIR,
)


def _format_comments_block(comments: list[dict]) -> str:
    """将评论列表格式化为分析用的文本块，包含子回复"""
    lines = []
    for i, c in enumerate(comments, 1):
        username = c.get("username", "匿名")
        content = c.get("content", "").replace("\n", " ")
        likes = c.get("like", 0)
        replies_count = c.get("replies_count", 0)
        lines.append(f"{i}. [{username}] (点赞:{likes}, 回复数:{replies_count}): {content}")
        for sub in c.get("sub_replies", []):
            sub_name = sub.get("username", "匿名")
            sub_content = sub.get("content", "").replace("\n", " ")
            sub_likes = sub.get("like", 0)
            lines.append(f"   ↳ [{sub_name}] (点赞:{sub_likes}): {sub_content}")
    return "\n".join(lines)


def _build_batch_prompt(comments: list[dict], video_title: str,
                        batch_num: int, total_batches: int) -> str:
    """构建批次分析 Prompt（返回观点+出现次数，非百分比）"""
    comments_block = _format_comments_block(comments)

    return f"""你是一个专业的社交媒体舆情分析师。请分析以下B站视频评论区的第{batch_num}/{total_batches}批次评论数据。

【视频标题】{video_title}
【当前批次】{batch_num}/{total_batches}，共 {len(comments)} 条主评论（含子回复）

【评论内容】
{comments_block}

说明：每条主评论下方以"↳"缩进显示的是其子回复（楼中楼）。子回复是其他用户对该主评论的直接回应，可能包含补充、反驳、延伸或无关内容。请将主评论及其子回复作为一个讨论线程综合分析，从整个对话线程中提炼观点，而非仅看主评论。

请从以下维度分析本批次评论，并以JSON格式返回结果：

1. **atmosphere**: 本批次评论区的整体氛围（可以从以下选择：友好讨论 / 激烈争论 / 理性分析为主 / 情绪化表达为主 / 一边倒支持 / 一边倒反对 / 中立观望为主 / 混乱无序）

2. **atmosphere_desc**: 对本批次氛围的简要描述（50-100字）

3. **viewpoints**: 数组，列出本批次评论中的主要观点，每个观点包含：
   - viewpoint: 观点名称（简洁概括，10字以内）
   - count: 该观点在本批次中出现的估计次数（整数，不是百分比，需综合考虑主评论和子回复中的立场）
   - description: 该观点的简要说明

4. **key_themes**: 本批次的关键主题词/短语列表（用于后续跨批次合并，5-10个）

请直接返回JSON，不要包含markdown代码块标记。"""


def _build_synthesis_prompt(batch_results: list[dict], video_title: str,
                            total_comments: int, failed_count: int) -> str:
    """构建合成分析 Prompt（合并所有批次结果）"""
    batches_json = json.dumps(batch_results, ensure_ascii=False, indent=2)

    return f"""你是一个专业的社交媒体舆情分析师。你已经分析了一个B站视频评论区的多个批次，现在请将所有批次的分析结果合成为一个整体分析报告。

【视频标题】{video_title}
【总评论数】{total_comments} 条
【成功分析批次数】{len(batch_results)} 个
【失败批次数】{failed_count} 个

【各批次分析结果】
{batches_json}

请综合所有批次的信息，完成以下任务：

1. **合并相似观点**：不同批次中语义相似的观点应合并为一个（如 "赞同UP主" 和 "UP主说得对" 应合并），将各批次的 count 求和后，转换为百分比（总和应为100%）。

2. **overall_atmosphere**: 综合各批次，判断评论区整体氛围。

3. **atmosphere_description**: 对整体氛围的详细描述（100-200字）。

4. **viewpoints**: 合并后的主要观点列表，每个观点包含：
   - viewpoint: 观点名称
   - percentage: 占总评论的百分比（整数，所有观点总和为100%）
   - description: 该观点的综合说明

5. **public_opinion_direction**: 视频的舆论导向分析，包括评论区是否被引导向某个特定方向、UP主对评论区的干预程度判断、是否存在明显的控评或水军迹象。

6. **summary**: 总结（150-300字），概括本次分析的要点。

请直接返回JSON，不要包含markdown代码块标记。"""


def _build_analysis_prompt(comments: list[dict], video_title: str) -> str:
    """构建单批次分析 Prompt（评论数 ≤ BATCH_SIZE 时使用，保留原格式）"""
    comments_block = _format_comments_block(comments)

    return f"""你是一个专业的社交媒体舆情分析师。请分析以下B站视频的评论区数据，并给出结构化的分析结果。

【视频标题】{video_title}
【分析评论数】{len(comments)} 条主评论（含子回复）

【评论内容】
{comments_block}

说明：每条主评论下方以"↳"缩进显示的是其子回复（楼中楼）。子回复是其他用户对该主评论的直接回应，可能包含补充、反驳、延伸或无关内容。请将主评论及其子回复作为一个讨论线程综合分析，从整个对话线程中提炼观点，而非仅看主评论。

请从以下维度进行分析，并以JSON格式返回结果（确保返回合法的JSON）：

1. **overall_atmosphere**: 评论区整体氛围（从以下选项中选择最匹配的一个，也可自定义）：
   - 友好讨论 / 激烈争论 / 理性分析为主 / 情绪化表达为主 / 一边倒支持 / 一边倒反对 / 中立观望为主 / 混乱无序

2. **atmosphere_description**: 对整体氛围的详细描述（100-200字）

3. **viewpoints**: 数组，列出评论区的主要观点及其占比（总和应为100%），每个观点包含：
   - viewpoint: 观点名称（简洁概括）
   - percentage: 占比百分比（整数，需综合考虑主评论和子回复中的立场）
   - description: 该观点的简要说明

4. **public_opinion_direction**: 视频的舆论导向分析，包括：
   - 评论区是否被引导向某个特定方向
   - UP主对评论区的干预程度判断
   - 是否存在明显的控评或水军迹象

5. **summary**: 总结（150-300字），概括本次分析的要点

请直接返回JSON，不要包含markdown代码块标记。"""


def call_deepseek(prompt: str, api_key: str = None, max_tokens: int = 4096) -> dict:
    """调用 DeepSeek API 进行分析"""
    if api_key is None:
        api_key = DEEPSEEK_API_KEY

    if not api_key:
        raise ValueError(
            "DeepSeek API Key 未设置。请设置环境变量 DEEPSEEK_API_KEY，"
            "或在 .env 文件中配置，或在程序中直接输入。"
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "你是一个专业的社交媒体舆情分析师，擅长分析评论区数据并给出结构化报告。请始终以合法JSON格式返回结果。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }

    resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=180)
    resp.raise_for_status()
    result = resp.json()

    if "choices" not in result or len(result["choices"]) == 0:
        raise RuntimeError(f"DeepSeek API 返回异常: {result}")

    content = result["choices"][0]["message"]["content"]
    return _parse_analysis_response(content)


def _parse_analysis_response(content: str) -> dict:
    """解析 DeepSeek 返回的内容为 JSON"""
    content = content.strip()

    if content.startswith("```"):
        lines = content.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        import re
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {"raw_analysis": content, "parse_error": True}


def _merge_mathematically(batch_results: list[dict], total_comments: int) -> dict:
    """降级方案：当合成调用失败时，用关键词匹配+数学平均合并批次结果"""
    from collections import defaultdict
    import re

    all_viewpoints = []
    atmospheres = []

    for batch in batch_results:
        if "error" in batch:
            continue
        viewpoints = batch.get("viewpoints", [])
        all_viewpoints.extend(viewpoints)
        atm = batch.get("atmosphere", "")
        if atm:
            atmospheres.append(atm)

    # 统计合并后的观点（基于名称归一化）
    def normalize(name):
        return re.sub(r"[^一-鿿\w]", "", name).lower()

    merged = {}  # normalized_name -> {viewpoint, total_count, descriptions}
    for vp in all_viewpoints:
        name = vp.get("viewpoint", "未命名")
        count = vp.get("count", vp.get("percentage", 0))
        desc = vp.get("description", "")

        # 尝试找最匹配的已有观点
        nname = normalize(name)
        best_key = None
        best_overlap = 0
        for key in merged:
            # 简单的字符重叠率匹配
            overlap = len(set(nname) & set(key)) / max(len(set(nname) | set(key)), 1)
            if overlap > best_overlap:
                best_overlap = overlap
                best_key = key

        if best_key and best_overlap > 0.5:
            merged[best_key]["total_count"] += count
            merged[best_key]["descriptions"].append(desc)
        else:
            merged[nname] = {
                "viewpoint": name,
                "total_count": count,
                "descriptions": [desc],
            }

    total_count = sum(m["total_count"] for m in merged.values()) or 1

    merged_viewpoints = []
    for m in merged.values():
        merged_viewpoints.append({
            "viewpoint": m["viewpoint"],
            "percentage": round(m["total_count"] / total_count * 100),
            "description": "; ".join(d for d in m["descriptions"] if d)[:200],
        })
    merged_viewpoints.sort(key=lambda v: v["percentage"], reverse=True)

    # 调整百分比使总和为100
    diff = 100 - sum(v["percentage"] for v in merged_viewpoints)
    if diff != 0 and merged_viewpoints:
        merged_viewpoints[0]["percentage"] += diff

    # 多数氛围
    from collections import Counter
    atm_counts = Counter(atmospheres)
    dominant_atm = atm_counts.most_common(1)[0][0] if atm_counts else "未知"

    return {
        "overall_atmosphere": dominant_atm,
        "atmosphere_description": f"综合{len(batch_results)}个批次的分析结果。各批次氛围分布: {dict(atm_counts)}。",
        "viewpoints": merged_viewpoints,
        "public_opinion_direction": "（合成调用失败，基于批次数据的数学合并）",
        "summary": f"共分析{total_comments}条评论，分为{len(batch_results)}个批次。识别出{len(merged_viewpoints)}个主要观点。"
                   f"（注：本结果为数学合并，非LLM合成，可能精度较低）",
        "_fallback_merge": True,
    }


def analyze_comments(comments_data: dict, api_key: str = None) -> dict:
    """
    对评论数据进行完整分析，支持大批量评论的批次分析+合成。

    当评论数 ≤ BATCH_SIZE 时，使用简化单次分析；
    当评论数 > BATCH_SIZE 时，启用批次分析+合成模式。
    """
    comments = comments_data.get("comments", [])
    video_info = comments_data.get("video_info", {})

    if not comments:
        return {
            "error": "没有评论数据可供分析",
            "video_info": video_info,
        }

    # 限制总分析量
    comments = comments[:MAX_ANALYSIS_COMMENTS]
    total_count = len(comments)
    video_title = video_info.get("title", "")

    # 单批次路径（评论数不超过 BATCH_SIZE）
    if total_count <= BATCH_SIZE:
        print(f"\n  正在调用 DeepSeek API 分析 {total_count} 条评论...")
        prompt = _build_analysis_prompt(comments, video_title)
        analysis = call_deepseek(prompt, api_key)
        analysis["video_info"] = video_info
        analysis["analyzed_comment_count"] = total_count
        analysis["total_comment_count"] = len(comments_data.get("comments", []))
        analysis["batch_count"] = 1
        return analysis

    # ---- 批次分析路径 ----
    total_batches = (total_count + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"\n  共 {total_count} 条评论，分为 {total_batches} 个批次分析（每批 {BATCH_SIZE} 条）...")

    batch_results = []
    failed_count = 0

    for i in range(total_batches):
        start = i * BATCH_SIZE
        end = min(start + BATCH_SIZE, total_count)
        batch_comments = comments[start:end]

        print(f"  [{i + 1}/{total_batches}] 正在分析第 {start + 1}-{end} 条评论...", end=" ", flush=True)

        batch_analysis = None
        for attempt in range(MAX_BATCH_RETRIES + 1):
            try:
                prompt = _build_batch_prompt(batch_comments, video_title,
                                             i + 1, total_batches)
                batch_analysis = call_deepseek(prompt, api_key,
                                               max_tokens=BATCH_MAX_TOKENS)
                break
            except Exception as e:
                if attempt < MAX_BATCH_RETRIES:
                    wait = 3 * (attempt + 1)
                    print(f"\n    [重试] 批次{i + 1}失败: {e}，{wait}秒后重试...", end="", flush=True)
                    time.sleep(wait)
                else:
                    print(f"\n    [错误] 批次{i + 1}最终失败: {e}")
                    batch_analysis = {"batch_index": i, "error": str(e)}
                    failed_count += 1

        if batch_analysis:
            vp_count = len(batch_analysis.get("viewpoints", []))
            print(f"完成 ({vp_count}个观点)")
            batch_analysis["_batch_index"] = i
            batch_analysis["_comment_range"] = f"{start + 1}-{end}"
            batch_results.append(batch_analysis)

        time.sleep(0.5)  # 批次间短暂间隔

    successful = [r for r in batch_results if "error" not in r]

    if not successful:
        return {
            "error": "所有批次分析均失败",
            "video_info": video_info,
            "total_comment_count": total_count,
            "batch_count": total_batches,
            "failed_batch_count": failed_count,
        }

    # ---- 合成阶段 ----
    if len(successful) == 1:
        print(f"\n  仅1个批次成功，跳过合成，直接使用批次结果。")
        analysis = successful[0]
        analysis["video_info"] = video_info
        analysis["analyzed_comment_count"] = total_count
        analysis["total_comment_count"] = len(comments_data.get("comments", []))
        analysis["batch_count"] = total_batches
        analysis["failed_batch_count"] = failed_count
        return analysis

    print(f"\n  正在合成 {len(successful)} 个批次的分析结果...")
    try:
        synthesis_prompt = _build_synthesis_prompt(successful, video_title,
                                                    total_count, failed_count)
        analysis = call_deepseek(synthesis_prompt, api_key,
                                 max_tokens=SYNTHESIS_MAX_TOKENS)
    except Exception as e:
        print(f"  [警告] 合成调用失败: {e}，使用数学合并作为降级方案。")
        analysis = _merge_mathematically(successful, total_count)

    analysis["video_info"] = video_info
    analysis["analyzed_comment_count"] = total_count
    analysis["total_comment_count"] = len(comments_data.get("comments", []))
    analysis["batch_count"] = total_batches
    analysis["failed_batch_count"] = failed_count

    return analysis


def save_analysis(analysis: dict, bv: str, output_dir: str = None) -> str:
    """保存分析结果到JSON文件"""
    if output_dir is None:
        output_dir = OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    filepath = os.path.join(output_dir, f"{bv}_analysis.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)

    print(f"  分析结果已保存至: {filepath}")
    return filepath

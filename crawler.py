"""B站评论区爬取模块 - 支持WBI签名与游标分页"""

import hashlib
import json
import os
import time
import urllib.parse
import requests
from config import (
    BILIBILI_COOKIE,
    BILIBILI_VIDEO_INFO_URL,
    HEADERS,
    MAX_COMMENT_PAGES,
    MAX_SUB_REPLY_PAGES,
    MAX_SUB_REPLY_COMMENTS,
    STALE_PAGE_THRESHOLD,
    REQUEST_DELAY,
    OUTPUT_DIR,
)

# ---- WBI 签名相关 ----

# WBI mixin 表（B站前端JS提取，固定不变）
MIXIN_TABLE = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 52,
]

BILIBILI_COMMENT_WBI_URL = "https://api.bilibili.com/x/v2/reply/wbi/main"
BILIBILI_SUB_REPLY_URL = "https://api.bilibili.com/x/v2/reply/reply"
BILIBILI_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"

# 全局 Session（复用cookie）
_session = None
_wbi_key = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(HEADERS)
        if BILIBILI_COOKIE:
            _session.headers["Cookie"] = BILIBILI_COOKIE
    return _session


def _fetch_wbi_key() -> str:
    """从B站nav接口获取img_key和sub_key，计算mixin_key（缓存1小时）"""
    global _wbi_key

    if _wbi_key:
        return _wbi_key

    sess = _get_session()
    resp = sess.get(BILIBILI_NAV_URL, timeout=15)
    data = resp.json()

    wbi_img = data.get("data", {}).get("wbi_img", {})
    img_url = wbi_img.get("img_url", "")
    sub_url = wbi_img.get("sub_url", "")

    if not img_url or not sub_url:
        raise RuntimeError("无法获取WBI密钥，B站可能已更新API")

    img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]

    combined = img_key + sub_key
    mixin_key = "".join(combined[i] for i in MIXIN_TABLE[:32])
    _wbi_key = mixin_key
    return mixin_key


def _sign_params(params: dict) -> dict:
    """对参数字典进行WBI签名，添加 wts 和 w_rid"""
    mixin_key = _fetch_wbi_key()
    params = dict(params)
    params["wts"] = int(time.time())

    sorted_keys = sorted(params.keys())
    query_parts = []
    for key in sorted_keys:
        val = params[key]
        query_parts.append(f"{key}={urllib.parse.quote(str(val), safe='~')}")

    query_string = "&".join(query_parts)
    sign_string = query_string + mixin_key
    w_rid = hashlib.md5(sign_string.encode("utf-8")).hexdigest()

    params["w_rid"] = w_rid
    return params


# ---- 视频信息 ----

def get_video_info(bv: str) -> dict:
    """通过BV号获取视频信息（aid、标题、评论数等）"""
    params = {"bvid": bv}
    sess = _get_session()
    resp = sess.get(BILIBILI_VIDEO_INFO_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != 0:
        raise RuntimeError(f"获取视频信息失败: code={data.get('code')}, message={data.get('message')}")

    video_data = data["data"]
    return {
        "bvid": bv,
        "aid": video_data["aid"],
        "title": video_data.get("title", ""),
        "comment_count": video_data.get("stat", {}).get("reply", 0),
        "desc": video_data.get("desc", ""),
    }


# ---- 评论解析 ----

def _parse_reply(r: dict) -> dict:
    """将API返回的单条评论转为统一格式"""
    comment = {
        "rpid": r.get("rpid"),
        "mid": r.get("mid"),
        "username": r.get("member", {}).get("uname", ""),
        "content": r.get("content", {}).get("message", ""),
        "ctime": r.get("ctime", 0),
        "like": r.get("like", 0),
        "replies_count": r.get("rcount", 0),
        "sub_replies": [],
    }
    for sub in (r.get("replies") or []):
        comment["sub_replies"].append({
            "rpid": sub.get("rpid"),
            "mid": sub.get("mid"),
            "username": sub.get("member", {}).get("uname", ""),
            "content": sub.get("content", {}).get("message", ""),
            "ctime": sub.get("ctime", 0),
            "like": sub.get("like", 0),
        })
    return comment


def _parse_sub_reply(r: dict) -> dict:
    """将子回复API返回的回复转为统一格式"""
    return {
        "rpid": r.get("rpid"),
        "mid": r.get("mid"),
        "username": r.get("member", {}).get("uname", ""),
        "content": r.get("content", {}).get("message", ""),
        "ctime": r.get("ctime", 0),
        "like": r.get("like", 0),
    }


# ---- 评论获取 ----

def fetch_comments_cursor(aid: int, mode: int = 3, next_cursor: int = 0,
                          max_retries: int = 3) -> tuple[list[dict], int, bool, int]:
    """
    使用WBI签名+游标分页获取一页评论（20条）。

    mode: 2=按热度, 3=按时间
    返回: (评论列表, 下一页游标, 是否结束, 总评论数)
    """
    params = {
        "oid": aid,
        "type": 1,
        "mode": mode,
        "ps": 20,
    }
    if next_cursor:
        params["next"] = next_cursor

    sess = _get_session()

    for attempt in range(max_retries):
        try:
            signed_params = _sign_params(params)
            resp = sess.get(BILIBILI_COMMENT_WBI_URL, params=signed_params, timeout=15)

            # HTTP 412 通常表示被反爬拦截，大概率是缺少有效Cookie
            if resp.status_code == 412:
                if not BILIBILI_COOKIE:
                    raise RuntimeError(
                        "评论接口返回412 (被反爬拦截)，大概率是因为未设置有效的B站Cookie。"
                        "请在 .env 文件中配置 BILIBILI_COOKIE（需要 SESSDATA），"
                        "或直接编辑 config.py 中的 BILIBILI_COOKIE 变量。"
                    )
                raise RuntimeError(f"评论接口返回412 (被反爬拦截)，请检查Cookie是否有效。")

            resp.raise_for_status()
            data = resp.json()
            code = data.get("code")

            if code == 0:
                page_data = data.get("data") or {}
                cursor = page_data.get("cursor") or {}
                replies = page_data.get("replies") or []

                comments = [_parse_reply(r) for r in replies] if replies else []
                next_cursor = cursor.get("next", 0)
                is_end = cursor.get("is_end", True)
                all_count = cursor.get("all_count", 0)

                return comments, next_cursor, is_end, all_count

            # API 级错误处理
            if code == -403:
                raise RuntimeError(f"评论接口访问被拒 (code=-403)，可能需要添加Cookie")
            if code == -412 or code == -429:
                # 频率限制 — 等待后重试
                wait = 3 * (attempt + 1)
                print(f"  [频率限制] API返回{code}，{wait}秒后重试...")
                time.sleep(wait)
                continue
            if code == -404:
                # 评论不存在或已删除
                return [], 0, True, 0
            if code == 12002:
                # 评论区关闭
                print(f"  [提示] 该视频评论区已关闭")
                return [], 0, True, 0

            # 其他API错误
            msg = data.get("message", "未知错误")
            if attempt < max_retries - 1:
                wait = 2 * (attempt + 1)
                print(f"  [重试] API返回 code={code}: {msg}，{wait}秒后重试...")
                time.sleep(wait)
            else:
                raise RuntimeError(f"获取评论失败: code={code}, message={msg}")

        except requests.RequestException as e:
            if attempt < max_retries - 1:
                wait = 2 * (attempt + 1)
                print(f"  [重试] 请求失败: {e}，{wait}秒后重试...")
                time.sleep(wait)
            else:
                raise RuntimeError(f"评论请求失败（已重试{max_retries}次）: {e}")

    return [], 0, True, 0


def fetch_sub_replies(aid: int, root_rpid: int, max_pages: int = None) -> list[dict]:
    """
    获取某条评论的所有子回复（楼中楼），使用WBI签名分页拉取。
    返回子回复列表，不含嵌套结构。
    """
    if max_pages is None:
        max_pages = MAX_SUB_REPLY_PAGES

    all_subs = []
    sess = _get_session()

    for page in range(1, max_pages + 1):
        params = {
            "oid": aid,
            "type": 1,
            "root": root_rpid,
            "pn": page,
            "ps": 20,
        }

        try:
            signed = _sign_params(params)
            resp = sess.get(BILIBILI_SUB_REPLY_URL, params=signed, timeout=15)

            if resp.status_code == 412:
                break  # 被拦截，静默跳过

            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                break

            page_data = data.get("data") or {}
            replies = page_data.get("replies") or []
            if not replies:
                break

            for r in replies:
                all_subs.append(_parse_sub_reply(r))

            # 最后一页不足20条说明已到末尾
            if len(replies) < 20:
                break

            time.sleep(0.3)  # 子回复请求间隔更短

        except requests.RequestException:
            break  # 网络错误，跳过该评论的子回复

    return all_subs


def _crawl_by_mode(aid: int, mode: int, seen_ids: set, all_comments: list,
                   mode_label: str, max_pages: int, delay: float):
    """按指定排序模式爬取评论，返回 (页数, 是否正常结束)"""
    cursor = 0
    page = 0
    stale_count = 0

    while page < max_pages:
        try:
            comments, cursor, is_end, _ = fetch_comments_cursor(
                aid, mode=mode, next_cursor=cursor
            )
        except RuntimeError as e:
            print(f"  [错误] {mode_label}: {e}")
            break

        if not comments and is_end:
            print(f"  {mode_label}已到达末尾")
            break

        if not comments:
            # 空页但未标记结束 — 可能是API限制，等待后重试一次
            stale_count += 1
            if stale_count >= 2:
                print(f"  {mode_label}连续空页，可能已达API深度限制")
                break
            time.sleep(delay)
            continue

        new_count = 0
        for c in comments:
            if c["rpid"] not in seen_ids:
                seen_ids.add(c["rpid"])
                all_comments.append(c)
                new_count += 1

        page += 1
        print(f"  {mode_label} 第{page}页: 本页{len(comments)}条, 新增{new_count}条, 累计{len(all_comments)}条")

        if new_count == 0:
            stale_count += 1
            if stale_count >= STALE_PAGE_THRESHOLD:
                print(f"  连续{STALE_PAGE_THRESHOLD}页无新评论，{mode_label}停止")
                break
        else:
            stale_count = 0

        if is_end:
            print(f"  {mode_label}已到达API返回的末尾")
            break

        time.sleep(delay)

    return page


def crawl_comments(bv: str, max_pages: int = None, delay: float = None) -> dict:
    """
    爬取视频评论，三阶段策略：
      1. 按时间排序（mode=3）— 获取最新评论
      2. 按热度排序（mode=2）— 获取热门评论
      3. 子回复补全 — 对有大量回复的评论，分页拉取子回复
    """
    if max_pages is None:
        max_pages = MAX_COMMENT_PAGES
    if delay is None:
        delay = REQUEST_DELAY

    video_info = get_video_info(bv)
    aid = video_info["aid"]
    total_comments = video_info["comment_count"]

    print(f"\n  视频标题: {video_info['title']}")
    print(f"  评论总数: {total_comments}")
    if not BILIBILI_COOKIE:
        print(f"  ⚠️  未设置B站Cookie，API将严重限制返回评论数！")
        print(f"     请在 .env 或 config.py 中配置 BILIBILI_COOKIE（需要 SESSDATA）")
        print(f"     获取方法见 .env.example 文件")
    print(f"  开始爬取评论（WBI签名+游标分页）...")

    seen_ids = set()
    all_comments = []

    # ---- Phase 1: 按时间排序（最新评论） ----
    print(f"\n  [1/3] 按时间排序获取最新评论...")
    _crawl_by_mode(aid, mode=3, seen_ids=seen_ids, all_comments=all_comments,
                   mode_label="时间排序", max_pages=max_pages, delay=delay)

    # ---- Phase 2: 按热度排序（热门评论） ----
    print(f"\n  [2/3] 按热度排序获取热门评论...")
    hot_pages = min(max_pages, 100)
    _crawl_by_mode(aid, mode=2, seen_ids=seen_ids, all_comments=all_comments,
                   mode_label="热度排序", max_pages=hot_pages, delay=delay)

    # ---- Phase 3: 补全子回复（楼中楼） ----
    print(f"\n  [3/3] 补全评论的子回复（楼中楼）...")
    # 找出有大量子回复的评论（rcount > 3 表示有超过3条子回复未展示）
    comments_with_replies = [
        c for c in all_comments if c.get("replies_count", 0) > len(c.get("sub_replies", []))
    ]
    # 按回复数降序，取前 MAX_SUB_REPLY_COMMENTS 条
    comments_with_replies.sort(key=lambda c: c.get("replies_count", 0), reverse=True)
    target_comments = comments_with_replies[:MAX_SUB_REPLY_COMMENTS]

    if target_comments:
        print(f"  共 {len(comments_with_replies)} 条评论有未展示子回复，处理前 {len(target_comments)} 条...")
        for i, c in enumerate(target_comments):
            rpid = c["rpid"]
            existing_count = len(c["sub_replies"])
            expected_count = c.get("replies_count", 0)
            print(f"    [{i+1}/{len(target_comments)}] rpid={rpid}: "
                  f"已有{existing_count}条, 预期{expected_count}条", end="")

            subs = fetch_sub_replies(aid, rpid)
            if subs:
                c["sub_replies"] = subs
                print(f" → 获取{len(subs)}条")
            else:
                print(f" → 无新增")

            time.sleep(0.3)
    else:
        print(f"  所有评论的子回复均已完整，无需补全。")

    total_subs = sum(len(c.get("sub_replies", [])) for c in all_comments)
    total_items = len(all_comments) + total_subs

    result = {
        "video_info": video_info,
        "total_crawled": len(all_comments),
        "total_sub_replies": total_subs,
        "total_items": total_items,
        "comments": all_comments,
    }

    coverage = total_items / max(total_comments, 1) * 100
    print(f"\n  爬取完成，共获取 {len(all_comments)} 条主评论 + {total_subs} 条子回复 = {total_items} 条（去重后），覆盖率 {coverage:.1f}%")

    if coverage < 10 and total_comments > 100:
        print(f"  ⚠️  评论覆盖率极低！强烈建议配置有效的 BILIBILI_COOKIE 以获得完整数据。")
        print(f"     当前仅获取了 {total_items}/{total_comments} 条评论。")
        print(f"     获取Cookie方法: 浏览器登录B站 → F12 → Application → Cookies → 复制 SESSDATA")

    return result


def save_comments(data: dict, output_dir: str = None) -> str:
    """保存评论数据到JSON文件"""
    if output_dir is None:
        output_dir = OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    bv = data["video_info"]["bvid"]
    filepath = os.path.join(output_dir, f"{bv}_comments.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"  评论数据已保存至: {filepath}")
    return filepath

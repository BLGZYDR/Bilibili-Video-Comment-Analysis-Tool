import os
from dotenv import load_dotenv

load_dotenv()

# DeepSeek API
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = ""

# B站 API（评论接口使用WBI签名+next游标分页，见crawler.py）
BILIBILI_VIDEO_INFO_URL = "https://api.bilibili.com/x/web-interface/view"

# HTTP headers
# ⚠️ 重要：如果不设置有效的SESSDATA Cookie，B站API仅返回少量评论（通常1-2页）。
# 要获取大量评论，必须从浏览器登录后获取Cookie。
# 获取方法：浏览器打开 bilibili.com → 登录 → F12 → Application → Cookies → 复制 SESSDATA 值
BILIBILI_COOKIE = ""
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# Crawler settings
MAX_COMMENT_PAGES = 500       # 每轮排序模式的最大页数 (20条/页)，依赖 is_end 而非硬上限
MAX_SUB_REPLY_PAGES = 20      # 每条评论的子回复最大页数
MAX_SUB_REPLY_COMMENTS = 200  # 最多抓取子回复的评论数（按回复数降序）
STALE_PAGE_THRESHOLD = 5      # 连续N页无新评论则停止（防止死循环）
REQUEST_DELAY = 0.6           # 请求间隔秒数
MAX_ANALYSIS_COMMENTS = 6000    # 发送给 DeepSeek 的最大评论数（含子回复，文本量更大需降低上限）
BATCH_SIZE = 200               # 每批次分析的评论数（含子回复，单条文本量更大）
MAX_BATCH_RETRIES = 2          # 批次分析失败最大重试次数
BATCH_MAX_TOKENS = 2048        # 批次分析API响应的 max_tokens
SYNTHESIS_MAX_TOKENS = 8192    # 合成分析API响应的 max_tokens
OUTPUT_DIR = "output"

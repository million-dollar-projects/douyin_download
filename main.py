import os
import re
import json
import logging
import urllib.parse
import urllib.request
import tempfile
import asyncio
from fastapi import FastAPI, HTTPException, status, Query, Request
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field
import httpx
import yt_dlp
from telebot.async_telebot import AsyncTeleBot
from telebot import types

# Set up logging
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # Mute verbose httpx logs
logger = logging.getLogger(__name__)

app = FastAPI(
    title="TikTok & Douyin Video Parsing API",
    description="API to extract no-watermark video URLs from TikTok and Douyin using FastAPI + yt-dlp",
    version="1.0.0"
)

# Telegram Bot Configuration
TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
COOKIES_CONTENT = os.getenv("COOKIES_CONTENT")
TG_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "@renzhiup")


# ==========================================
# Utility Functions (must be defined first)
# ==========================================

def sanitize_and_bridge_cookies(file_path: str):
    """Reads a cookie file, fixes wrapped lines, standardizes columns to tabs, clones douyin.com keys to iesdouyin.com, and writes it back."""
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        raw_lines = content.strip().split('\n')
        combined_lines = []

        # Phase 1: Merge wrapped lines
        for line in raw_lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue
            if line_stripped.startswith("#"):
                combined_lines.append(line_stripped)
                continue

            is_new_cookie = False
            first_word = line_stripped.split()[0] if line_stripped.split() else ""
            if first_word.startswith(".") or "douyin.com" in first_word or "tiktok.com" in first_word:
                is_new_cookie = True

            if is_new_cookie:
                combined_lines.append(line_stripped)
            else:
                if combined_lines and not combined_lines[-1].startswith("#"):
                    prev_line = combined_lines[-1]
                    if prev_line[-1].isspace() or line_stripped[0].isspace():
                        combined_lines[-1] = prev_line + line_stripped
                    else:
                        combined_lines[-1] = prev_line + " " + line_stripped
                else:
                    combined_lines.append(line_stripped)

        # Phase 2: Convert to tab separation, clone keys to iesdouyin.com
        cleaned_lines = []
        cloned_lines = []
        for line in combined_lines:
            if line.startswith("#"):
                cleaned_lines.append(line)
                continue

            parts = line.split('\t')
            if len(parts) < 3:
                parts = re.split(r'\s+', line)

            if len(parts) == 6:
                parts.append("")

            if len(parts) == 7:
                # Force domain_specified flag to perfectly align with dot prefix to satisfy python's cookiejar assert
                starts_with_dot = parts[0].startswith(".")
                parts[1] = "TRUE" if starts_with_dot else "FALSE"
                cleaned_lines.append("\t".join(parts))

                # Clone to iesdouyin.com
                domain = parts[0]
                cookie_name = parts[5]
                if ("douyin.com" in domain) and ("iesdouyin.com" not in domain):
                    essential_keys = [
                        "sessionid", "sessionid_ss", "uid_tt", "uid_tt_ss",
                        "sid_tt", "passport_csrf_token", "__ac_nonce", "__ac_signature"
                    ]
                    if cookie_name in essential_keys:
                        cloned_parts = list(parts)
                        cloned_parts[0] = ".iesdouyin.com"
                        cloned_parts[1] = "TRUE"  # Enforce domain_specified flag to match dot prefix for cookiejar compatibility
                        cloned_lines.append("\t".join(cloned_parts))

        cleaned_lines.extend(cloned_lines)
        final_cookies_text = "# Netscape HTTP Cookie File\n" + "\n".join(cleaned_lines) + "\n"

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(final_cookies_text)
        logger.info(f"Successfully sanitized and bridged cookies in {file_path}")
    except Exception as e:
        logger.error(f"Failed to sanitize and bridge cookies file: {str(e)}")


def extract_http_url(text: str) -> str:
    """Extracts the first HTTP/HTTPS URL from a string."""
    pattern = r'https?://[^\s/$.?#].[^\s]*'
    match = re.search(pattern, text)
    if not match:
        raise ValueError("No valid URL found in the input text")
    return match.group(0)


def clean_error_message(error_msg: str) -> str:
    """Strips ANSI escape characters and converts common yt-dlp errors to friendly Chinese messages."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    cleaned = ansi_escape.sub('', error_msg)

    if "Fresh cookies (not necessarily logged in) are needed" in cleaned:
        return "解析失败：该平台（抖音/TikTok）目前强化了防爬虫限制，需要有效的 Cookie。请获取您浏览器的 Netscape 格式 Cookie 并保存到项目根目录下的 cookies.txt 文件中。"
    if "Unsupported URL" in cleaned:
        return "解析失败：暂不支持该链接，请确认输入的是抖音（Douyin）或 TikTok 的有效视频分享链接。"
    if "Your IP address is blocked" in cleaned or "HTTP Error 403" in cleaned:
        return "解析失败：服务器 IP 被平台暂时封禁/限制访问，请尝试配置代理或在 cookies.txt 中加入 Cookie 凭证。"

    return f"解析失败：{cleaned}"


# ==========================================
# Cookie Initialization (after functions are defined)
# ==========================================

# Dynamically generate cookies.txt from environment variable if provided
if COOKIES_CONTENT:
    try:
        cleaned_cookies = COOKIES_CONTENT.replace("\\n", "\n").replace("\\t", "\t").strip()
        if "Netscape HTTP Cookie File" not in cleaned_cookies:
            cleaned_cookies = "# Netscape HTTP Cookie File\n" + cleaned_cookies

        with open("cookies.txt", "w", encoding="utf-8") as f:
            f.write(cleaned_cookies + "\n")

        # Clean, reconstruct columns, and bridge to iesdouyin.com domain
        sanitize_and_bridge_cookies("cookies.txt")
        logger.info("Successfully loaded, generated and bridged cookies.txt from environment variable.")
    except Exception as e:
        logger.error(f"Failed to create cookies.txt from environment variable: {str(e)}")


# ==========================================
# Bot Initialization
# ==========================================

bot = None
if TG_BOT_TOKEN:
    bot = AsyncTeleBot(TG_BOT_TOKEN)
    logger.info("Telegram Bot Async instance initialized.")
else:
    logger.warning("TELEGRAM_BOT_TOKEN is not set. Telegram Bot functions will be inactive.")


# ==========================================
# Pydantic Models
# ==========================================

class ParseRequest(BaseModel):
    url: str = Field(..., description="The TikTok or Douyin video URL, or share text containing the URL")

class VideoMetadata(BaseModel):
    id: str
    title: str
    description: str
    thumbnail: str
    uploader: str
    duration: float
    video_url: str  # 代理后的流媒体播放/下载链接
    raw_video_url: str  # 原始的 CDN 直连链接
    extractor: str


# ==========================================
# Video Parsing Logic
# ==========================================

def parse_video_fallback(url: str) -> dict:
    """Fallback parser that queries a public Evil0ctal API instance when local yt-dlp fails."""
    logger.info(f"Using fallback parser for URL: {url}")
    api_url = f"https://api.douyin.wtf/api/hybrid/video_data?url={urllib.parse.quote(url)}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    req = urllib.request.Request(api_url, headers=headers)
    # Perform synchronous request with timeout
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read().decode('utf-8'))

    if data.get('code') != 200 or 'data' not in data:
        status_msg = data.get('msg') or data.get('message') or 'Unknown error'
        raise ValueError(f"Fallback API failed: {status_msg}")

    video_data = data['data']
    video = video_data.get('video', {})

    # Extract play URL from play_addr list
    play_addr_list = video.get('play_addr', {}).get('url_list', [])
    if not play_addr_list:
        raise ValueError("No playable CDN streams returned by fallback API")

    video_url = play_addr_list[0]

    metadata = {
        'id': video_data.get('aweme_id') or '',
        'title': video_data.get('desc') or 'No Title',
        'description': video_data.get('desc') or '',
        'thumbnail': video.get('cover', {}).get('url_list', [''])[0] or '',
        'uploader': video_data.get('author', {}).get('nickname') or 'Unknown',
        'duration': float(video_data.get('duration') or 0) / 1000.0,  # Convert ms to seconds
        'raw_video_url': video_url,
        'extractor': 'Douyin' if 'douyin.com' in video_url or 'amemv.com' in video_url else 'TikTok'
    }

    return {
        'metadata': metadata,
        'cookie_header': ''  # Fallback uses hosted proxies; cookies are handled upstream
    }


def parse_video_pearktrue(url: str) -> dict:
    """Second fallback parser that queries pearktrue public API."""
    logger.info(f"Using PearkTrue fallback parser for URL: {url}")
    api_url = f"https://api.pearktrue.cn/api/douyin/?url={urllib.parse.quote(url)}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    try:
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        if data.get('code') != 200 or 'data' not in data:
            status_msg = data.get('msg') or data.get('message') or 'Unknown error'
            raise ValueError(f"PearkTrue API returned error status: {status_msg}")

        video_data = data['data']
        video_url = video_data.get('video')
        if not video_url:
            raise ValueError("No video URL returned by PearkTrue API")

        metadata = {
            'id': '',
            'title': video_data.get('title') or 'No Title',
            'description': video_data.get('title') or '',
            'thumbnail': video_data.get('cover') or '',
            'uploader': 'Unknown',
            'duration': 0.0,
            'raw_video_url': video_url,
            'extractor': 'Douyin'
        }

        return {
            'metadata': metadata,
            'cookie_header': ''
        }
    except Exception as e:
        logger.error(f"PearkTrue API connection or parse error: {str(e)}")
        raise e


def parse_video_mobile_html(url: str) -> dict:
    """Fallback parser that extracts no-watermark video directly from Douyin mobile sharing HTML without cookies."""
    logger.info(f"Using Mobile HTML scraper fallback for URL: {url}")

    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
    }

    try:
        # Step 1: Follow redirects to get real URL
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            final_url = resp.geturl()
            html = resp.read().decode('utf-8')

        logger.info(f"Redirected to: {final_url}")

        # Step 2: Extract aweme_id
        video_id_match = re.search(r'video/(\d+)', final_url)
        if not video_id_match:
            video_id_match = re.search(r'video/(\d+)', html)

        video_id = video_id_match.group(1) if video_id_match else ""
        if not video_id:
            raise ValueError("Could not extract video ID from redirects")

        # Step 3: Parse RENDER_DATA from HTML if present
        render_data_match = re.search(r'<script id="RENDER_DATA" type="application/json">([^<]+)</script>', html)
        if render_data_match:
            render_data_json = urllib.parse.unquote(render_data_match.group(1))
            data = json.loads(render_data_json)

            def find_play_addr(obj):
                if isinstance(obj, dict):
                    if 'play_addr' in obj and isinstance(obj['play_addr'], dict):
                        url_list = obj['play_addr'].get('url_list', [])
                        if url_list:
                            return url_list[0]
                    for k, v in obj.items():
                        res = find_play_addr(v)
                        if res:
                            return res
                elif isinstance(obj, list):
                    for item in obj:
                        res = find_play_addr(item)
                        if res:
                            return res
                return None

            video_url = find_play_addr(data)
            if video_url:
                no_watermark_url = video_url.replace("playwm", "play")
                if no_watermark_url.startswith("//"):
                    no_watermark_url = "https:" + no_watermark_url

                def find_key(obj, target_key):
                    if isinstance(obj, dict):
                        if target_key in obj:
                            return obj[target_key]
                        for k, v in obj.items():
                            res = find_key(v, target_key)
                            if res is not None:
                                return res
                    elif isinstance(obj, list):
                        for item in obj:
                            res = find_key(item, target_key)
                            if res is not None:
                                return res
                    return None

                title = find_key(data, 'desc') or 'No Title'
                nickname = find_key(data, 'nickname') or 'Unknown'
                cover = find_key(data, 'cover')
                cover_url = cover.get('url_list', [''])[0] if isinstance(cover, dict) and cover.get('url_list') else ''

                metadata = {
                    'id': video_id,
                    'title': title,
                    'description': title,
                    'thumbnail': cover_url,
                    'uploader': nickname,
                    'duration': 0.0,
                    'raw_video_url': no_watermark_url,
                    'extractor': 'Douyin'
                }
                return {
                    'metadata': metadata,
                    'cookie_header': ''
                }

        # Scraper regex fallback
        play_addr_match = re.search(r'"playAddr"\s*:\s*"([^"]+)"', html)
        if not play_addr_match:
            play_addr_match = re.search(r'playwm[^"]+', html)
            if play_addr_match:
                matched_str = play_addr_match.group(0)
                if matched_str.startswith("//"):
                    matched_str = "https:" + matched_str
                play_addr_match = re.match(r'[^\\\s]+', matched_str)

        if play_addr_match:
            video_url = play_addr_match.group(1) if len(play_addr_match.groups()) > 0 else play_addr_match.group(0)
            video_url = video_url.replace('\\u002F', '/').replace('\\/', '/')
            if video_url.startswith("//"):
                video_url = "https:" + video_url

            no_watermark_url = video_url.replace("playwm", "play")

            # Try to extract title and nickname from HTML via regex
            title = "无水印视频"
            title_match = re.search(r'<title>([^<]+)</title>', html)
            if title_match:
                title = title_match.group(1).replace(" - 抖音", "").replace(" - 抖音手机网页版", "").strip()

            nickname = "未知作者"
            nickname_match = re.search(r'"nickname"\s*:\s*"([^"]+)"', html)
            if not nickname_match:
                nickname_match = re.search(r'<p class="[^\"]*nickname[^\"]*">([^<]+)</p>', html)
            if nickname_match:
                nickname = nickname_match.group(1).strip()

            metadata = {
                'id': video_id,
                'title': title,
                'description': title,
                'thumbnail': '',
                'uploader': nickname,
                'duration': 0.0,
                'raw_video_url': no_watermark_url,
                'extractor': 'Douyin'
            }
            return {
                'metadata': metadata,
                'cookie_header': ''
            }

        raise ValueError("Could not find any video play address in mobile HTML")
    except Exception as e:
        logger.error(f"Mobile HTML scraper failed: {str(e)}")
        raise e


def parse_video(url: str) -> dict:
    """Uses yt-dlp to extract video metadata and direct URL along with session cookies."""
    ydl_opts = {
        'format': 'best',
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
        }
    }

    # Use cookies if available
    cookies_path = 'cookies.txt'
    if os.path.exists(cookies_path) and os.path.getsize(cookies_path) > 0:
        # Pre-process, fix wraps, format columns, and bridge to iesdouyin.com domain
        sanitize_and_bridge_cookies(cookies_path)
        ydl_opts['cookiefile'] = cookies_path
        logger.info(f"Using sanitized and bridged cookies from {cookies_path}")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            if 'entries' in info:
                entries = list(info['entries'])
                if not entries:
                    raise ValueError("No video entries found in the URL")
                info = entries[0]

            video_url = info.get('url')

            if not video_url and info.get('formats'):
                formats = info.get('formats', [])
                valid_formats = [f for f in formats if f.get('url')]
                if valid_formats:
                    video_url = valid_formats[-1]['url']

            if not video_url:
                raise ValueError("Could not extract a direct video download URL")

            cookies = []
            for c in ydl.cookiejar:
                cookies.append(f"{c.name}={c.value}")
            cookie_header = "; ".join(cookies)

            metadata = {
                'id': info.get('id') or '',
                'title': info.get('title') or info.get('description') or 'No Title',
                'description': info.get('description') or '',
                'thumbnail': info.get('thumbnail') or (info.get('thumbnails')[-1]['url'] if info.get('thumbnails') else ''),
                'uploader': info.get('uploader') or info.get('uploader_id') or 'Unknown',
                'duration': float(info.get('duration') or 0),
                'raw_video_url': video_url,
                'extractor': info.get('extractor') or ''
            }

            return {
                'metadata': metadata,
                'cookie_header': cookie_header
            }
    except Exception as e:
        logger.warning(f"Local yt-dlp parsing failed: {str(e)}. Swapping to fallback 1 (Mobile HTML Scraper)...")
        try:
            return parse_video_mobile_html(url)
        except Exception as mobile_err:
            logger.warning(f"Fallback 1 (Mobile HTML Scraper) failed: {str(mobile_err)}. Swapping to fallback 2 (douyin.wtf)...")
            try:
                return parse_video_fallback(url)
            except Exception as fallback_err_1:
                logger.warning(f"Fallback 2 (douyin.wtf) failed: {str(fallback_err_1)}. Swapping to fallback 3 (pearktrue)...")
                try:
                    return parse_video_pearktrue(url)
                except Exception as fallback_err_2:
                    logger.error(f"All fallback parsers failed. Mobile: {str(mobile_err)}. WTF: {str(fallback_err_1)}. PearkTrue: {str(fallback_err_2)}")
                    # Raise original error to represent the primary failure reason
                    raise e


# ==========================================
# FastAPI Routes
# ==========================================

@app.get("/")
async def serve_ui():
    """Serves the Web UI page."""
    return FileResponse("index.html")

@app.get("/health")
async def health():
    """Service health-check endpoint."""
    return {
        "status": "healthy",
        "service": "TikTok & Douyin Parser API",
        "yt-dlp_version": yt_dlp.version.__version__
    }

@app.post("/parse", response_model=VideoMetadata)
async def parse(request: Request, req_body: ParseRequest):
    try:
        target_url = extract_http_url(req_body.url)
        logger.info(f"Parsing URL: {target_url}")
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    try:
        result = parse_video(target_url)
        metadata = result['metadata']
        cookie_header = result['cookie_header']

        # Build the proxy streaming URL
        encoded_cdn_url = urllib.parse.quote(metadata['raw_video_url'])
        encoded_cookies = urllib.parse.quote(cookie_header)
        encoded_orig_url = urllib.parse.quote(target_url)

        # Build the proxy URL dynamically based on the request host
        proxy_url = f"{request.base_url}stream?url={encoded_cdn_url}&cookies={encoded_cookies}&referer={encoded_orig_url}"

        metadata['video_url'] = proxy_url
        return metadata
    except Exception as e:
        error_msg = str(e)
        cleaned_msg = clean_error_message(error_msg)
        if "Unsupported URL" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=cleaned_msg
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=cleaned_msg
        )

@app.get("/stream")
async def stream_video(
    url: str = Query(..., description="The direct video URL from CDN"),
    cookies: str = Query(None, description="The cookies required for authentication"),
    referer: str = Query(None, description="The original video page URL to use as Referer"),
    download: int = Query(0, description="Force download the file by setting Content-Disposition")
):
    """Proxies the video stream from TikTok/Douyin CDN to bypass 403 Forbidden checks."""
    req_referer = referer
    if not req_referer:
        req_referer = "https://www.tiktok.com/"
        if "douyin.com" in url or "amemv.com" in url:
            req_referer = "https://www.douyin.com/"

    headers = {
        "Referer": req_referer,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    }
    if cookies:
        headers["Cookie"] = cookies

    async def video_streamer():
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                async with client.stream("GET", url, headers=headers) as r:
                    if r.status_code >= 400:
                        logger.error(f"Proxy stream failed with status {r.status_code}")
                        return
                    async for chunk in r.aiter_bytes(chunk_size=1024 * 64):
                        yield chunk
            except Exception as e:
                logger.error(f"Error during video streaming proxy: {str(e)}")
                return

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.head(url, headers=headers)
            content_type = r.headers.get("content-type", "video/mp4")
            content_length = r.headers.get("content-length")
    except Exception as e:
        logger.warning(f"Failed to fetch metadata headers for proxy streaming: {str(e)}")
        content_type = "video/mp4"
        content_length = None

    response_headers = {}
    if content_length:
        response_headers["Content-Length"] = content_length
    if download:
        response_headers["Content-Disposition"] = "attachment; filename=\"video.mp4\""

    return StreamingResponse(
        video_streamer(),
        media_type=content_type,
        headers=response_headers
    )


# ==========================================
# Telegram Bot Integrations
# ==========================================

async def self_keep_alive():
    """Background task to ping itself and keep Render instance alive."""
    if not RENDER_EXTERNAL_URL:
        return

    url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/health"
    logger.info(f"Self keep-alive task started. Target: {url}")

    # Wait for service startup to stabilize
    await asyncio.sleep(60)

    while True:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url)
                logger.info(f"Self-ping keep-alive status: {r.status_code}")
        except Exception as e:
            logger.warning(f"Self-ping keep-alive failed: {str(e)}")

        # Render sleeps after 15 mins of inactivity. Ping every 10 mins (600s).
        await asyncio.sleep(600)

@app.on_event("startup")
async def on_startup():
    # Start the self-ping keep alive task
    if RENDER_EXTERNAL_URL:
        asyncio.create_task(self_keep_alive())

    if bot and RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/tg-webhook/{TG_BOT_TOKEN}"
        logger.info(f"Setting Telegram Webhook to: {webhook_url}")
        try:
            await bot.remove_webhook()
            success = await bot.set_webhook(url=webhook_url)
            if success:
                logger.info("Telegram Webhook set successfully.")
            else:
                logger.error("Failed to set Telegram Webhook.")
        except Exception as e:
            logger.error(f"Error during setting Telegram Webhook: {str(e)}")
    elif bot:
        logger.warning("RENDER_EXTERNAL_URL is not set. Webhook registration skipped. In local environment, use polling or configure local tunnels.")

@app.post("/tg-webhook/{token}")
async def tg_webhook(token: str, request: Request):
    if not bot or token != TG_BOT_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized or Bot not active"
        )
    try:
        body = await request.body()
        json_string = body.decode("utf-8")
        update = types.Update.de_json(json_string)
        await bot.process_new_updates([update])
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error processing Telegram update: {str(e)}")
        return {"status": "error", "message": str(e)}


# Define Bot handlers only if bot is initialized
if bot:
    @bot.message_handler(commands=['start', 'help'])
    async def send_welcome(message):
        welcome_text = (
            "👋 **欢迎使用抖音 & TikTok 无水印视频下载机器人！**\n\n"
            "直接向我发送抖音或 TikTok 的分享链接（支持整段分享文本），我就会为您解析并直接把去水印的高清视频发送给您。\n\n"
            "💡 示例链接：\n"
            "• `https://v.douyin.com/xxxx/`\n"
            "• `https://www.tiktok.com/@user/video/xxxx`"
        )
        await bot.reply_to(message, welcome_text, parse_mode="Markdown")

    @bot.message_handler(func=lambda message: True)
    async def handle_message(message):
        text = message.text
        if not text:
            return

        try:
            target_url = extract_http_url(text)
        except ValueError:
            await bot.reply_to(message, "⚠️ 未在您的消息中检测到有效的链接，请发送正确的抖音或 TikTok 分享文本。")
            return

        # Simple verification of domains
        if not any(domain in target_url for domain in ["douyin.com", "tiktok.com", "amemv.com"]):
            await bot.reply_to(message, "⚠️ 该链接不属于支持的平台（抖音/TikTok），请检查后重新发送。")
            return

        status_msg = await bot.reply_to(message, "⏳ 正在解析链接，请稍候...")

        try:
            # Parse video using existing logic
            result = parse_video(target_url)
            metadata = result['metadata']
            cookie_header = result['cookie_header']
            raw_cdn_url = metadata['raw_video_url']

            await bot.edit_message_text(
                text="📥 视频解析成功，正在下载并准备无水印高清视频文件...",
                chat_id=message.chat.id,
                message_id=status_msg.message_id
            )

            # Start downloading video stream
            req_referer = target_url
            headers = {
                "Referer": req_referer,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
            }
            if cookie_header:
                headers["Cookie"] = cookie_header

            # Use temp file to download video safely
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_video:
                temp_path = temp_video.name

            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
                    async with client.stream("GET", raw_cdn_url, headers=headers) as r:
                        if r.status_code >= 400:
                            raise ValueError(f"Download failed from CDN, HTTP {r.status_code}")

                        content_length = r.headers.get("content-length")
                        file_size = int(content_length) if content_length else 0

                        # Telegram Bot file size limit is 50MB
                        if file_size > 50 * 1024 * 1024:
                            raise ValueError("视频大小超过 50MB 限制")

                        with open(temp_path, "wb") as f:
                            async for chunk in r.aiter_bytes(chunk_size=65536):
                                f.write(chunk)

                # Double check size on disk
                actual_size = os.path.getsize(temp_path)
                if actual_size > 50 * 1024 * 1024:
                    raise ValueError("视频下载文件实际大小超过 50MB")

                # Determine target for video: channel if configured, otherwise user
                target_chat = TG_CHANNEL if TG_CHANNEL else message.chat.id

                upload_msg_text = "📤 正在上传视频到频道..." if TG_CHANNEL else "📤 正在上传视频到 Telegram..."
                await bot.edit_message_text(
                    text=upload_msg_text,
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id
                )

                # Send video
                title = metadata.get('title') or metadata.get('description') or '无水印高清视频'
                caption = f"🎬 {title[:200]}\n\n👤 作者: {metadata.get('uploader', '未知')}\n⏱️ 时长: {metadata.get('duration', 0)}秒\n\n💡 视频已成功去水印！"

                with open(temp_path, "rb") as video_file:
                    await bot.send_video(
                        chat_id=target_chat,
                        video=video_file,
                        caption=caption,
                        supports_streaming=True
                    )

                # Delete the loading status message
                await bot.delete_message(chat_id=message.chat.id, message_id=status_msg.message_id)

                # Send final confirmation message to user if synced to channel
                if TG_CHANNEL:
                    await bot.reply_to(message, f"🎉 视频解析成功，已同步发送至频道 {TG_CHANNEL}！")

            except Exception as dl_upload_err:
                logger.warning(f"Failed to post video directly: {str(dl_upload_err)}")

                # Build download link using fallback configuration (RENDER_EXTERNAL_URL)
                base_url = RENDER_EXTERNAL_URL.rstrip('/') + '/' if RENDER_EXTERNAL_URL else "http://localhost:8000/"
                encoded_cdn = urllib.parse.quote(raw_cdn_url)
                encoded_cookies = urllib.parse.quote(cookie_header)
                encoded_orig = urllib.parse.quote(target_url)

                proxy_download_url = f"{base_url}stream?url={encoded_cdn}&cookies={encoded_cookies}&referer={encoded_orig}&download=1"

                fallback_text = (
                    f"🎬 **{metadata.get('title', '视频解析成功')}**\n\n"
                    f"👤 作者: {metadata.get('uploader', '未知')}\n"
                    f"⏱️ 时长: {metadata.get('duration', 0)}秒\n\n"
                    f"⚠️ 因视频文件过大 (>50MB) 或机器人权限受限，未能直接上传视频文件。\n"
                    f"🔗 您可以点击下方链接直接下载高清无水印视频：\n\n"
                    f"[📥 点击下载无水印视频]({proxy_download_url})"
                )

                if TG_CHANNEL:
                    try:
                        # Try to post the link fallback message to the channel
                        await bot.send_message(
                            chat_id=TG_CHANNEL,
                            text=fallback_text,
                            parse_mode="Markdown"
                        )
                        await bot.edit_message_text(
                            text=f"🎉 视频解析成功！但因文件过大或权限受限未能直接上传视频，已将下载链接同步发布到频道 {TG_CHANNEL}。\n\n[📥 点击直接下载]({proxy_download_url})",
                            chat_id=message.chat.id,
                            message_id=status_msg.message_id,
                            parse_mode="Markdown"
                        )
                    except Exception as chan_err:
                        logger.error(f"Failed to send fallback message to channel: {str(chan_err)}")
                        await bot.edit_message_text(
                            text=fallback_text + f"\n\n*(发送至频道失败，请确保机器人已成为频道 {TG_CHANNEL} 的管理员。)*",
                            chat_id=message.chat.id,
                            message_id=status_msg.message_id,
                            parse_mode="Markdown"
                        )
                else:
                    await bot.edit_message_text(
                        text=fallback_text,
                        chat_id=message.chat.id,
                        message_id=status_msg.message_id,
                        parse_mode="Markdown"
                    )
            finally:
                # Remove temp file
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception as cleanup_err:
                        logger.error(f"Failed to remove temp file: {str(cleanup_err)}")

        except Exception as parse_error:
            logger.error(f"Bot handler parse error: {str(parse_error)}")
            error_msg = clean_error_message(str(parse_error))
            await bot.edit_message_text(
                text=f"❌ 解析失败\n\n原因: {error_msg}",
                chat_id=message.chat.id,
                message_id=status_msg.message_id
            )

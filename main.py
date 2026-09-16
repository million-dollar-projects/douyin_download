import os
import re
import json
import logging
import urllib.parse
import urllib.request
import tempfile
import asyncio
import gc
import ctypes
import uuid
import time
from fastapi import FastAPI, HTTPException, status, Query, Request, BackgroundTasks
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field
import httpx
import yt_dlp
from telebot.async_telebot import AsyncTeleBot
from telebot import types
from abogus import ABogus, generate_a_bogus

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

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
# Parse comma-separated list of channels from environment variable
TELEGRAM_CHANNEL_RAW = os.getenv("TELEGRAM_CHANNEL", "@renzhiup")
TG_CHANNELS = [c.strip() for c in TELEGRAM_CHANNEL_RAW.split(",") if c.strip()]
TG_CHANNEL = TG_CHANNELS[0] if TG_CHANNELS else ""

# Upstash Redis REST Configuration
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")


def clean_memory():
    """Forces Python garbage collection and trims memory allocator memory on Linux (Render)."""
    gc.collect()
    try:
        # malloc_trim is a GNU libc extension available on Linux (like Render)
        # It releases free memory from the allocator back to the OS.
        libc = ctypes.CDLL(None)
        libc.malloc_trim(0)
    except Exception:
        pass

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
            if (first_word.startswith(".") or "." in first_word) and len(line_stripped.split()) >= 3:
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
        return "解析失败：暂不支持该链接，请确认输入的是抖音 (Douyin)、TikTok 或 X (Twitter) 的有效视频分享链接。"
    if "No video could be found in this tweet" in cleaned:
        return "解析失败：未在推文中检测到视频。如果该视频包含敏感或成人内容 (NSFW/年龄限制)，需要您导出已登录 X (Twitter) 账号的浏览器 Cookie (Netscape 格式) 并追加保存到项目根目录下的 cookies.txt 文件中以完成授权访问。"
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


USER_PREFS_FILE = "user_preferences.json"

def load_user_prefs() -> dict:
    """Loads user preferences from Upstash Redis or local user_preferences.json file."""
    if UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN:
        try:
            req = urllib.request.Request(
                UPSTASH_REDIS_REST_URL,
                data=json.dumps(["GET", "user_preferences"]).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}",
                    "Content-Type": "application/json"
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                result_data = json.loads(resp.read().decode("utf-8"))
                val = result_data.get("result")
                if val:
                    return json.loads(val)
                return {}
        except Exception as e:
            logger.error(f"Failed to load user preferences from Upstash Redis: {e}")
            return {}

    if os.path.exists(USER_PREFS_FILE):
        try:
            with open(USER_PREFS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load user preferences: {e}")
            return {}
    return {}

def save_user_prefs(prefs: dict):
    """Saves user preferences to Upstash Redis or local user_preferences.json file."""
    if UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN:
        try:
            val_str = json.dumps(prefs, ensure_ascii=False)
            req = urllib.request.Request(
                UPSTASH_REDIS_REST_URL,
                data=json.dumps(["SET", "user_preferences", val_str]).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}",
                    "Content-Type": "application/json"
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                result_data = json.loads(resp.read().decode("utf-8"))
                if result_data.get("result") == "OK":
                    logger.info("Successfully saved user preferences to Upstash Redis")
                else:
                    logger.error(f"Failed to save user preferences to Upstash Redis: {result_data}")
            return
        except Exception as e:
            logger.error(f"Failed to save user preferences to Upstash Redis: {e}")
            return

    try:
        with open(USER_PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save user preferences: {e}")

def get_user_data(chat_id: int) -> dict:
    """Retrieves user preference dictionary from file. Supports backward-compatibility for string values."""
    prefs = load_user_prefs()
    data = prefs.get(str(chat_id), {})
    if isinstance(data, str):
        # Convert legacy string (mode) to structured dict
        data = {
            "mode": data,
            "private_channels": []
        }
    return data

def get_user_private_channels(chat_id: int) -> list:
    """Returns the list of private channels configured by the user."""
    data = get_user_data(chat_id)
    return data.get("private_channels", [])

def add_user_private_channel(chat_id: int, channel: str) -> bool:
    """Binds a new private channel for the user. Returns True if successful, False if duplicate."""
    prefs = load_user_prefs()
    data = prefs.get(str(chat_id), {})
    if isinstance(data, str):
        data = {"mode": data, "private_channels": []}
    
    private_channels = data.setdefault("private_channels", [])
    if channel not in private_channels:
        private_channels.append(channel)
        prefs[str(chat_id)] = data
        save_user_prefs(prefs)
        return True
    return False

def remove_user_private_channel(chat_id: int, channel: str) -> bool:
    """Unbinds a private channel for the user. Returns True if successful, False if not found."""
    prefs = load_user_prefs()
    data = prefs.get(str(chat_id), {})
    if isinstance(data, str):
        data = {"mode": data, "private_channels": []}
    
    private_channels = data.get("private_channels", [])
    if channel in private_channels:
        private_channels.remove(channel)
        prefs[str(chat_id)] = data
        save_user_prefs(prefs)
        return True
    return False

def get_user_mode(chat_id: int) -> str:
    """Returns the preference mode ('direct' or 'channel:<channel_name>'). Supports fallback to public/private channels."""
    data = get_user_data(chat_id)
    mode = data.get("mode")
    
    # Combined public + user private channels
    user_private_channels = data.get("private_channels", [])
    all_channels = TG_CHANNELS + user_private_channels
    
    if not mode:
        if all_channels:
            return f"channel:{all_channels[0]}"
        else:
            return "direct"
            
    # Legacy compatibility mapping
    if mode == "channel":
        if all_channels:
            return f"channel:{all_channels[0]}"
        else:
            return "direct"
            
    if mode.startswith("channel:"):
        target_channel = mode.split(":", 1)[1]
        if target_channel not in all_channels:
            # Fallback to first available channel, otherwise direct
            if all_channels:
                return f"channel:{all_channels[0]}"
            else:
                return "direct"
                
    return mode

def set_user_mode(chat_id: int, mode: str):
    """Sets user mode preference and saves to file."""
    prefs = load_user_prefs()
    data = prefs.get(str(chat_id), {})
    if isinstance(data, str):
        data = {"mode": data, "private_channels": []}
    data["mode"] = mode
    prefs[str(chat_id)] = data
    save_user_prefs(prefs)

def get_user_keyboard_markup(chat_id: int) -> types.ReplyKeyboardMarkup:
    """Generates bottom reply keyboard dynamically merging public and user-configured private channels in a 3-column layout."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    
    user_private_channels = get_user_private_channels(chat_id)
    all_channels = TG_CHANNELS + user_private_channels
    
    if not all_channels:
        # If no channel is configured at all, show simple helper button
        btn = types.KeyboardButton("📥 直接返回给您 ✅")
        markup.add(btn)
        return markup

    current_mode = get_user_mode(chat_id)
    buttons = []
    
    btn_direct = types.KeyboardButton("📥 直接返回给您" + (" ✅" if current_mode == "direct" else ""))
    buttons.append(btn_direct)

    # Add a button for each channel (public + private)
    for channel in all_channels:
        is_selected = (current_mode == f"channel:{channel}")
        btn_channel = types.KeyboardButton(f"📤 发送至 {channel}" + (" ✅" if is_selected else ""))
        buttons.append(btn_channel)

    markup.add(*buttons)
    return markup


RETRY_CACHE = {}

def cache_retry_url(url: str) -> str:
    """Caches retry URL and returns a short retry ID."""
    now = time.time()
    # Clean up old entries (e.g. older than 1 hour) to prevent memory leak
    expired_ids = [k for k, v in RETRY_CACHE.items() if now - v[1] > 3600]
    for k in expired_ids:
        RETRY_CACHE.pop(k, None)
        
    retry_id = str(uuid.uuid4())[:8]
    RETRY_CACHE[retry_id] = (url, now)
    return retry_id

def get_cached_retry_url(retry_id: str) -> str:
    """Retrieves cached retry URL by its ID."""
    entry = RETRY_CACHE.get(retry_id)
    if entry:
        return entry[0]
    return None



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

def parse_video_douyin_web(url: str) -> dict:
    """Primary parser using Douyin's web post API (aweme/v1/web/aweme/post) to find the user's own videos."""
    logger.info(f"Using Douyin web post API parser for URL: {url}")
    
    # Step 1: Follow redirect to get video_id
    headers_mobile = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
    }
    req = urllib.request.Request(url, headers=headers_mobile)
    with urllib.request.urlopen(req, timeout=10) as resp:
        final_url = resp.geturl()
    
    video_id_match = re.search(r'/video/(\d+)', final_url)
    if not video_id_match:
        raise ValueError(f"Could not extract video ID from redirect URL: {final_url}")
    video_id = video_id_match.group(1)
    logger.info(f"Extracted video ID: {video_id}")
    
    # Step 2: Load cookies from cookies.txt
    cookies_path = 'cookies.txt'
    cookie_dict = {}
    if os.path.exists(cookies_path):
        with open(cookies_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) == 7 and 'douyin.com' in parts[0]:
                    cookie_dict[parts[5]] = parts[6]
    
    if not cookie_dict.get('sessionid'):
        raise ValueError("No sessionid in cookies.txt — please update cookies via /update-cookies endpoint")
    
    cookie_str = '; '.join(f'{k}={v}' for k, v in cookie_dict.items())
    
    # Step 3: Call aweme/post API to get the user's posts
    api_url = (
        f'https://www.douyin.com/aweme/v1/web/aweme/post/'
        f'?user_id=&count=35&aid=6383&version_name=23.5.0'
        f'&device_platform=webapp&os_name=windows&browser_language=zh-CN'
        f'&browser_platform=Win32&browser_name=Chrome&browser_version=124.0.0.0'
    )
    headers_api = {
        'Cookie': cookie_str,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Referer': f'https://www.douyin.com/video/{video_id}',
        'Accept': 'application/json, text/plain, */*',
    }
    
    req2 = urllib.request.Request(api_url, headers=headers_api)
    with urllib.request.urlopen(req2, timeout=15) as resp2:
        data = json.loads(resp2.read().decode('utf-8'))
    
    if data.get('status_code') != 0:
        raise ValueError(f"Douyin web API returned error: {data.get('status_code')} {data.get('status_msg', '')}")
    
    aweme_list = data.get('aweme_list', [])
    if not aweme_list:
        raise ValueError("Douyin web API returned empty aweme_list")
    
    # Search for the matching video_id in the post list
    target_item = None
    for item in aweme_list:
        if item.get('aweme_id') == video_id:
            target_item = item
            break
            
    if not target_item:
        raise ValueError(f"Video {video_id} not found in the user's latest 35 posts via API.")
    
    video_data = target_item.get('video', {})
    
    # Prefer highest quality (last bit_rate entry)
    bit_rates = video_data.get('bit_rate', [])
    if bit_rates:
        best = bit_rates[-1]
        play_urls = best.get('play_addr', {}).get('url_list', [])
    else:
        play_urls = video_data.get('play_addr', {}).get('url_list', [])
    
    if not play_urls:
        raise ValueError("No play URL found in Douyin web API response")
    
    video_url = play_urls[0]
    author = target_item.get('author', {})
    
    # Extract cover image
    cover_urls = video_data.get('cover', {}).get('url_list', [])
    thumbnail = cover_urls[0] if cover_urls else ''
    
    metadata = {
        'id': target_item.get('aweme_id') or video_id,
        'title': target_item.get('desc') or 'No Title',
        'description': target_item.get('desc') or '',
        'thumbnail': thumbnail,
        'uploader': author.get('nickname') or author.get('uid') or 'Unknown',
        'duration': float(video_data.get('duration') or 0) / 1000.0,
        'raw_video_url': video_url,
        'extractor': 'Douyin'
    }
    
    logger.info(f"✅ Douyin web post API succeeded for {video_id}: {metadata['title'][:50]}")
    return {
        'metadata': metadata,
        'cookie_header': cookie_str
    }


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


def parse_video_douyin_abogus(url: str) -> dict:
    """Primary parser that extracts 1080P/4K no-watermark video directly from Douyin Web Detail API using a_bogus signature and Chrome TLS impersonation."""
    logger.info(f"Using a_bogus Web API parser for URL: {url}")

    ua = ABogus.DEFAULT_USER_AGENT
    mobile_ua = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'
    
    # Step 1: Follow redirects to obtain canonical URL
    if HAS_CURL_CFFI:
        s = cffi_requests.Session(impersonate="chrome131")
        resp = s.get(url, headers={'User-Agent': ua}, allow_redirects=True, timeout=12)
        final_url = str(resp.url)
        resp_text = resp.text
    else:
        with httpx.Client(headers={'User-Agent': ua}, follow_redirects=True, timeout=12) as client:
            resp = client.get(url)
            final_url = str(resp.url)
            resp_text = resp.text
        
    # Step 2: Extract aweme_id from final_url or page response
    video_id_match = re.search(r'video/(\d+)', final_url)
    if not video_id_match:
        video_id_match = re.search(r'note/(\d+)', final_url)
    if not video_id_match:
        video_id_match = re.search(r'video/(\d+)', resp_text)
    if not video_id_match:
        video_id_match = re.search(r'note/(\d+)', resp_text)

    video_id = video_id_match.group(1) if video_id_match else ""
    if not video_id:
        raise ValueError(f"Could not extract Douyin video ID from URL: {final_url}")

    # Step 3: Extract fresh ttwid from iesdouyin share page with Chrome impersonation
    ttwid = None
    share_url = f"https://www.iesdouyin.com/share/video/{video_id}/"
    try:
        if HAS_CURL_CFFI:
            s_share = cffi_requests.Session(impersonate="chrome131")
            r_share = s_share.get(share_url, headers={'User-Agent': mobile_ua}, timeout=10)
            ttwid = r_share.cookies.get('ttwid')
            if not ttwid:
                for k, v in r_share.headers.items():
                    if k.lower() == 'set-cookie':
                        m = re.search(r'ttwid=([^;]+)', v)
                        if m:
                            ttwid = m.group(1)
                            break
        else:
            with httpx.Client(headers={'User-Agent': mobile_ua}, timeout=10) as client:
                r_share = client.get(share_url)
                ttwid = r_share.cookies.get('ttwid')
                if not ttwid:
                    for header_val in r_share.headers.get_list('set-cookie'):
                        m = re.search(r'ttwid=([^;]+)', header_val)
                        if m:
                            ttwid = m.group(1)
                            break
    except Exception as ttwid_err:
        logger.warning(f"Could not fetch ttwid from iesdouyin share page: {ttwid_err}")

    # Step 4: Combine ttwid and any local cookies (cookies.txt)
    cookie_parts = []
    if ttwid:
        cookie_parts.append(f"ttwid={ttwid}")

    cookies_path = 'cookies.txt'
    if os.path.exists(cookies_path) and os.path.getsize(cookies_path) > 0:
        try:
            with open(cookies_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith('#') or not line.strip():
                        continue
                    p = line.strip().split('\t')
                    if len(p) >= 7 and 'douyin' in p[0]:
                        cookie_parts.append(f"{p[5]}={p[6]}")
        except Exception:
            pass

    cookie_header_str = '; '.join(cookie_parts)

    # Step 5: Build Web API query parameters and calculate a_bogus signature
    params_dict = {
        'device_platform': 'webapp',
        'aid': '6383',
        'channel': 'channel_pc_web',
        'aweme_id': video_id,
        'update_version_code': '170400',
        'pc_client_type': '1',
        'pc_libra_divert': 'Windows',
        'version_code': '190500',
        'version_name': '19.5.0',
        'cookie_enabled': 'true',
        'screen_width': '1920',
        'screen_height': '1080',
        'browser_language': 'zh-CN',
        'browser_platform': 'Win32',
        'browser_name': 'Edge',
        'browser_version': '131.0.0.0',
        'browser_online': 'true',
        'engine_name': 'Blink',
        'engine_version': '131.0.0.0',
        'os_name': 'Windows',
        'os_version': '10',
        'cpu_core_num': '12',
        'device_memory': '8',
        'platform': 'PC',
        'downlink': '10',
        'effective_type': '4g',
        'round_trip_time': '50'
    }

    params_str = urllib.parse.urlencode(params_dict)
    signer = ABogus(user_agent=ua)
    signed_params, a_bogus_token, _, _ = signer.generate_abogus(params=params_str)

    api_url = f"https://www.douyin.com/aweme/v1/web/aweme/detail/?{signed_params}"
    headers = {
        'User-Agent': ua,
        'Referer': f'https://www.douyin.com/video/{video_id}',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Sec-Ch-Ua': '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Cookie': cookie_header_str
    }

    if HAS_CURL_CFFI:
        s_api = cffi_requests.Session(impersonate="chrome131")
        api_resp = s_api.get(api_url, headers=headers, timeout=12)
        resp_status = api_resp.status_code
        resp_text = api_resp.text
    else:
        with httpx.Client(timeout=12) as client:
            api_resp = client.get(api_url, headers=headers)
            resp_status = api_resp.status_code
            resp_text = api_resp.text

    if not resp_text or not resp_text.strip():
        raise ValueError(f"Empty response returned from Douyin Web Detail API (HTTP {resp_status})")

    try:
        data = api_resp.json()
    except Exception:
        raise ValueError(f"Invalid JSON returned from Douyin API (HTTP {resp_status}): {resp_text[:80]}")

        detail = data.get('aweme_detail')
        if not detail:
            status_msg = data.get('status_msg') or 'No aweme_detail object in JSON'
            raise ValueError(f"Douyin detail API returned: {status_msg}")

        title = detail.get('desc') or 'No Title'
        nickname = detail.get('author', {}).get('nickname') or 'Unknown'
        video_data = detail.get('video', {})
        play_urls = video_data.get('play_addr', {}).get('url_list', [])
        
        # In case of bit_rate list with higher quality
        if not play_urls and video_data.get('bit_rate'):
            for br in video_data.get('bit_rate', []):
                br_urls = br.get('play_addr', {}).get('url_list', [])
                if br_urls:
                    play_urls = br_urls
                    break

        if not play_urls:
            raise ValueError("No playable CDN stream found in aweme_detail")

        video_url = play_urls[0]
        cover_urls = video_data.get('cover', {}).get('url_list', [])
        cover_url = cover_urls[0] if cover_urls else ''
        duration = float(video_data.get('duration') or 0) / 1000.0

        metadata = {
            'id': detail.get('aweme_id') or video_id,
            'title': title,
            'description': title,
            'thumbnail': cover_url,
            'uploader': nickname,
            'duration': duration,
            'raw_video_url': video_url,
            'extractor': 'Douyin'
        }

        logger.info(f"✅ a_bogus API parse succeeded for {video_id}: {title[:40]}")
        return {
            'metadata': metadata,
            'cookie_header': ''
        }


def parse_video_mobile_html(url: str) -> dict:
    """Fallback parser that extracts no-watermark video directly from Douyin mobile sharing HTML without cookies.
    
    This parser leverages window._ROUTER_DATA script tag inside Douyin mobile/reflow sharing pages.
    """
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

        # Step 3: Parse _ROUTER_DATA from HTML if present (Modern.js router data format)
        router_match = re.search(r'window\._ROUTER_DATA\s*=\s*(.+?)</script>', html)
        if router_match:
            try:
                data_str = router_match.group(1).strip()
                if data_str.endswith(';'):
                    data_str = data_str[:-1]
                data = json.loads(data_str)
                loader_data = data.get('loaderData', {})
                item_list = []
                for key, val in loader_data.items():
                    if val and 'videoInfoRes' in val:
                        item_list = val['videoInfoRes'].get('item_list', [])
                        if item_list:
                            break
                if item_list:
                    item = item_list[0]
                    video_data = item.get('video', {})
                    play_urls = video_data.get('play_addr', {}).get('url_list', [])
                    if play_urls:
                        video_url = play_urls[0]
                        no_watermark_url = video_url.replace("playwm", "play")
                        if no_watermark_url.startswith("//"):
                            no_watermark_url = "https:" + no_watermark_url
                        
                        title = item.get('desc') or 'No Title'
                        nickname = item.get('author', {}).get('nickname') or 'Unknown'
                        cover_urls = video_data.get('cover', {}).get('url_list', [])
                        cover_url = cover_urls[0] if cover_urls else ''
                        duration = float(video_data.get('duration') or 0) / 1000.0

                        metadata = {
                            'id': item.get('aweme_id') or video_id,
                            'title': title,
                            'description': title,
                            'thumbnail': cover_url,
                            'uploader': nickname,
                            'duration': duration,
                            'raw_video_url': no_watermark_url,
                            'extractor': 'Douyin'
                        }
                        logger.info(f"✅ Extracted metadata from _ROUTER_DATA for video {video_id}")
                        return {
                            'metadata': metadata,
                            'cookie_header': ''
                        }
            except Exception as router_err:
                logger.warning(f"Failed parsing _ROUTER_DATA: {str(router_err)}")

        # Step 4: Parse RENDER_DATA from HTML if present
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

        # Step 5: Scraper regex fallback
        play_addr_match = re.search(r'"playAddr"\s*:\s*"([^"]+)"', html)
        if not play_addr_match:
            play_addr_match = re.search(r'playwm[^"]+', html)
            if play_addr_match:
                matched_str = play_addr_match.group(0)
                # Unescape slashes first to prevent regex cutting it short
                matched_str = matched_str.replace('\\u002F', '/').replace('\\/', '/')
                if matched_str.startswith("//"):
                    matched_str = "https:" + matched_str
                # Use it directly as no-watermark replacement
                no_watermark_url = matched_str.replace("playwm", "play")
                
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
    """Uses various fallback parsers to extract video metadata and download URL.
    
    1. Mobile HTML Scraper (uses _ROUTER_DATA, fast, no cookies/signatures needed for public videos).
    2. Local yt-dlp (uses cookies.txt if available, supports private/logged-in user content).
    3. Douyin web post API (uses cookies, checks user's own latest posts to match the requested video).
    4. Fallbacks (douyin.wtf, pearktrue.cn).
    """
    # Define yt-dlp options (used as second option)
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

    # Special handling for Twitter/X URLs to bypass Mobile HTML scraper and Douyin fallback paths
    is_twitter = any(domain in url for domain in ["x.com", "twitter.com"])
    if is_twitter:
        logger.info(f"Using direct yt-dlp parsing for Twitter/X URL: {url}")
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

                extractor_name = info.get('extractor') or ''
                if extractor_name.lower() in ['twitter', 'twitter:legacy']:
                    extractor_name = 'Twitter / X'

                metadata = {
                    'id': info.get('id') or '',
                    'title': info.get('title') or info.get('description') or 'No Title',
                    'description': info.get('description') or '',
                    'thumbnail': info.get('thumbnail') or (info.get('thumbnails')[-1]['url'] if info.get('thumbnails') else ''),
                    'uploader': info.get('uploader') or info.get('uploader_id') or 'Unknown',
                    'duration': float(info.get('duration') or 0),
                    'raw_video_url': video_url,
                    'extractor': extractor_name
                }

                return {
                    'metadata': metadata,
                    'cookie_header': cookie_header
                }
        except Exception as ytdlp_err:
            logger.warning(f"yt-dlp parsing failed for Twitter/X: {str(ytdlp_err)[:100]}. Trying FixTweet API fallback...")
            try:
                tweet_id_match = re.search(r'status/(\d+)', url)
                if not tweet_id_match:
                    raise ValueError("Could not extract tweet ID from URL")
                
                tweet_id = tweet_id_match.group(1)
                api_url = f"https://api.fxtwitter.com/status/{tweet_id}"
                
                req = urllib.request.Request(
                    api_url,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                
                if data.get('code') != 200 or 'tweet' not in data:
                    raise ValueError(f"FixTweet API returned error: {data.get('message', 'Unknown error')}")
                
                tweet = data['tweet']
                media = tweet.get('media', {})
                videos = media.get('videos', [])
                if not videos:
                    videos = [m for m in media.get('all', []) if m.get('type') == 'video']
                
                if not videos:
                    raise ValueError("No video found in FixTweet response")
                
                # Get the highest bitrate or just the main video
                video = videos[0]
                video_url = video.get('url')
                
                # Try to get highest quality from formats list if available
                formats = video.get('formats', [])
                mp4_formats = [f for f in formats if f.get('container') == 'mp4' and f.get('url')]
                if mp4_formats:
                    # Sort by bitrate desc if available
                    mp4_formats.sort(key=lambda x: x.get('bitrate', 0), reverse=True)
                    video_url = mp4_formats[0]['url']
                
                if not video_url:
                    raise ValueError("Could not extract video download URL from FixTweet")
                
                metadata = {
                    'id': tweet.get('id') or tweet_id,
                    'title': tweet.get('text') or 'No Title',
                    'description': tweet.get('text') or '',
                    'thumbnail': video.get('thumbnail_url') or tweet.get('author', {}).get('avatar_url') or '',
                    'uploader': tweet.get('author', {}).get('name') or tweet.get('author', {}).get('screen_name') or 'Unknown',
                    'duration': float(video.get('duration') or 0),
                    'raw_video_url': video_url,
                    'extractor': 'Twitter / X'
                }
                
                return {
                    'metadata': metadata,
                    'cookie_header': ''
                }
            except Exception as api_err:
                logger.error(f"FixTweet API fallback failed: {str(api_err)}")
                raise ValueError(f"Twitter/X 解析失败。yt-dlp 报错: {str(ytdlp_err)[:100]}; 敏感内容解析服务报错: {str(api_err)[:100]}")

    # Step 1: Try Douyin a_bogus Web API (fastest, official 1080P/4K stream, zero login cookies needed)
    try:
        return parse_video_douyin_abogus(url)
    except Exception as abogus_err:
        logger.warning(f"a_bogus Web API parser failed: {str(abogus_err)[:100]}. Trying Mobile HTML Scraper...")

        # Step 2: Try Mobile HTML Scraper
        try:
            return parse_video_mobile_html(url)
        except Exception as mobile_err:
            logger.warning(f"Mobile HTML Scraper failed: {str(mobile_err)[:100]}. Trying local yt-dlp...")
            
            # Step 3: Try local yt-dlp
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

                    extractor_name = info.get('extractor') or ''
                    if extractor_name.lower() in ['twitter', 'twitter:legacy']:
                        extractor_name = 'Twitter / X'

                    metadata = {
                        'id': info.get('id') or '',
                        'title': info.get('title') or info.get('description') or 'No Title',
                        'description': info.get('description') or '',
                        'thumbnail': info.get('thumbnail') or (info.get('thumbnails')[-1]['url'] if info.get('thumbnails') else ''),
                        'uploader': info.get('uploader') or info.get('uploader_id') or 'Unknown',
                        'duration': float(info.get('duration') or 0),
                        'raw_video_url': video_url,
                        'extractor': extractor_name
                    }

                    return {
                        'metadata': metadata,
                        'cookie_header': cookie_header
                    }
            except Exception as ytdlp_err:
                logger.warning(f"yt-dlp parsing failed: {str(ytdlp_err)[:100]}. Trying Douyin web post API...")
                
                # Step 4: Try Douyin web post API (uses cookies, checks user's own latest posts)
                try:
                    return parse_video_douyin_web(url)
                except Exception as web_err:
                    logger.warning(f"Douyin web post API failed: {str(web_err)[:100]}. Trying pearktrue...")
                    
                    # Step 5: Try PearkTrue public API
                    try:
                        return parse_video_pearktrue(url)
                    except Exception as fallback_err_2:
                        logger.warning(f"PearkTrue failed: {str(fallback_err_2)[:80]}. Trying douyin.wtf...")
                        
                        # Step 6: Try douyin.wtf public API
                        try:
                            return parse_video_fallback(url)
                        except Exception as fallback_err_1:
                            logger.error(f"All parsers failed. abogus={str(abogus_err)[:50]} mobile={str(mobile_err)[:50]} ytdlp={str(ytdlp_err)[:50]}")
                            raise ValueError(f"解析失败: 抖音官方接口与备用解析均未返回有效视频流 (abogus: {str(abogus_err)[:60]})")



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
    finally:
        clean_memory()

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
            finally:
                clean_memory()

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


async def auto_refresh_douyin_tokens():
    """Background task that automatically refreshes short-lived Douyin tokens (msToken, ttwid)
    every 15 minutes. This eliminates the need to manually update cookies.
    The long-lived sessionid (30 days) remains unchanged; only the ephemeral tokens are refreshed.
    """
    cookies_path = 'cookies.txt'
    logger.info("Auto-refresh token task started. Will refresh msToken every 15 minutes.")

    # Wait a bit for initial startup before first refresh
    await asyncio.sleep(30)

    while True:
        try:
            if not os.path.exists(cookies_path) or os.path.getsize(cookies_path) == 0:
                logger.warning("Auto-refresh: cookies.txt not found or empty, skipping.")
                await asyncio.sleep(900)
                continue

            # --- Step 1: Read all existing cookies into a dict ---
            cookie_lines_map = {}  # name -> list of 7 tab-separated fields
            with open(cookies_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split('\t')
                    if len(parts) == 7:
                        name = parts[5]
                        cookie_lines_map[name] = parts

            if not cookie_lines_map:
                logger.warning("Auto-refresh: No valid cookies found, skipping.")
                await asyncio.sleep(900)
                continue

            # Build cookie header from existing cookies for the request
            cookie_header_str = '; '.join(f"{n}={p[6]}" for n, p in cookie_lines_map.items())

            # --- Step 2: Visit Douyin homepage to get a fresh msToken ---
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=20.0,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                    'Cookie': cookie_header_str,
                }
            ) as client:
                r = await client.get('https://www.douyin.com/')

                refreshed = []
                # Tokens to refresh from Set-Cookie response
                tokens_to_refresh = ['msToken', 'ttwid', '__ac_nonce', '__ac_signature', '__ac_referer']

                for name, value in r.cookies.items():
                    if name in tokens_to_refresh and value:
                        if name in cookie_lines_map:
                            # Update existing entry
                            cookie_lines_map[name][6] = value
                        else:
                            # Add new entry with standard Douyin cookie attributes
                            cookie_lines_map[name] = [
                                '.douyin.com', 'TRUE', '/', 'FALSE', '2147483647', name, value
                            ]
                        refreshed.append(name)

            # --- Step 3: Write updated cookies back to file ---
            if refreshed:
                lines = ['# Netscape HTTP Cookie File\n']
                for name, parts in cookie_lines_map.items():
                    lines.append('\t'.join(parts) + '\n')

                with open(cookies_path, 'w', encoding='utf-8') as f:
                    f.writelines(lines)

                logger.info(f"✅ Auto-refresh success: updated tokens {refreshed}")
            else:
                logger.warning("Auto-refresh: No new tokens received from Douyin. Cookies may be fully expired.")

        except Exception as e:
            logger.warning(f"Auto-refresh token task error: {str(e)}")

        # Refresh every 15 minutes (well within the ~30min expiry window)
        clean_memory()
        await asyncio.sleep(900)

@app.on_event("startup")
async def on_startup():
    # Register bot commands in Telegram Menu button
    if bot:
        try:
            await bot.set_my_commands([
                types.BotCommand("start", "开始使用 / 帮助说明"),
                types.BotCommand("settings", "配置接收模式 (直接返回/发送到频道)"),
            ])
            logger.info("Telegram Bot commands registered successfully.")
        except Exception as e:
            logger.error(f"Failed to register Telegram Bot commands: {str(e)}")

    # Start the self-ping keep alive task
    if RENDER_EXTERNAL_URL:
        asyncio.create_task(self_keep_alive())

    # Always start the auto-refresh token task (works both locally and on Render)
    asyncio.create_task(auto_refresh_douyin_tokens())
    logger.info("Auto-refresh token task scheduled.")

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


@app.get("/cookie-status")
async def cookie_status():
    """Check the current validity and age of cookies in cookies.txt."""
    cookies_path = 'cookies.txt'
    if not os.path.exists(cookies_path):
        return {"status": "missing", "message": "cookies.txt not found"}

    size = os.path.getsize(cookies_path)
    if size == 0:
        return {"status": "empty", "message": "cookies.txt is empty"}

    import time
    mtime = os.path.getmtime(cookies_path)
    age_minutes = (time.time() - mtime) / 60

    cookie_names = []
    with open(cookies_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                parts = line.split('\t')
                if len(parts) >= 7:
                    cookie_names.append(parts[5])

    has_session = any(n in cookie_names for n in ['sessionid', 'sessionid_ss', 'sid_tt'])
    has_mstoken = 'msToken' in cookie_names

    return {
        "status": "ok" if has_session else "no_session",
        "size_bytes": size,
        "last_updated_minutes_ago": round(age_minutes, 1),
        "cookie_count": len(cookie_names),
        "has_session_id": has_session,
        "has_mstoken": has_mstoken,
        "cookie_names": cookie_names,
        "message": (
            "✅ Cookies look healthy" if (has_session and has_mstoken)
            else "⚠️ Missing sessionid - you need to re-login"
            if not has_session
            else "⚠️ Missing msToken - auto-refresh should fix this shortly"
        )
    }

COOKIES_UPDATE_TOKEN = os.getenv("COOKIES_UPDATE_TOKEN", "")

@app.post("/update-cookies")
async def update_cookies_endpoint(request: Request):
    """Securely receive and update cookies.txt from a remote push (e.g., local shell script).
    Requires the COOKIES_UPDATE_TOKEN header to match the env var COOKIES_UPDATE_TOKEN.
    """
    # Require auth token if configured
    if COOKIES_UPDATE_TOKEN:
        req_token = request.headers.get("X-Update-Token", "")
        if req_token != COOKIES_UPDATE_TOKEN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid update token")

    try:
        body = await request.json()
        raw_cookies = body.get("cookies", "")
        if not raw_cookies:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No cookies provided")

        # Clean and validate
        cleaned = raw_cookies.replace("\\n", "\n").replace("\\t", "\t").strip()
        if "Netscape HTTP Cookie File" not in cleaned:
            cleaned = "# Netscape HTTP Cookie File\n" + cleaned

        cookies_path = "cookies.txt"
        with open(cookies_path, "w", encoding="utf-8") as f:
            f.write(cleaned + "\n")

        sanitize_and_bridge_cookies(cookies_path)
        size = os.path.getsize(cookies_path)
        logger.info(f"✅ cookies.txt updated remotely via /update-cookies endpoint ({size} bytes)")

        return {
            "status": "ok",
            "message": f"Cookies updated successfully ({size} bytes)",
            "cookie_count": sum(1 for line in cleaned.split("\n") if line.strip() and not line.strip().startswith("#"))
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update cookies via endpoint: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.post("/tg-webhook/{token}")
async def tg_webhook(token: str, request: Request, background_tasks: BackgroundTasks):
    if not bot or token != TG_BOT_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized or Bot not active"
        )
    try:
        body = await request.body()
        json_string = body.decode("utf-8")
        update = types.Update.de_json(json_string)
        # Offload update processing to a background task so we return 200 OK immediately.
        # This prevents Telegram from retrying the request due to connection timeouts.
        background_tasks.add_task(bot.process_new_updates, [update])
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error scheduling Telegram update: {str(e)}")
        return {"status": "error", "message": str(e)}


# Define Bot handlers only if bot is initialized
if bot:
    @bot.message_handler(commands=['start', 'help'])
    async def send_welcome(message):
        welcome_text = (
            "👋 **欢迎使用抖音 & TikTok & X (Twitter) 视频下载机器人！**\n\n"
            "直接向我发送抖音、TikTok 或 X (Twitter) 的分享链接（支持整段分享文本），我就会为您解析并获取高清视频。\n\n"
            "⚙️ **模式切换**：\n"
            "您可以通过点击下方的底部键盘按钮，即时切换接收模式（系统会记住您的选择，并在当前选中的模式后标有 ✅）。\n\n"
            "💡 示例链接：\n"
            "• `https://v.douyin.com/xxxx/`\n"
            "• `https://www.tiktok.com/@user/video/xxxx`\n"
            "• `https://x.com/user/status/xxxx`"
        )
        markup = get_user_keyboard_markup(message.chat.id)
        await bot.reply_to(message, welcome_text, reply_markup=markup, parse_mode="Markdown")

    @bot.message_handler(commands=['settings'])
    async def show_settings(message):
        current_mode = get_user_mode(message.chat.id)
        if current_mode == "direct":
            mode_text = "📥 **直接返回给您**"
        elif current_mode.startswith("channel:"):
            target_channel = current_mode.split(":", 1)[1]
            mode_text = f"📤 **发送至频道 {target_channel}**"
        else:
            mode_text = "📥 **直接返回给您**"
        
        private_channels = get_user_private_channels(message.chat.id)
        private_text = "\n".join([f"• `{c}`" for c in private_channels]) if private_channels else "*(无)*"
        
        welcome_text = (
            f"⚙️ **机器人接收设置**\n\n"
            f"当前投递模式：{mode_text}\n\n"
            f"🌐 **系统公共频道**：\n"
            f"{' • ' + ', '.join(TG_CHANNELS) if TG_CHANNELS else '*(无)*'}\n\n"
            f"🔒 **您的私有频道**：\n{private_text}\n\n"
            f"💡 **私有频道绑定命令**：\n"
            f"你可以向机器人发送以下指令来自主管理：\n"
            f"1. `/addchannel <频道用户名或ID>` 来绑定私有频道\n"
            f"   *(例如：`/addchannel @my_private_channel`)*\n"
            f"2. `/delchannel <频道用户名或ID>` 来解除绑定\n"
            f"   *(例如：`/delchannel @my_private_channel`)*\n\n"
            f"*(注意：绑定的私有频道必须将机器人设为管理员并赋予发布消息权限。)*\n\n"
            f"您可以通过轻点下方的底部键盘按钮来切换接收模式。"
        )
        markup = get_user_keyboard_markup(message.chat.id)
        await bot.reply_to(message, welcome_text, reply_markup=markup, parse_mode="Markdown")

    @bot.message_handler(func=lambda message: message.text and ("直接返回给您" in message.text or "发送到频道" in message.text or "发送至" in message.text))
    async def handle_settings_toggle(message):
        chat_id = message.chat.id
        text = message.text

        user_private_channels = get_user_private_channels(chat_id)
        all_channels = TG_CHANNELS + user_private_channels

        if "直接返回给您" in text:
            set_user_mode(chat_id, "direct")
            reply_text = "✨ 设置已更新！解析后的视频将**直接在聊天中发送给您**。"
        elif "发送到频道" in text:
            if not all_channels:
                await bot.reply_to(message, "⚠️ 未配置任何目标频道，无法切换到该模式。")
                return
            set_user_mode(chat_id, f"channel:{all_channels[0]}")
            reply_text = f"✨ 设置已更新！解析后的视频将**同步发送至频道 {all_channels[0]}**。"
        elif "发送至" in text:
            try:
                # Extract channel name: remove emoji, "发送至" and "✅"
                clean_text = text.replace("📤", "").strip()
                parts = clean_text.split("发送至")
                if len(parts) < 2:
                    raise ValueError("Invalid button text format")
                channel_part = parts[1].strip()
                channel = channel_part.replace("✅", "").strip()
                
                if channel not in all_channels:
                    await bot.reply_to(message, f"⚠️ 频道 {channel} 不是可用的配置频道，请重新选择。")
                    return
                    
                set_user_mode(chat_id, f"channel:{channel}")
                reply_text = f"✨ 设置已更新！解析后的视频将**同步发送至频道 {channel}**。"
            except Exception as e:
                logger.error(f"Failed to parse channel from text '{text}': {e}")
                await bot.reply_to(message, "⚠️ 无法解析选中的频道，请重新选择。")
                return
        else:
            return

        markup = get_user_keyboard_markup(chat_id)
        await bot.reply_to(message, reply_text, reply_markup=markup, parse_mode="Markdown")

    @bot.message_handler(commands=['addchannel'])
    async def handle_add_channel(message):
        chat_id = message.chat.id
        text = message.text.strip()
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await bot.reply_to(message, "⚠️ 使用格式不正确，请输入：`/addchannel <频道用户名或ID>`", parse_mode="Markdown")
            return
            
        new_channel = parts[1].strip()
        
        # Validation: must start with @ or be a negative integer
        is_valid = False
        if new_channel.startswith("@"):
            is_valid = True
        else:
            try:
                val = int(new_channel)
                if val < 0:
                    is_valid = True
            except ValueError:
                pass
                
        if not is_valid:
            await bot.reply_to(message, "⚠️ 频道格式无效。必须以 `@` 开头（如 `@my_channel`）或者是负数频道 ID（如 `-100123456789`）。")
            return
            
        if new_channel in TG_CHANNELS:
            await bot.reply_to(message, "⚠️ 该频道已经是系统公共频道，无需重复添加。")
            return
            
        added = add_user_private_channel(chat_id, new_channel)
        if added:
            # Refresh keyboard
            markup = get_user_keyboard_markup(chat_id)
            await bot.reply_to(message, f"✅ 已成功绑定私有频道：`{new_channel}`！\n您现在可以在下方键盘选择该频道进行投递了。", reply_markup=markup, parse_mode="Markdown")
        else:
            await bot.reply_to(message, "⚠️ 该频道已在您的私有频道列表中。")

    @bot.message_handler(commands=['delchannel'])
    async def handle_del_channel(message):
        chat_id = message.chat.id
        text = message.text.strip()
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await bot.reply_to(message, "⚠️ 使用格式不正确，请输入：`/delchannel <频道用户名或ID>`", parse_mode="Markdown")
            return
            
        channel_to_del = parts[1].strip()
        removed = remove_user_private_channel(chat_id, channel_to_del)
        if removed:
            # If the user was currently in this mode, reset to direct
            current_mode = get_user_mode(chat_id)
            if current_mode == f"channel:{channel_to_del}":
                set_user_mode(chat_id, "direct")
                
            markup = get_user_keyboard_markup(chat_id)
            await bot.reply_to(message, f"✅ 已成功解绑私有频道：`{channel_to_del}`！", reply_markup=markup, parse_mode="Markdown")
        else:
            await bot.reply_to(message, "⚠️ 您的私有频道列表中未找到该频道。")

    async def process_video_download_and_upload(chat_id: int, target_url: str, status_msg, is_retry: bool = False):
        try:
            # Parse video using existing logic
            result = parse_video(target_url)
            metadata = result['metadata']
            cookie_header = result['cookie_header']
            raw_cdn_url = metadata['raw_video_url']

            await bot.edit_message_text(
                text="📥 视频解析成功，正在下载并准备无水印高清视频文件...",
                chat_id=chat_id,
                message_id=status_msg.message_id
            )

            # Determine target for video based on user preferences and configuration
            user_mode = get_user_mode(chat_id)
            is_uploading_to_channel = user_mode.startswith("channel:")
            if is_uploading_to_channel:
                target_channel = user_mode.split(":", 1)[1]
            else:
                target_channel = chat_id
            target_chat = target_channel

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
                if actual_size == 0:
                    raise ValueError("下载 of 视频文件大小为 0 字节，可能下载失败")
                if actual_size > 50 * 1024 * 1024:
                    raise ValueError("视频下载文件实际大小超过 50MB 限制")

                upload_msg_text = "📤 正在上传视频到频道..." if is_uploading_to_channel else "📤 正在上传视频到 Telegram..."
                await bot.edit_message_text(
                    text=upload_msg_text,
                    chat_id=chat_id,
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
                        supports_streaming=True,
                        timeout=300  # Extend upload timeout to 5 minutes to prevent transient failures on Render
                    )

                # Delete the loading status message
                await bot.delete_message(chat_id=chat_id, message_id=status_msg.message_id)

                # Send final confirmation message to user if synced to channel
                if is_uploading_to_channel:
                    await bot.send_message(chat_id=chat_id, text=f"🎉 视频解析成功，已发送至频道 {target_channel}！")

            except Exception as dl_upload_err:
                logger.warning(f"Failed to post video directly: {str(dl_upload_err)}")

                # Build download link using fallback configuration (RENDER_EXTERNAL_URL)
                base_url = RENDER_EXTERNAL_URL.rstrip('/') + '/' if RENDER_EXTERNAL_URL else "http://localhost:8000/"
                encoded_cdn = urllib.parse.quote(raw_cdn_url)
                encoded_cookies = urllib.parse.quote(cookie_header)
                encoded_orig = urllib.parse.quote(target_url)

                proxy_download_url = f"{base_url}stream?url={encoded_cdn}&cookies={encoded_cookies}&referer={encoded_orig}&download=1"

                # Truncate and clean the error message for markdown
                err_details = str(dl_upload_err)
                escaped_err = err_details.replace("*", "\\*").replace("_", "\\_").replace("[", "\\[").replace("]", "\\]").replace("`", "\\`")
                if len(escaped_err) > 150:
                    escaped_err = escaped_err[:147] + "..."

                fallback_text = (
                    f"🎬 **{metadata.get('title', '视频解析成功')}**\n\n"
                    f"👤 作者: {metadata.get('uploader', '未知')}\n"
                    f"⏱️ 时长: {metadata.get('duration', 0)}秒\n\n"
                    f"⚠️ 因视频文件过大 (>50MB) 或机器人权限受限，未能直接上传视频文件。\n"
                    f"*(错误详情: {escaped_err})*\n"
                    f"🔗 您可以点击下方按钮直接下载或重新上传："
                )

                retry_id = cache_retry_url(target_url)
                markup = types.InlineKeyboardMarkup()
                dl_btn = types.InlineKeyboardButton(text="📥 点击下载无水印视频", url=proxy_download_url)
                retry_btn = types.InlineKeyboardButton(text="🔄 重新尝试上传", callback_data=f"retry_up:{retry_id}")
                markup.row(dl_btn)
                markup.row(retry_btn)

                if is_uploading_to_channel:
                    try:
                        # Try to post the link fallback message to the channel
                        await bot.send_message(
                            chat_id=target_channel,
                            text=fallback_text,
                            reply_markup=markup,
                            parse_mode="Markdown"
                        )
                        await bot.edit_message_text(
                            text=f"🎉 视频解析成功！但因文件过大或权限受限未能直接上传视频，已将下载链接同步发布到频道 {target_channel}。\n\n*(错误详情: {escaped_err})*",
                            chat_id=chat_id,
                            message_id=status_msg.message_id,
                            reply_markup=markup,
                            parse_mode="Markdown"
                        )
                    except Exception as chan_err:
                        logger.error(f"Failed to send fallback message to channel {target_channel}: {str(chan_err)}")
                        await bot.edit_message_text(
                            text=fallback_text + f"\n\n*(发送至频道失败，请确保机器人已成为频道 {target_channel} 的管理员。)*",
                            chat_id=chat_id,
                            message_id=status_msg.message_id,
                            reply_markup=markup,
                            parse_mode="Markdown"
                        )
                else:
                    await bot.edit_message_text(
                        text=fallback_text,
                        chat_id=chat_id,
                        message_id=status_msg.message_id,
                        reply_markup=markup,
                        parse_mode="Markdown"
                    )
            finally:
                # Remove temp file
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception as cleanup_err:
                        logger.error(f"Failed to remove temp file: {str(cleanup_err)}")
                clean_memory()

        except Exception as parse_error:
            logger.error(f"Bot handler parse error: {str(parse_error)}")
            error_msg = clean_error_message(str(parse_error))
            await bot.edit_message_text(
                text=f"❌ 解析失败\n\n原因: {error_msg}",
                chat_id=chat_id,
                message_id=status_msg.message_id
            )

    @bot.message_handler(func=lambda message: True)
    async def handle_message(message):
        text = message.text
        if not text:
            return

        try:
            target_url = extract_http_url(text)
        except ValueError:
            await bot.reply_to(message, "⚠️ 未在您的消息中检测到有效的链接，请发送正确的抖音、TikTok 或 X (Twitter) 分享文本。")
            return

        # Simple verification of domains
        if not any(domain in target_url for domain in ["douyin.com", "tiktok.com", "amemv.com", "x.com", "twitter.com"]):
            await bot.reply_to(message, "⚠️ 该链接不属于支持的平台（抖音/TikTok/X），请检查后重新发送。")
            return

        status_msg = await bot.reply_to(message, "⏳ 正在解析链接，请稍候...")
        await process_video_download_and_upload(message.chat.id, target_url, status_msg)

    @bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("retry_up:"))
    async def handle_retry_callback(call):
        retry_id = call.data.split(":")[1]
        target_url = get_cached_retry_url(retry_id)
        if not target_url:
            await bot.answer_callback_query(call.id, text="⚠️ 该重试链接已失效或超时，请重新发送原链接进行解析。", show_alert=True)
            return

        # Let the user know we are retrying
        await bot.answer_callback_query(call.id, text="🔄 正在重新尝试上传...")
        
        # We edit the message with the retry button to show it is loading
        try:
            await bot.edit_message_text(
                text="⏳ 正在重新解析链接并尝试上传视频，请稍候...",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id
            )
        except Exception as edit_err:
            logger.error(f"Failed to edit callback message text: {edit_err}")
            
        await process_video_download_and_upload(call.message.chat.id, target_url, call.message, is_retry=True)

import os
import re
import json
import logging
import urllib.parse
import urllib.request
from fastapi import FastAPI, HTTPException, status, Query, Request
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field
import httpx
import yt_dlp

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="TikTok & Douyin Video Parsing API",
    description="API to extract no-watermark video URLs from TikTok and Douyin using FastAPI + yt-dlp",
    version="1.0.0"
)

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

def parse_video(url: str) -> dict:
    """Uses yt-dlp to extract video metadata and direct URL along with session cookies."""
    ydl_opts = {
        'format': 'best',
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
    }
    
    # Use cookies if available
    cookies_path = 'cookies.txt'
    if os.path.exists(cookies_path) and os.path.getsize(cookies_path) > 0:
        ydl_opts['cookiefile'] = cookies_path
        logger.info(f"Using cookies from {cookies_path}")
        
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
        logger.warning(f"Local yt-dlp parsing failed: {str(e)}. Swapping to fallback API...")
        try:
            return parse_video_fallback(url)
        except Exception as fallback_err:
            logger.error(f"Fallback parsing also failed: {str(fallback_err)}")
            # Raise original error to represent the primary failure reason
            raise e

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

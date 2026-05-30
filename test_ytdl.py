import yt_dlp
import sys
from main import sanitize_and_bridge_cookies

# Sanitize and bridge local cookies before run
sanitize_and_bridge_cookies("cookies.txt")

url = "https://v.douyin.com/4E1NKRefM_k/"
ydl_opts = {
    'format': 'best',
    'cookiefile': 'cookies.txt',
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
    }
}

print("Testing with cookiefile and Custom User-Agent...")
try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        print("SUCCESS! Title:", info.get('title'))
except Exception as e:
    print("FAILED with Custom UA:", str(e))

print("\nTesting with raw yt-dlp defaults (no custom UA)...")
ydl_opts_raw = {
    'format': 'best',
    'cookiefile': 'cookies.txt',
}
try:
    with yt_dlp.YoutubeDL(ydl_opts_raw) as ydl:
        info = ydl.extract_info(url, download=False)
        print("SUCCESS! Title:", info.get('title'))
except Exception as e:
    print("FAILED with default UA:", str(e))

print("\nTesting PearkTrue API directly using httpx...")
import httpx
import urllib.parse
try:
    api_url = f"https://api.pearktrue.cn/api/douyin/?url={urllib.parse.quote(url)}"
    r = httpx.get(api_url, headers={'User-Agent': 'Mozilla/5.0'}, verify=False, timeout=10.0)
    print("PearkTrue status:", r.status_code)
    data = r.json()
    print("PearkTrue response code:", data.get('code'))
    if data.get('code') == 200:
        print("PearkTrue returned video URL:", data.get('data', {}).get('video'))
    else:
        print("PearkTrue full response:", data)
except Exception as e:
    print("PearkTrue httpx FAILED:", str(e))

print("\nTesting with NO cookiefile (completely clean run)...")
ydl_opts_no_cookie = {
    'format': 'best',
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
}
try:
    with yt_dlp.YoutubeDL(ydl_opts_no_cookie) as ydl:
        info = ydl.extract_info(url, download=False)
        print("SUCCESS! Title:", info.get('title'))
except Exception as e:
    print("FAILED with NO cookiefile:", str(e))

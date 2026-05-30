import yt_dlp
import sys
import urllib.parse
from main import parse_video_mobile_html

url = "https://v.douyin.com/4E1NKRefM_k/"

print("Testing parse_video_mobile_html directly (completely NO cookie required)...")
try:
    res = parse_video_mobile_html(url)
    print("SUCCESS!")
    print("Video Title:", res['metadata']['title'])
    print("Uploader:", res['metadata']['uploader'])
    print("Raw Video URL (CDN):", res['metadata']['raw_video_url'][:80] + "...")
except Exception as e:
    print("FAILED:", str(e))

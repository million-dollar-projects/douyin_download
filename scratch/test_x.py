import yt_dlp
import json

url = "https://x.com/TianjiOracle/status/2081162833081884717/video/1"

ydl_opts = {
    'format': 'best',
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
}

try:
    print(f"Extracting info for URL: {url}...")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        print("Success! Extracted info keys:", list(info.keys()))
        print("ID:", info.get('id'))
        print("Title:", info.get('title'))
        print("Uploader:", info.get('uploader') or info.get('uploader_id') or info.get('user'))
        print("Duration:", info.get('duration'))
        print("Extractor:", info.get('extractor'))
        
        # Get video URL
        video_url = info.get('url')
        if not video_url and info.get('formats'):
            formats = info.get('formats', [])
            valid_formats = [f for f in formats if f.get('url')]
            if valid_formats:
                video_url = valid_formats[-1]['url']
        print("Video URL:", video_url[:100] + "..." if video_url else "None")
except Exception as e:
    print("Error:", e)

import sys
import os

# Add parent directory to path so we can import main
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from main import parse_video

url = "https://x.com/_GayFun_Car/status/2082206200960078110"

try:
    print(f"Calling parse_video with X NSFW URL: {url}...")
    res = parse_video(url)
    
    print("\n--- Parsing Result ---")
    print("Success!")
    metadata = res.get('metadata', {})
    print("ID:", metadata.get('id'))
    print("Title:", metadata.get('title'))
    print("Uploader:", metadata.get('uploader'))
    print("Duration:", metadata.get('duration'))
    print("Extractor:", metadata.get('extractor'))
    print("Raw Video URL:", metadata.get('raw_video_url'))
    print("Cookie Header:", res.get('cookie_header'))
    
    # Assertions to verify correctness
    assert metadata.get('extractor') == 'Twitter / X', f"Expected extractor to be 'Twitter / X', got '{metadata.get('extractor')}'"
    assert metadata.get('raw_video_url').startswith('http'), "Raw video URL should start with http"
    print("\n✅ Verification Successful: All checks passed!")
except Exception as e:
    print("\n❌ Verification Failed:", e)
    sys.exit(1)

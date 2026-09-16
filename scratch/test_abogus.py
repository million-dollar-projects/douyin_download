import urllib.parse
import json
import httpx
import re
from abogus import ABogus

def test_abogus_parse(short_or_long_url: str):
    print(f"Testing URL: {short_or_long_url}")
    
    # Step 1: Follow redirect if short link
    client = httpx.Client(follow_redirects=True, timeout=10)
    resp = client.get(short_or_long_url, headers={
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'
    })
    final_url = str(resp.url)
    print(f"Final URL: {final_url}")
    
    # Extract aweme_id
    match = re.search(r'/video/(\d+)', final_url)
    if not match:
        match = re.search(r'/video/(\d+)', resp.text)
    if not match:
        raise ValueError(f"Could not extract aweme_id from {final_url}")
    
    video_id = match.group(1)
    print(f"Extracted video_id: {video_id}")
    
    # Step 2: Sign with ABogus
    ua = ABogus.DEFAULT_USER_AGENT
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
    
    print(f"Generated a_bogus: {a_bogus_token}")
    api_url = f"https://www.douyin.com/aweme/v1/web/aweme/detail/?{signed_params}"
    
    headers = {
        'User-Agent': ua,
        'Referer': f'https://www.douyin.com/video/{video_id}',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Sec-Ch-Ua': '"Microsoft Edge";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
    }
    
    api_resp = client.get(api_url, headers=headers)
    print(f"API Response Code: {api_resp.status_code}, Length: {len(api_resp.text)}")
    
    if api_resp.text:
        data = api_resp.json()
        aweme_detail = data.get('aweme_detail')
        if aweme_detail:
            print("🎉 SUCCESSFUL PARSE VIA A_BOGUS!")
            print(f"Title: {aweme_detail.get('desc')}")
            print(f"Author: {aweme_detail.get('author', {}).get('nickname')}")
            video = aweme_detail.get('video', {})
            urls = video.get('play_addr', {}).get('url_list', [])
            if urls:
                print(f"No-Watermark Stream: {urls[0]}")
            return True
    return False

if __name__ == "__main__":
    test_abogus_parse("https://v.douyin.com/w6dXePahxrw/")

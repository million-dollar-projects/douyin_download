import sys
import os
import unittest
from unittest.mock import patch, MagicMock

# Add parent directory to path so we can import main
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Set mock env variables before importing main
os.environ["TELEGRAM_CHANNEL"] = "@renzhiup,@anotherchannel"

import main

class TestMultiChannelSupport(unittest.TestCase):
    def setUp(self):
        # Clear temporary prefs file if exists
        self.prefs_file = main.USER_PREFS_FILE
        if os.path.exists(self.prefs_file):
            os.remove(self.prefs_file)
            
    def tearDown(self):
        if os.path.exists(self.prefs_file):
            os.remove(self.prefs_file)

    def test_tg_channels_parsing(self):
        print("Testing TG_CHANNELS parsing...")
        self.assertEqual(main.TG_CHANNELS, ["@renzhiup", "@anotherchannel"])
        self.assertEqual(main.TG_CHANNEL, "@renzhiup")
        print("✅ TG_CHANNELS parsed correctly:", main.TG_CHANNELS)

    def test_get_user_mode(self):
        print("Testing get_user_mode...")
        chat_id = 123456
        
        # Test 1: Empty preference (defaults to channel:first_channel)
        mode = main.get_user_mode(chat_id)
        self.assertEqual(mode, "channel:@renzhiup")
        
        # Test 2: Legacy "channel" mode (should fallback to channel:first_channel)
        main.set_user_mode(chat_id, "channel")
        mode = main.get_user_mode(chat_id)
        self.assertEqual(mode, "channel:@renzhiup")
        
        # Test 3: Specific channel mode
        main.set_user_mode(chat_id, "channel:@anotherchannel")
        mode = main.get_user_mode(chat_id)
        self.assertEqual(mode, "channel:@anotherchannel")
        
        # Test 4: Direct mode
        main.set_user_mode(chat_id, "direct")
        mode = main.get_user_mode(chat_id)
        self.assertEqual(mode, "direct")
        
        # Test 5: Invalid/Obsolete channel (fallback to channel:first_channel)
        main.set_user_mode(chat_id, "channel:@obsolete")
        mode = main.get_user_mode(chat_id)
        self.assertEqual(mode, "channel:@renzhiup")
        
        print("✅ get_user_mode / set_user_mode verified successfully!")

    def test_get_user_keyboard_markup(self):
        print("Testing get_user_keyboard_markup...")
        chat_id = 987654
        
        # Setup mode to direct
        main.set_user_mode(chat_id, "direct")
        markup = main.get_user_keyboard_markup(chat_id)
        buttons = [b.get('text') if isinstance(b, dict) else b.text for row in markup.keyboard for b in row]
        self.assertIn("📥 直接返回给您 ✅", buttons)
        self.assertIn("📤 发送至 @renzhiup", buttons)
        self.assertIn("📤 发送至 @anotherchannel", buttons)
        
        # Setup mode to channel:@anotherchannel
        main.set_user_mode(chat_id, "channel:@anotherchannel")
        markup = main.get_user_keyboard_markup(chat_id)
        buttons = [b.get('text') if isinstance(b, dict) else b.text for row in markup.keyboard for b in row]
        self.assertIn("📥 直接返回给您", buttons)
        self.assertIn("📤 发送至 @renzhiup", buttons)
        self.assertIn("📤 发送至 @anotherchannel ✅", buttons)
        
        print("✅ get_user_keyboard_markup verified successfully!")

if __name__ == "__main__":
    unittest.main()

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

    def test_get_user_mode_with_private_channels(self):
        print("Testing get_user_mode with public and private channels...")
        chat_id = 123456
        
        # Test 1: Empty preference (defaults to channel:first_public_channel)
        mode = main.get_user_mode(chat_id)
        self.assertEqual(mode, "channel:@renzhiup")
        
        # Test 2: Add private channel
        added = main.add_user_private_channel(chat_id, "@my_private")
        self.assertTrue(added)
        
        # Verify private channel list
        privates = main.get_user_private_channels(chat_id)
        self.assertEqual(privates, ["@my_private"])
        
        # Test 3: Specific private channel selection
        main.set_user_mode(chat_id, "channel:@my_private")
        mode = main.get_user_mode(chat_id)
        self.assertEqual(mode, "channel:@my_private")
        
        # Test 4: Obsolete private channel fallback (if removed, goes to first available channel which is public @renzhiup)
        main.remove_user_private_channel(chat_id, "@my_private")
        mode = main.get_user_mode(chat_id)
        self.assertEqual(mode, "channel:@renzhiup")
        
        print("✅ get_user_mode / set_user_mode with private channels verified successfully!")

    def test_get_user_keyboard_markup_with_private_channels(self):
        print("Testing get_user_keyboard_markup with private channels...")
        chat_id = 987654
        
        # Add private channel
        main.add_user_private_channel(chat_id, "@my_private")
        
        # Setup mode to direct
        main.set_user_mode(chat_id, "direct")
        markup = main.get_user_keyboard_markup(chat_id)
        buttons = [b.get('text') if isinstance(b, dict) else b.text for row in markup.keyboard for b in row]
        self.assertIn("📥 直接返回给您 ✅", buttons)
        self.assertIn("📤 发送至 @renzhiup", buttons)
        self.assertIn("📤 发送至 @anotherchannel", buttons)
        self.assertIn("📤 发送至 @my_private", buttons)
        
        # Assert layout structure (should be 3 columns maximum per row)
        # We have 4 buttons total (1 direct, 2 public, 1 private) -> should result in 2 rows (3 buttons in row 1, 1 button in row 2)
        self.assertEqual(len(markup.keyboard), 2)
        self.assertEqual(len(markup.keyboard[0]), 3)
        self.assertEqual(len(markup.keyboard[1]), 1)
        
        # Setup mode to channel:@my_private
        main.set_user_mode(chat_id, "channel:@my_private")
        markup = main.get_user_keyboard_markup(chat_id)
        buttons = [b.get('text') if isinstance(b, dict) else b.text for row in markup.keyboard for b in row]
        self.assertIn("📥 直接返回给您", buttons)
        self.assertIn("📤 发送至 @renzhiup", buttons)
        self.assertIn("📤 发送至 @anotherchannel", buttons)
        self.assertIn("📤 发送至 @my_private ✅", buttons)
        
        print("✅ get_user_keyboard_markup with private channels verified successfully!")

if __name__ == "__main__":
    unittest.main()

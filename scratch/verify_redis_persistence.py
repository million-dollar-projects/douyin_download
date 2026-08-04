import sys
import os
import json
import unittest

# Add parent directory to path so we can import main
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Set mock env variables for Upstash
os.environ["UPSTASH_REDIS_REST_URL"] = "https://simple-pegasus-35519.upstash.io"
os.environ["UPSTASH_REDIS_REST_TOKEN"] = "AYq_AAIgcDExZTczMjg3YzY4ZWM0OTE3YWQ2MTJhNzVjYWUwODA1Zg"

import main

class TestRedisPersistence(unittest.TestCase):
    def test_redis_load_save(self):
        print("Testing Upstash Redis GET and SET endpoints integration...")
        
        # Ensure main has the environment variables imported
        self.assertEqual(main.UPSTASH_REDIS_REST_URL, "https://simple-pegasus-35519.upstash.io")
        self.assertEqual(main.UPSTASH_REDIS_REST_TOKEN, "AYq_AAIgcDExZTczMjg3YzY4ZWM0OTE3YWQ2MTJhNzVjYWUwODA1Zg")
        
        # Test Data
        test_prefs = {
            "12345": {
                "mode": "channel:@renzhiup",
                "private_channels": ["@renzhiup", "@my_private_channel"]
            },
            "67890": {
                "mode": "direct",
                "private_channels": []
            }
        }
        
        # Perform SET (save)
        print("Writing test preferences to Upstash Redis...")
        main.save_user_prefs(test_prefs)
        
        # Perform GET (load)
        print("Reading test preferences back from Upstash Redis...")
        loaded_prefs = main.load_user_prefs()
        
        # Assertions
        self.assertEqual(loaded_prefs, test_prefs)
        print("✅ Success! Data written and read back matches exactly.")
        print("Loaded data: ", json.dumps(loaded_prefs, indent=2))

if __name__ == "__main__":
    unittest.main()

import requests
import time
import os
from dotenv import load_dotenv

load_dotenv()

# The URL of your local or live site
SYNC_URL = "http://127.0.0.1:5000/cron/sync/"
TOKEN = os.getenv('CRON_SECRET_TOKEN', 'your-secret-123')

def run_sync():
    try:
        print(f"[{time.ctime()}] Starting Automated Sync...")
        response = requests.get(SYNC_URL + TOKEN)
        if response.status_code == 200:
            print("Success: Deals Updated.")
        else:
            print(f"Failed: {response.status_code}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    while True:
        run_sync()
        # Wait for 6 hours (6 * 60 * 60 seconds)
        print("Waiting 6 hours for next sync...")
        time.sleep(21600)
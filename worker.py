import time
import os
from dotenv import load_dotenv
from app import run_automation_cycle

load_dotenv()

SLEEP_SECONDS = int(os.getenv('AUTOMATION_INTERVAL_SECONDS', 21600))
PREFER_OFFLINE = os.getenv('AUTOMATION_PREFER_OFFLINE', 'false').lower() == 'true'

def run_sync():
    try:
        print(f"[{time.ctime()}] Starting automated sync (offline={PREFER_OFFLINE})...")
        result = run_automation_cycle('worker', prefer_offline=PREFER_OFFLINE)
        if result['success']:
            print(f"Success: {result['created_count']} deals processed; emails sent={result['emails_sent']}.")
        else:
            print(f"Failed: {result.get('error_message') or 'unknown error'}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    while True:
        run_sync()
        print(f"Waiting {SLEEP_SECONDS} seconds for next automation cycle...")
        time.sleep(SLEEP_SECONDS)

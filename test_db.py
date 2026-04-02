import os
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

try:
    print("Connecting to:", os.getenv("DB_HOST"))
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=int(os.getenv("DB_PORT")),
        database=os.getenv("DB_NAME")
    )
    print("✅ SUCCESS! Connection established.")
    db.close()
except Exception as e:
    print("❌ CONNECTION FAILED!")
    print(f"Error: {e}")
import os
import requests
import mysql.connector
import random
from dotenv import load_dotenv
from datetime import datetime, timedelta

# 1. Load variables from .env file
load_dotenv()

def sync_lifestyle_deals():
    print("🚀 Starting Sync: Fetching from FakeStoreAPI...")
    
    # 2. Fetching from the Free API
    try:
        url = "https://fakestoreapi.com/products"
        response = requests.get(url, timeout=10)
        all_items = response.json()
    except Exception as e:
        print(f"❌ Failed to fetch from API: {e}")
        return

    # 3. Connect to Aiven MySQL using Environment Variables
    try:
        db = mysql.connector.connect(
            host=os.getenv("DB_HOST"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME"),
            port=int(os.getenv("DB_PORT"))
        )
        cursor = db.cursor()
        print("✅ Connection to Aiven MySQL successful!")
    except Exception as e:
        print(f"❌ Database Connection failed: {e}")
        return

    count = 0
    # 4. Loop through items and insert
    for item in all_items:
        # Filter for the categories you wanted
        target_categories = ['electronics', "men's clothing", "women's clothing", "jewelery"]
        
        if item['category'] in target_categories:
            try:
                name = item['title']
                
                # Logic to create a "Flash Sale" look from static API data
                sale_price = float(item['price'])
                # Create an 'original price' that is 30-50% higher
                markup = random.uniform(1.3, 1.5)
                original_price = round(sale_price * markup, 2)
                
                # Calculate actual discount percentage for the UI
                discount = int(((original_price - sale_price) / original_price) * 100)
                
                img = item['image']
                category = item['category'].replace("'", "").capitalize() # Clean up names like "Men's"
                
                # Set random expiry (6 to 48 hours) to keep the 'Flash' vibe alive
                random_hours = random.randint(6, 48)
                expiry = datetime.now() + timedelta(hours=random_hours)
                
                # Meta-data for the UI
                rating = item['rating']['rate']
                reviews = item['rating']['count']
                views = random.randint(45, 600)
                is_trending = 1 if rating >= 4.0 else 0

                # 5. The SQL Query
                # Using 'REPLACE' or 'ON DUPLICATE KEY UPDATE' to avoid errors if item exists
                query = """
                    INSERT INTO deals 
                    (product_name, original_price, sale_price, discount_percentage, 
                     image_url, expiry_time, category, rating, review_count, live_views, is_trending)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                
                values = (
                    name, original_price, sale_price, discount, 
                    img, expiry, category, rating, reviews, views, is_trending
                )

                cursor.execute(query, values)
                count += 1
                print(f"Added: {name[:30]}...")

            except Exception as loop_error:
                # If one item fails (e.g. duplicate name), just skip it
                continue

    # 6. Save and Close
    db.commit()
    print(f"\n✨ FINISHED! {count} Fashion & Tech deals are now LIVE on your site.")
    
    cursor.close()
    db.close()

if __name__ == "__main__":
    sync_lifestyle_deals()
import requests
import mysql.connector
from datetime import datetime, timedelta

def sync_api_deals():
    # 1. Fetch from CheapShark (Free API)
    url = "https://www.cheapshark.com/api/1.0/deals?storeID=1&upperPrice=20"
    response = requests.get(url)
    api_deals = response.json()

    # 2. Connect to your Aiven MySQL
    db = mysql.connector.connect(
        host="your-aiven-host",
        user="your-user",
        password="your-password",
        database="your-db-name",
        port=12345
    )
    cursor = db.cursor()

    for item in api_deals[:10]:  # Let's grab the top 10
        name = item['title']
        sale_price = item['salePrice']
        orig_price = item['normalPrice']
        discount = int(float(item['savings']))
        img = item['thumb']
        # Set expiry to 24 hours from now
        expiry = datetime.now() + timedelta(days=1)
        
        # 3. Insert or Update Database
        query = """
            INSERT INTO deals (product_name, original_price, sale_price, discount_percentage, image_url, expiry_time, category)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE sale_price=%s, discount_percentage=%s
        """
        values = (name, orig_price, sale_price, discount, img, expiry, "Gaming", sale_price, discount)
        cursor.execute(query, values)

    db.commit()
    print(f"SUCCESS: {len(api_deals[:10])} deals synced!")
    cursor.close()
    db.close()

if __name__ == "__main__":
    sync_api_deals()
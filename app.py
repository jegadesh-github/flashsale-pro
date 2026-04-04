import os
import random
import requests
import time
import re
import json
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, has_request_context
import mysql.connector
from dotenv import load_dotenv
from functools import wraps
from urllib.parse import quote_plus, urlparse, parse_qsl, urlencode, urlunparse
from partner_sync import fetch_partner_deals

load_dotenv()

app = Flask(__name__)
HTTP_HEADERS = {"User-Agent": "DropDealsBot/1.0 (+https://dropdeals.local)"}
SCHEMA_READY = False
CACHE_DIR = Path(__file__).resolve().parent / "data"
OFFLINE_CACHE_FILE = CACHE_DIR / "offline_deals_cache.json"

# ─────────────────────────────────────────────────────────
#  TEMPLATES FILTERS
# ─────────────────────────────────────────────────────────

@app.template_filter('currency')
def currency_filter(value):
    try:
        if value is None or value == "" or str(value).lower() == 'none':
            return "₹0"
        
        # Strip existing formatting
        clean_value = str(value).replace('₹', '').replace(',', '').strip()
        num = int(float(clean_value))
        
        s = str(num)
        if len(s) <= 3: return f"₹{s}"
        
        last3 = s[-3:]
        rest = s[:-3]
        res = ""
        while len(rest) > 2:
            res = "," + rest[-2:] + res
            rest = rest[:-2]
        if rest: res = rest + res
        return f"₹{res},{last3}"
    except (ValueError, TypeError, Exception):
        return "₹0"

app.jinja_env.filters['currency'] = currency_filter

app.secret_key = os.getenv('SECRET_KEY', 'flashsale-pro-ultra-premium-2026')
app.permanent_session_lifetime = timedelta(hours=8)

# ─────────────────────────────────────────────────────────
#  DATABASE CONNECTION
# ─────────────────────────────────────────────────────────

def get_db_connection():
    try:
        connection = mysql.connector.connect(
            host=os.getenv("DB_HOST"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            port=int(os.getenv("DB_PORT", 3306)),
            database=os.getenv("DB_NAME"),
            connection_timeout=30
        )
        ensure_database_schema(connection)
        return connection
    except mysql.connector.Error as err:
        print(f"Database Connection Error: {err}")
        return None


def ensure_database_schema(db):
    global SCHEMA_READY
    if SCHEMA_READY:
        return

    cursor = db.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS deals (
            id INT AUTO_INCREMENT PRIMARY KEY,
            product_name VARCHAR(255) NOT NULL,
            original_price DECIMAL(12,2) DEFAULT 0,
            sale_price DECIMAL(12,2) DEFAULT 0,
            discount_percentage INT DEFAULT 0,
            category VARCHAR(120),
            affiliate_url TEXT,
            image_url TEXT,
            expiry_time DATETIME,
            is_active BOOLEAN DEFAULT TRUE,
            store_name VARCHAR(120),
            batch_id VARCHAR(120),
            is_trending BOOLEAN DEFAULT FALSE,
            is_mega BOOLEAN DEFAULT FALSE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clicks (
            id INT AUTO_INCREMENT PRIMARY KEY,
            deal_id INT NOT NULL,
            clicked_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            email VARCHAR(255) NOT NULL,
            deal_id INT NULL,
            target_price DECIMAL(12,2) NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INT AUTO_INCREMENT PRIMARY KEY,
            session_id VARCHAR(120),
            role VARCHAR(20) NOT NULL,
            message TEXT NOT NULL,
            detected_category VARCHAR(120),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_events (
            id INT AUTO_INCREMENT PRIMARY KEY,
            subscription_id INT,
            deal_id INT,
            event_type VARCHAR(60),
            status VARCHAR(40),
            sent_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS automation_runs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            run_type VARCHAR(80),
            status VARCHAR(40),
            summary TEXT,
            deals_processed INT DEFAULT 0,
            emails_sent INT DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    required_columns = {
        'deals': {
            'source_name': "VARCHAR(80)",
            'source_product_id': "VARCHAR(120)",
            'source_url': "TEXT",
            'merchant_name': "VARCHAR(120)",
            'source_rating': "DECIMAL(4,2) DEFAULT 0",
            'review_count': "INT DEFAULT 0",
            'trust_score': "INT DEFAULT 70",
            'merchandising_badge': "VARCHAR(120)",
            'ai_summary': "TEXT",
            'is_featured': "BOOLEAN DEFAULT FALSE",
            'currency_code': "VARCHAR(12) DEFAULT 'INR'",
            'live_views': "INT DEFAULT 0",
            'base_views': "INT DEFAULT 0",
        },
        'subscriptions': {
            'category': "VARCHAR(120) NULL",
            'is_active': "BOOLEAN DEFAULT TRUE",
            'last_notified_at': "DATETIME NULL",
            'created_at': "DATETIME DEFAULT CURRENT_TIMESTAMP",
            'unsubscribe_token': "VARCHAR(120) NULL",
            'unsubscribed_at': "DATETIME NULL",
        },
        'clicks': {
            'referrer': "VARCHAR(255)",
        },
    }

    for table_name, columns in required_columns.items():
        for column_name, definition in columns.items():
            cursor.execute(f"SHOW COLUMNS FROM `{table_name}` LIKE %s", (column_name,))
            if cursor.fetchone():
                continue
            try:
                cursor.execute(f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {definition}")
            except mysql.connector.Error:
                pass

    db.commit()
    cursor.close()
    SCHEMA_READY = True

# ─────────────────────────────────────────────────────────
#  AUTH DECORATORS
# ─────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash("Unauthorized access. Please login.", "danger")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ─────────────────────────────────────────────────────────
#  CONSTANTS & DATA
# ─────────────────────────────────────────────────────────

INFO_PAGES = {
    'about': {'title': 'About DropDeals', 'content': 'DropDeals curates limited-time offers and makes it easier for shoppers to discover worthwhile savings in one place.'},
    'privacy': {'title': 'Privacy Policy', 'content': 'We collect only the information needed to improve deal discovery, alerts, and site performance.'},
    'terms': {'title': 'Terms & Disclosures', 'content': 'DropDeals shares curated offers and may include sponsored or partner-linked promotions.'}
}

CATEGORY_KEYWORDS = {
    'Smartphones': ['phone','mobile','smartphone','iphone','android','samsung','oneplus','pixel'],
    'Laptops': ['laptop','computer','macbook','notebook','dell','hp','lenovo','asus'],
    'Fragrances': ['perfume','fragrance','cologne','scent','deo'],
    'Skincare': ['skincare','beauty','cream','serum','moisturizer','sunscreen','face'],
    'Groceries': ['grocery','food','groceries','tea','coffee'],
    'Home Decoration': ['home','decor','decoration','lamp','candle'],
    'Furniture': ['furniture','chair','table','sofa','bed','desk'],
}

CATEGORY_META = {
    'Smartphones': {'icon': 'fa-mobile-screen-button', 'label': 'Phone deals updated daily'},
    'Laptops': {'icon': 'fa-laptop', 'label': 'Work and gaming picks'},
    'Fragrances': {'icon': 'fa-spray-can-sparkles', 'label': 'Giftable luxury offers'},
    'Skincare': {'icon': 'fa-pump-soap', 'label': 'Beauty bestsellers'},
    'Groceries': {'icon': 'fa-basket-shopping', 'label': 'Household savings'},
    'Home Decoration': {'icon': 'fa-couch', 'label': 'Decor and living finds'},
    'Furniture': {'icon': 'fa-chair', 'label': 'Large-ticket home deals'},
}

PRIMARY_PRICE_TARGET = 3000
MAX_DEFAULT_SALE_PRICE = 30000

FAQ_ITEMS = [
    {
        'question': 'How often are offers refreshed?',
        'answer': 'Deals are refreshed throughout the day so the homepage keeps up with new price drops and fast-moving offers.',
    },
    {
        'question': 'Are the product names and images matched to the real items?',
        'answer': 'Product titles and images are kept aligned with the listed offers so it is easier to recognize what you are opening.',
    },
    {
        'question': 'Can visitors get deal alerts by email?',
        'answer': 'Yes. You can join the email list to hear about fresh deals, category favorites and upcoming drops.',
    },
]

CUSTOMER_QUOTES = [
    {
        'name': 'Aarav S.',
        'city': 'Bengaluru',
        'quote': 'I check DropDeals before buying gadgets now. The best finds show up without the clutter.',
    },
    {
        'name': 'Neha P.',
        'city': 'Mumbai',
        'quote': 'The home and beauty offers are easy to scan, and I can spot the real markdowns much faster.',
    },
    {
        'name': 'Rahul M.',
        'city': 'Pune',
        'quote': 'The trending picks and latest drops section save me time whenever I am hunting for a good deal.',
    },
]

DEMO_DEALS = [
    {
        'id': 10001,
        'product_name': 'boAt Airdopes 141 TWS Earbuds',
        'original_price': 2999.0,
        'sale_price': 1299.0,
        'discount_percentage': 57,
        'category': 'Smartphones',
        'affiliate_url': 'https://www.amazon.in/s?k=boAt+Airdopes+141&tag=dropdeals-21',
        'image_url': 'https://images.unsplash.com/photo-1572569511254-d8f925fe2cbb?auto=format&fit=crop&w=900&q=80',
        'expiry_time': datetime.now() + timedelta(hours=18),
        'is_active': True,
        'store_name': 'Amazon India',
        'is_trending': 1,
        'is_mega': 1,
        'created_at': datetime.now() - timedelta(hours=1),
    },
    {
        'id': 10002,
        'product_name': 'Minimalist Vitamin C Face Serum',
        'original_price': 699.0,
        'sale_price': 449.0,
        'discount_percentage': 36,
        'category': 'Skincare',
        'affiliate_url': 'https://www.amazon.in/s?k=Minimalist+Vitamin+C+Serum&tag=dropdeals-21',
        'image_url': 'https://images.unsplash.com/photo-1556228578-8c89e6adf883?auto=format&fit=crop&w=900&q=80',
        'expiry_time': datetime.now() + timedelta(hours=12),
        'is_active': True,
        'store_name': 'Amazon India',
        'is_trending': 1,
        'is_mega': 0,
        'created_at': datetime.now() - timedelta(hours=2),
    },
    {
        'id': 10003,
        'product_name': 'Bella Vita Luxury Perfume Gift Set',
        'original_price': 2199.0,
        'sale_price': 999.0,
        'discount_percentage': 55,
        'category': 'Fragrances',
        'affiliate_url': 'https://www.amazon.in/s?k=Bella+Vita+Luxury+Perfume&tag=dropdeals-21',
        'image_url': 'https://images.unsplash.com/photo-1541643600914-78b084683601?auto=format&fit=crop&w=900&q=80',
        'expiry_time': datetime.now() + timedelta(hours=30),
        'is_active': True,
        'store_name': 'Amazon India',
        'is_trending': 1,
        'is_mega': 1,
        'created_at': datetime.now() - timedelta(hours=3),
    },
    {
        'id': 10004,
        'product_name': 'Organic Essentials Grocery Combo Box',
        'original_price': 2499.0,
        'sale_price': 1699.0,
        'discount_percentage': 32,
        'category': 'Groceries',
        'affiliate_url': 'https://www.amazon.in/s?k=Organic+Grocery+Combo&tag=dropdeals-21',
        'image_url': 'https://images.unsplash.com/photo-1542838132-92c53300491e?auto=format&fit=crop&w=900&q=80',
        'expiry_time': datetime.now() + timedelta(hours=24),
        'is_active': True,
        'store_name': 'Amazon India',
        'is_trending': 1,
        'is_mega': 0,
        'created_at': datetime.now() - timedelta(hours=4),
    },
    {
        'id': 10005,
        'product_name': 'Noise ColorFit Pro Smartwatch',
        'original_price': 5999.0,
        'sale_price': 2999.0,
        'discount_percentage': 50,
        'category': 'Smartphones',
        'affiliate_url': 'https://www.amazon.in/s?k=Noise+ColorFit+Pro&tag=dropdeals-21',
        'image_url': 'https://images.unsplash.com/photo-1546868871-7041f2a55e12?auto=format&fit=crop&w=900&q=80',
        'expiry_time': datetime.now() + timedelta(hours=36),
        'is_active': True,
        'store_name': 'Amazon India',
        'is_trending': 1,
        'is_mega': 1,
        'created_at': datetime.now() - timedelta(hours=5),
    },
    {
        'id': 10006,
        'product_name': 'Philips Air Fryer 4.1L',
        'original_price': 11995.0,
        'sale_price': 7999.0,
        'discount_percentage': 33,
        'category': 'Home Decoration',
        'affiliate_url': 'https://www.amazon.in/s?k=Philips+Air+Fryer&tag=dropdeals-21',
        'image_url': 'https://images.unsplash.com/photo-1585515656443-4f22f3278ac0?auto=format&fit=crop&w=900&q=80',
        'expiry_time': datetime.now() + timedelta(hours=20),
        'is_active': True,
        'store_name': 'Amazon India',
        'is_trending': 0,
        'is_mega': 0,
        'created_at': datetime.now() - timedelta(hours=6),
    },
    {
        'id': 10007,
        'product_name': 'Lenovo IdeaPad Slim 3',
        'original_price': 42990.0,
        'sale_price': 28990.0,
        'discount_percentage': 33,
        'category': 'Laptops',
        'affiliate_url': 'https://www.amazon.in/s?k=Lenovo+IdeaPad+Slim+3&tag=dropdeals-21',
        'image_url': 'https://images.unsplash.com/photo-1517336714739-489689fd1ca8?auto=format&fit=crop&w=900&q=80',
        'expiry_time': datetime.now() + timedelta(hours=18),
        'is_active': True,
        'store_name': 'Amazon India',
        'is_trending': 0,
        'is_mega': 0,
        'created_at': datetime.now() - timedelta(hours=7),
    },
    {
        'id': 10008,
        'product_name': 'Godrej Interio Study Chair',
        'original_price': 7999.0,
        'sale_price': 5499.0,
        'discount_percentage': 31,
        'category': 'Furniture',
        'affiliate_url': 'https://www.amazon.in/s?k=Godrej+Interio+Study+Chair&tag=dropdeals-21',
        'image_url': 'https://images.unsplash.com/photo-1505843490701-5be5d3b4eb7f?auto=format&fit=crop&w=900&q=80',
        'expiry_time': datetime.now() + timedelta(hours=16),
        'is_active': True,
        'store_name': 'Amazon India',
        'is_trending': 0,
        'is_mega': 0,
        'created_at': datetime.now() - timedelta(hours=8),
    },
]


def normalize_deal(deal):
    normalized = dict(deal)
    normalized['original_price'] = float(normalized.get('original_price') or 0)
    normalized['sale_price'] = float(normalized.get('sale_price') or 0)
    normalized['discount_percentage'] = int(normalized.get('discount_percentage') or 0)
    normalized['source_rating'] = float(normalized.get('source_rating') or 0)
    normalized['review_count'] = int(normalized.get('review_count') or 0)
    normalized['trust_score'] = int(normalized.get('trust_score') or 0)
    normalized['savings'] = max(normalized['original_price'] - normalized['sale_price'], 0)
    normalized['expiry_iso'] = normalized.get('expiry_time').isoformat() if normalized.get('expiry_time') else ""
    normalized['outbound_url'] = normalized.get('affiliate_url', '#') if normalized.get('is_demo') else f"/go/{normalized['id']}" if normalized.get('id') else normalized.get('affiliate_url', '#')
    normalized['store_name'] = normalized.get('store_name') or normalized.get('merchant_name') or 'Partner Store'
    normalized['merchant_name'] = normalized.get('merchant_name') or normalized['store_name']
    normalized['source_domain'] = extract_domain(normalized.get('source_url', ''))
    normalized['is_hot'] = normalized['discount_percentage'] >= 25
    normalized['badge_text'] = normalized.get('merchandising_badge') or ('Mega Deal' if normalized.get('is_mega') else 'Trending' if normalized.get('is_trending') else 'Editor Pick')
    normalized['trust_label'] = 'High Confidence' if normalized['trust_score'] >= 85 else 'Checked'
    return normalized


def filter_demo_deals(search_query='', category=''):
    filtered = []
    for deal in DEMO_DEALS:
        sample_deal = dict(deal)
        sample_deal['is_demo'] = True
        if category and deal['category'] != category:
            continue
        if search_query:
            text = f"{deal['product_name']} {deal['category']}".lower()
            if search_query.lower() not in text:
                continue
        filtered.append(normalize_deal(sample_deal))
    return sorted(filtered, key=lambda item: item.get('discount_percentage', 0), reverse=True)


def prioritize_affordable_deals(deals):
    return sorted(
        deals,
        key=lambda item: (
            0 if item.get('sale_price', 0) <= PRIMARY_PRICE_TARGET else 1,
            0 if item.get('sale_price', 0) <= MAX_DEFAULT_SALE_PRICE else 1,
            -item.get('discount_percentage', 0),
            item.get('sale_price', 0),
        )
    )


def build_homepage_context(deals):
    deals = prioritize_affordable_deals(deals)
    categories = sorted({deal['category'] for deal in deals})
    affordable_core = [deal for deal in deals if deal.get('sale_price', 0) <= PRIMARY_PRICE_TARGET]
    mid_range = [deal for deal in deals if PRIMARY_PRICE_TARGET < deal.get('sale_price', 0) <= MAX_DEFAULT_SALE_PRICE]
    featured_pool = affordable_core or mid_range or deals
    featured_deals = sorted(featured_pool, key=lambda item: (item.get('is_featured', 0), item.get('trust_score', 0), item['discount_percentage']), reverse=True)[:4]
    trending_pool = affordable_core[:8] + mid_range[:6] if affordable_core else mid_range or deals
    trending_deals = sorted(trending_pool, key=lambda item: (item.get('is_trending', 0), item.get('review_count', 0), item['discount_percentage']), reverse=True)[:4] or featured_deals[:4]
    hero_deal = featured_deals[0] if featured_deals else None
    newest_deals = sorted(deals, key=lambda item: item.get('created_at', datetime.min), reverse=True)[:6]
    top_saved = sorted(mid_range or deals, key=lambda item: item['savings'], reverse=True)[:3]

    total_savings = sum(deal['savings'] for deal in deals)
    avg_discount = int(round(sum(deal['discount_percentage'] for deal in deals) / len(deals))) if deals else 0
    avg_trust_score = int(round(sum(deal.get('trust_score', 0) for deal in deals) / len(deals))) if deals else 0

    category_count = {}
    store_count = {}
    for deal in deals:
        category_count[deal['category']] = category_count.get(deal['category'], 0) + 1
        store_key = deal.get('store_name') or deal.get('merchant_name') or 'Partner Store'
        store_count[store_key] = store_count.get(store_key, 0) + 1

    top_category = max(category_count, key=category_count.get) if category_count else 'Smartphones'
    category_highlights = []
    for category in categories[:6]:
        meta = CATEGORY_META.get(category, {'icon': 'fa-tags', 'label': 'Fresh affiliate picks'})
        category_highlights.append({
            'name': category,
            'count': category_count.get(category, 0),
            'icon': meta['icon'],
            'label': meta['label'],
        })

    budget_ranges = [
        {'label': 'Under 999', 'value': 999},
        {'label': 'Under 2,999', 'value': 2999},
        {'label': 'Under 9,999', 'value': 9999},
        {'label': 'Under 29,999', 'value': 29999},
    ]
    budget_collections = []
    for item in budget_ranges:
        matching = [deal for deal in deals if deal['sale_price'] <= item['value']]
        budget_collections.append({
            'label': item['label'],
            'count': len(matching),
            'slug': str(item['value']),
        })

    live_strip_deals = sorted(
        deals,
        key=lambda item: (item.get('click_count', 0), item.get('discount_percentage', 0), item.get('created_at', datetime.min)),
        reverse=True
    )[:10] or deals[:10]

    popular_deals = sorted(
        affordable_core + mid_range if affordable_core else deals,
        key=lambda item: (item.get('click_count', 0), item.get('review_count', 0), item.get('discount_percentage', 0)),
        reverse=True
    )[:6] or deals[:6]

    return {
        'categories': categories,
        'featured_deals': featured_deals,
        'trending_deals': trending_deals,
        'hero_deal': hero_deal,
        'newest_deals': newest_deals,
        'top_saved': top_saved,
        'popular_deals': popular_deals,
        'live_strip_deals': live_strip_deals,
        'budget_collections': budget_collections,
        'category_highlights': category_highlights,
        'faq_items': FAQ_ITEMS,
        'customer_quotes': CUSTOMER_QUOTES,
        'chatbot_starters': [
            'Show me laptop deals under 50000',
            'Find trending skincare offers',
            'Suggest a giftable fragrance deal',
        ],
        'stats': {
            'deal_count': len(deals),
            'avg_discount': avg_discount,
            'total_savings': total_savings,
            'avg_trust_score': avg_trust_score,
            'store_count': len(store_count),
            'top_category': top_category,
            'primary_price_target': PRIMARY_PRICE_TARGET,
            'price_cap': MAX_DEFAULT_SALE_PRICE,
        }
    }


def extract_domain(value):
    try:
        return urlparse(value).netloc.lower().replace('www.', '')
    except Exception:
        return ""


def convert_usd_to_inr(value):
    return round(float(value or 0) * float(os.getenv("USD_TO_INR_RATE", "83.0")), 2)


def slugify(value):
    value = re.sub(r'[^a-zA-Z0-9]+', '-', (value or '').strip().lower())
    return value.strip('-') or 'deal'


def merge_query_params(url, params):
    parsed = urlparse(url)
    existing = dict(parse_qsl(parsed.query, keep_blank_values=True))
    existing.update({key: value for key, value in params.items() if value})
    return urlunparse(parsed._replace(query=urlencode(existing)))


def decorate_store_affiliate_url(url, merchant_name=''):
    if not url:
        return ""

    host = extract_domain(url)
    merchant = (merchant_name or '').lower()

    if 'amazon.' in host or 'amazon' in merchant:
        affiliate_tag = os.getenv("AMAZON_AFFILIATE_ID") or os.getenv("AFFILIATE_ID")
        return merge_query_params(url, {'tag': affiliate_tag}) if affiliate_tag else url

    if 'flipkart.com' in host or 'flipkart' in merchant:
        affiliate_id = os.getenv("FLIPKART_AFFILIATE_ID")
        return merge_query_params(url, {'affid': affiliate_id}) if affiliate_id else url

    return url


def build_affiliate_search_url(name, merchant_name='Amazon India'):
    query = quote_plus(name)
    merchant = (merchant_name or '').lower()
    if 'flipkart' in merchant and os.getenv("FLIPKART_AFFILIATE_ID"):
        return f"https://www.flipkart.com/search?q={query}&affid={os.getenv('FLIPKART_AFFILIATE_ID')}"

    affiliate_tag = os.getenv("AMAZON_AFFILIATE_ID") or os.getenv("AFFILIATE_ID") or "dropdeals-21"
    return f"https://www.amazon.in/s?k={query}&tag={affiliate_tag}"


def build_affiliate_url(product_name, source_url='', merchant_name='Amazon India'):
    decorated = decorate_store_affiliate_url(source_url, merchant_name)
    if decorated:
        return decorated
    return build_affiliate_search_url(product_name, merchant_name)


def get_base_url():
    configured = os.getenv("PUBLIC_BASE_URL") or os.getenv("APP_BASE_URL")
    if configured:
        return configured.rstrip('/')
    return request.url_root.rstrip('/') if has_request_context() else "http://127.0.0.1:5000"


def build_unsubscribe_url(token):
    return f"{get_base_url()}/unsubscribe/{token}"


def ensure_subscription_token(subscription_row):
    token = subscription_row.get('unsubscribe_token')
    if token:
        return token
    return f"sub_{subscription_row['id']}_{slugify(subscription_row.get('email', 'subscriber'))}"


def build_merchandising_fields(deal):
    discount = int(deal.get('discount_percentage') or 0)
    rating = float(deal.get('source_rating') or 0)
    reviews = int(deal.get('review_count') or 0)
    trust_score = min(98, max(55, int(60 + (discount * 0.45) + (rating * 5) + min(reviews, 500) / 30)))
    deal['trust_score'] = trust_score
    deal['is_featured'] = 1 if trust_score >= 85 or discount >= 40 else int(deal.get('is_featured') or 0)
    deal['is_trending'] = 1 if discount >= 20 or rating >= 4.4 or reviews >= 100 else int(deal.get('is_trending') or 0)
    deal['is_mega'] = 1 if discount >= 50 else int(deal.get('is_mega') or 0)
    if deal['is_mega']:
        deal['merchandising_badge'] = 'Mega Deal'
    elif deal['is_featured']:
        deal['merchandising_badge'] = 'Featured Pick'
    elif deal['is_trending']:
        deal['merchandising_badge'] = 'Trending Now'
    else:
        deal['merchandising_badge'] = 'Fresh Find'
    if rating:
        deal['ai_summary'] = f"{deal.get('category', 'Product')} offer from {deal.get('merchant_name') or deal.get('store_name') or deal.get('source_name', 'trusted source')} with {discount}% off, rating {rating:.1f} and {reviews} reviews."
    else:
        deal['ai_summary'] = f"{deal.get('category', 'Product')} offer with {discount}% off and a source-matched product title and image."
    return deal


def build_standard_deal(**kwargs):
    deal = {
        'product_name': kwargs.get('product_name', '').strip(),
        'source_name': kwargs.get('source_name', 'Unknown'),
        'source_product_id': str(kwargs.get('source_product_id', '')),
        'source_url': kwargs.get('source_url', ''),
        'merchant_name': kwargs.get('merchant_name', kwargs.get('store_name', 'Partner Store')),
        'store_name': kwargs.get('store_name', kwargs.get('merchant_name', 'Partner Store')),
        'category': kwargs.get('category', 'Featured'),
        'original_price': round(float(kwargs.get('original_price', 0) or 0), 2),
        'sale_price': round(float(kwargs.get('sale_price', 0) or 0), 2),
        'discount_percentage': int(kwargs.get('discount_percentage', 0) or 0),
        'affiliate_url': kwargs.get('affiliate_url', ''),
        'image_url': kwargs.get('image_url', ''),
        'source_rating': round(float(kwargs.get('source_rating', 0) or 0), 2),
        'review_count': int(kwargs.get('review_count', 0) or 0),
        'currency_code': kwargs.get('currency_code', 'INR'),
        'expiry_time': kwargs.get('expiry_time', datetime.now() + timedelta(hours=24)),
        'live_views': int(kwargs.get('live_views', random.randint(18, 240))),
    }
    return build_merchandising_fields(deal)


def serialize_deal_for_cache(deal):
    cached = dict(deal)
    expiry_value = cached.get('expiry_time')
    created_value = cached.get('created_at')
    if isinstance(expiry_value, datetime):
        cached['expiry_time'] = expiry_value.isoformat()
    if isinstance(created_value, datetime):
        cached['created_at'] = created_value.isoformat()
    return cached


def deserialize_cached_deal(deal):
    hydrated = dict(deal)
    expiry_value = hydrated.get('expiry_time')
    created_value = hydrated.get('created_at')
    if isinstance(expiry_value, str):
        try:
            hydrated['expiry_time'] = datetime.fromisoformat(expiry_value)
        except ValueError:
            hydrated['expiry_time'] = datetime.now() + timedelta(hours=24)
    if isinstance(created_value, str):
        try:
            hydrated['created_at'] = datetime.fromisoformat(created_value)
        except ValueError:
            hydrated['created_at'] = datetime.now()
    return hydrated


def save_offline_cache(deals, metadata=None):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            'saved_at': datetime.now().isoformat(),
            'metadata': metadata or {},
            'deals': [serialize_deal_for_cache(deal) for deal in deals],
        }
        OFFLINE_CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding='utf-8')
        return True
    except Exception as exc:
        print(f"Offline cache save failed: {exc}")
        return False


def load_offline_cache_payload():
    try:
        if not OFFLINE_CACHE_FILE.exists():
            return None
        payload = json.loads(OFFLINE_CACHE_FILE.read_text(encoding='utf-8'))
        payload['deals'] = [deserialize_cached_deal(deal) for deal in payload.get('deals', [])]
        return payload
    except Exception as exc:
        print(f"Offline cache load failed: {exc}")
        return None


def load_offline_deals():
    payload = load_offline_cache_payload()
    return payload.get('deals', []) if payload else []


def build_demo_sync_deals():
    demo_deals = []
    for deal in DEMO_DEALS:
        demo_deals.append(build_standard_deal(
            product_name=deal['product_name'],
            source_name='OfflineDemo',
            source_product_id=deal['id'],
            source_url=deal['affiliate_url'],
            merchant_name=deal['store_name'],
            store_name=deal['store_name'],
            category=deal['category'],
            original_price=deal['original_price'],
            sale_price=deal['sale_price'],
            discount_percentage=deal['discount_percentage'],
            affiliate_url=deal['affiliate_url'],
            image_url=deal['image_url'],
            source_rating=4.3,
            review_count=120,
            expiry_time=datetime.now() + timedelta(hours=24),
        ))
    return demo_deals


def generate_deal_suggestions(deals):
    if not deals:
        return [
            {'title': 'Run a sync', 'detail': 'No live deals were found. Run a source sync or load offline cache to repopulate the storefront.'},
            {'title': 'Publish a manual featured deal', 'detail': 'Add at least one hero-worthy deal so the homepage has a strong lead card.'},
        ]

    suggestions = []
    sorted_by_discount = sorted(deals, key=lambda item: item.get('discount_percentage', 0), reverse=True)
    sorted_by_clicks = sorted(deals, key=lambda item: item.get('click_count', item.get('live_views', 0)), reverse=True)
    expiring = sorted(
        [deal for deal in deals if deal.get('expiry_time')],
        key=lambda item: item.get('expiry_time')
    )

    top_category_counts = {}
    for deal in deals:
        top_category_counts[deal['category']] = top_category_counts.get(deal['category'], 0) + 1

    thinnest_category = min(top_category_counts, key=top_category_counts.get) if top_category_counts else None

    if sorted_by_discount:
        hero = sorted_by_discount[0]
        suggestions.append({
            'title': f"Feature {hero['product_name'][:32]}",
            'detail': f"It currently has the strongest discount at {hero['discount_percentage']}% off and can anchor the homepage hero slot.",
        })
    if sorted_by_clicks:
        performer = sorted_by_clicks[0]
        suggestions.append({
            'title': f"Promote {performer['category']} harder",
            'detail': f"{performer['product_name'][:34]} is drawing the most attention. Add more related cards or a homepage section for that category.",
        })
    if expiring:
        deadline = expiring[0]
        suggestions.append({
            'title': 'Refresh expiring inventory',
            'detail': f"{deadline['product_name'][:34]} is among the next deals to expire. Replace it early so the homepage stays full.",
        })
    if thinnest_category:
        suggestions.append({
            'title': f"Add more {thinnest_category} deals",
            'detail': f"{thinnest_category} currently has the lightest inventory footprint, so adding depth there would balance the storefront.",
        })
    return suggestions[:4]


def upsert_deal(cursor, deal, batch_id):
    cursor.execute("""
        SELECT id FROM deals
        WHERE COALESCE(source_name, '') = %s AND COALESCE(source_product_id, '') = %s
        LIMIT 1
    """, (deal['source_name'], deal['source_product_id']))
    existing = cursor.fetchone()

    fields = (
        deal['product_name'], deal['source_name'], deal['source_product_id'], deal['source_url'],
        deal['merchant_name'], deal['store_name'], deal['category'], deal['original_price'],
        deal['sale_price'], deal['discount_percentage'], deal.get('currency_code', 'INR'),
        deal['affiliate_url'], deal['image_url'], deal.get('source_rating', 0), deal.get('review_count', 0),
        deal.get('live_views', 0), deal.get('trust_score', 70), deal.get('merchandising_badge'),
        deal.get('ai_summary'), batch_id, deal.get('is_trending', 0), deal.get('is_mega', 0),
        deal.get('is_featured', 0), deal['expiry_time']
    )

    if existing:
        cursor.execute("""
            UPDATE deals
            SET product_name=%s, source_name=%s, source_product_id=%s, source_url=%s,
                merchant_name=%s, store_name=%s, category=%s, original_price=%s, sale_price=%s,
                discount_percentage=%s, currency_code=%s, affiliate_url=%s, image_url=%s,
                source_rating=%s, review_count=%s, live_views=%s, trust_score=%s,
                merchandising_badge=%s, ai_summary=%s, batch_id=%s, is_trending=%s,
                is_mega=%s, is_featured=%s, expiry_time=%s, is_active=TRUE
            WHERE id=%s
        """, fields + (existing['id'],))
        return existing['id'], False

    cursor.execute("""
        INSERT INTO deals (
            product_name, source_name, source_product_id, source_url, merchant_name, store_name,
            category, original_price, sale_price, discount_percentage, currency_code, affiliate_url,
            image_url, source_rating, review_count, live_views, trust_score, merchandising_badge,
            ai_summary, batch_id, is_trending, is_mega, is_featured, expiry_time, is_active
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
    """, fields)
    return cursor.lastrowid, True


def fetch_dummyjson_deals():
    try:
        response = requests.get("https://dummyjson.com/products?limit=0", headers=HTTP_HEADERS, timeout=15)
        response.raise_for_status()
        items = response.json().get('products', [])
    except Exception as exc:
        print(f"DummyJSON sync failed: {exc}")
        return []

    category_map = {
        'beauty': 'Skincare', 'fragrances': 'Fragrances', 'furniture': 'Furniture',
        'groceries': 'Groceries', 'home-decoration': 'Home Decoration', 'kitchen-accessories': 'Home Decoration',
        'laptops': 'Laptops', 'smartphones': 'Smartphones', 'skin-care': 'Skincare',
        'mens-shirts': 'Fashion', 'mens-shoes': 'Fashion', 'womens-dresses': 'Fashion',
        'womens-jewellery': 'Fashion', 'womens-shoes': 'Fashion', 'womens-bags': 'Fashion',
        'sports-accessories': 'Gaming', 'tablets': 'Smartphones'
    }

    deals = []
    for item in items:
        raw_category = item.get('category', '').lower()
        if raw_category not in category_map:
            continue
        price_inr = convert_usd_to_inr(item.get('price', 0))
        discount_pct = float(item.get('discountPercentage', 0) or 0)
        sale_price = round(price_inr - (price_inr * (discount_pct / 100.0)), 2)
        deals.append(build_standard_deal(
            product_name=item.get('title'),
            source_name='DummyJSON',
            source_product_id=item.get('id'),
            source_url=f"https://dummyjson.com/products/{item.get('id')}",
            merchant_name='Amazon India',
            store_name='Amazon India',
            category=category_map[raw_category],
            original_price=max(price_inr, sale_price + 1),
            sale_price=max(sale_price, 1),
            discount_percentage=int(round(discount_pct)),
            affiliate_url=build_affiliate_url(item.get('title', ''), '', 'Amazon India'),
            image_url=item.get('thumbnail') or (item.get('images') or [""])[0],
            source_rating=item.get('rating', 0),
            review_count=int(item.get('stock', 0) or 0) * 3,
            expiry_time=datetime.now() + timedelta(hours=random.randint(10, 48)),
        ))
    return deals


def fetch_fakestore_deals():
    try:
        response = requests.get("https://fakestoreapi.com/products", headers=HTTP_HEADERS, timeout=15)
        response.raise_for_status()
        items = response.json()
    except Exception as exc:
        print(f"Fake Store sync failed: {exc}")
        return []

    category_map = {
        "electronics": "Laptops",
        "jewelery": "Fashion",
        "men's clothing": "Fashion",
        "women's clothing": "Fashion",
    }
    deals = []
    for item in items:
        raw_category = item.get('category', '')
        if raw_category not in category_map:
            continue
        sale_price = convert_usd_to_inr(item.get('price', 0))
        original_price = round(sale_price * random.uniform(1.18, 1.42), 2)
        deals.append(build_standard_deal(
            product_name=item.get('title'),
            source_name='FakeStoreAPI',
            source_product_id=item.get('id'),
            source_url=f"https://fakestoreapi.com/products/{item.get('id')}",
            merchant_name='Amazon India',
            store_name='Amazon India',
            category=category_map[raw_category],
            original_price=original_price,
            sale_price=sale_price,
            discount_percentage=int(round(((original_price - sale_price) / original_price) * 100)),
            affiliate_url=build_affiliate_url(item.get('title', ''), '', 'Amazon India'),
            image_url=item.get('image', ''),
            source_rating=(item.get('rating') or {}).get('rate', 0),
            review_count=(item.get('rating') or {}).get('count', 0),
            expiry_time=datetime.now() + timedelta(hours=random.randint(12, 72)),
        ))
    return deals


def fetch_escuelajs_deals():
    try:
        response = requests.get("https://api.escuelajs.co/api/v1/products?offset=0&limit=40", headers=HTTP_HEADERS, timeout=15)
        response.raise_for_status()
        items = response.json()
    except Exception as exc:
        print(f"EscuelaJS sync failed: {exc}")
        return []

    category_map = {
        'clothes': 'Fashion',
        'electronics': 'Laptops',
        'furniture': 'Furniture',
        'shoes': 'Fashion',
        'miscellaneous': 'Home Decoration',
    }
    deals = []
    for item in items:
        raw_category = ((item.get('category') or {}).get('name') or '').lower()
        if raw_category not in category_map:
            continue
        sale_price = convert_usd_to_inr(item.get('price', 0))
        original_price = round(sale_price * random.uniform(1.14, 1.38), 2)
        deals.append(build_standard_deal(
            product_name=item.get('title'),
            source_name='EscuelaJS',
            source_product_id=item.get('id'),
            source_url=f"https://api.escuelajs.co/api/v1/products/{item.get('id')}",
            merchant_name='Curated Partner Store',
            store_name='Curated Partner Store',
            category=category_map[raw_category],
            original_price=original_price,
            sale_price=sale_price,
            discount_percentage=int(round(((original_price - sale_price) / original_price) * 100)),
            affiliate_url=build_affiliate_url(item.get('title', ''), '', 'Amazon India'),
            image_url=((item.get('images') or [''])[0]),
            source_rating=4.1 + random.random() * 0.8,
            review_count=random.randint(25, 240),
            expiry_time=datetime.now() + timedelta(hours=random.randint(12, 60)),
        ))
    return deals


def fetch_cheapshark_deals():
    try:
        response = requests.get("https://www.cheapshark.com/api/1.0/deals?storeID=1&pageSize=25&upperPrice=40", headers=HTTP_HEADERS, timeout=15)
        response.raise_for_status()
        items = response.json()
    except Exception as exc:
        print(f"CheapShark sync failed: {exc}")
        return []

    deals = []
    for item in items:
        sale_price = convert_usd_to_inr(item.get('salePrice', 0))
        original_price = convert_usd_to_inr(item.get('normalPrice', 0)) or round(sale_price * 1.25, 2)
        rating_percent = float(item.get('steamRatingPercent', 0) or 0)
        deals.append(build_standard_deal(
            product_name=item.get('title'),
            source_name='CheapShark',
            source_product_id=item.get('dealID'),
            source_url=f"https://www.cheapshark.com/redirect?dealID={item.get('dealID')}",
            merchant_name='Steam / PC Store',
            store_name='Gaming Partner',
            category='Gaming',
            original_price=original_price,
            sale_price=sale_price,
            discount_percentage=int(float(item.get('savings', 0) or 0)),
            affiliate_url=build_affiliate_url(item.get('title', ''), f"https://www.cheapshark.com/redirect?dealID={item.get('dealID')}", 'Gaming Partner'),
            image_url=item.get('thumb', ''),
            source_rating=(rating_percent / 20) if rating_percent else 0,
            review_count=int(item.get('steamRatingCount', 0) or 0),
            expiry_time=datetime.now() + timedelta(hours=random.randint(8, 30)),
        ))
    return deals


def smtp_is_configured():
    return all([os.getenv("SMTP_HOST"), os.getenv("SMTP_PORT"), os.getenv("SMTP_FROM")])


def compose_welcome_email(email, unsubscribe_token, category=None):
    unsubscribe_url = build_unsubscribe_url(unsubscribe_token)
    html_body = render_template(
        'emails/welcome.html',
        email=email,
        category=category,
        home_url=get_base_url(),
        unsubscribe_url=unsubscribe_url,
        brand='DropDeals',
    )
    text_body = f"Welcome to DropDeals alerts. You will now receive new deal updates{f' for {category}' if category else ''}. Unsubscribe: {unsubscribe_url}"
    return "Welcome to DropDeals alerts", html_body, text_body


def compose_deal_alert_email(row):
    unsubscribe_url = build_unsubscribe_url(row['unsubscribe_token'])
    html_body = render_template(
        'emails/deal_alert.html',
        row=row,
        unsubscribe_url=unsubscribe_url,
        brand='DropDeals',
    )
    text_body = (
        f"DropDeals alert: {row['product_name']} is now available at {currency_filter(row['sale_price'])}. "
        f"Original price: {currency_filter(row['original_price'])}. Open the deal: {row['affiliate_url']} "
        f"Unsubscribe: {unsubscribe_url}"
    )
    return f"DropDeals alert: {row['product_name']}", html_body, text_body


def send_email_message(to_email, subject, html_body, text_body):
    if not smtp_is_configured():
        return False, "SMTP not configured"
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.getenv("SMTP_FROM")
        msg["To"] = to_email
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")

        with smtplib.SMTP(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", "587")), timeout=20) as server:
            server.starttls()
            if os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"):
                server.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
            server.send_message(msg)
        return True, "sent"
    except Exception as exc:
        print(f"Email send failed: {exc}")
        return False, str(exc)


def log_automation_run(run_type, status, summary, deals_processed, emails_sent):
    db = get_db_connection()
    if not db:
        return
    try:
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO automation_runs (run_type, status, summary, deals_processed, emails_sent)
            VALUES (%s, %s, %s, %s, %s)
        """, (run_type, status, summary, deals_processed, emails_sent))
        db.commit()
    finally:
        db.close()


def process_subscription_alerts():
    if not smtp_is_configured():
        return 0

    db = get_db_connection()
    if not db:
        return 0

    emails_sent = 0
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT s.*, d.id AS matched_deal_id, d.product_name, d.sale_price, d.original_price,
                   d.category AS deal_category, d.affiliate_url
            FROM subscriptions s
            JOIN deals d
              ON ((s.deal_id IS NOT NULL AND s.deal_id = d.id)
                  OR (s.deal_id IS NULL AND s.category IS NOT NULL AND s.category = d.category))
            WHERE COALESCE(s.is_active, TRUE) = TRUE
              AND d.is_active = TRUE
              AND d.expiry_time > NOW()
              AND (s.target_price IS NULL OR d.sale_price <= s.target_price)
              AND (s.last_notified_at IS NULL OR s.last_notified_at < DATE_SUB(NOW(), INTERVAL 12 HOUR))
        """)
        matches = cursor.fetchall()

        for row in matches:
            if not row.get('unsubscribe_token'):
                row['unsubscribe_token'] = f"sub_{row['id']}_{slugify(row['email'])}"
                cursor.execute("UPDATE subscriptions SET unsubscribe_token = %s WHERE id = %s", (row['unsubscribe_token'], row['id']))

            cursor.execute("""
                SELECT id FROM email_events
                WHERE subscription_id = %s AND deal_id = %s AND event_type = 'deal_alert'
                  AND sent_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
                LIMIT 1
            """, (row['id'], row['matched_deal_id']))
            if cursor.fetchone():
                continue

            subject, html_body, text_body = compose_deal_alert_email(row)
            sent, status = send_email_message(row['email'], subject, html_body, text_body)
            cursor.execute("""
                INSERT INTO email_events (subscription_id, deal_id, event_type, status)
                VALUES (%s, %s, 'deal_alert', %s)
            """, (row['id'], row['matched_deal_id'], status))
            if sent:
                cursor.execute("UPDATE subscriptions SET last_notified_at = NOW() WHERE id = %s", (row['id'],))
                emails_sent += 1

        db.commit()
        return emails_sent
    finally:
        db.close()


def run_automation_cycle(run_type="agent", prefer_offline=False):
    success, created_count, batch_id, source_counts, error_message = perform_sync_logic(prefer_offline=prefer_offline)
    emails_sent = process_subscription_alerts() if success else 0
    summary = f"Batch {batch_id or 'n/a'} | sources={source_counts} | emails={emails_sent}"
    if error_message:
        summary = f"{summary} | error={error_message}"
    log_automation_run(run_type, "success" if success else "failed", summary, created_count, emails_sent)
    return {
        'success': success,
        'created_count': created_count,
        'batch_id': batch_id,
        'source_counts': source_counts,
        'emails_sent': emails_sent,
        'error_message': error_message,
    }

# ─────────────────────────────────────────────────────────
#  SYNC ENGINE (FIXED MATH LOGIC)
# ─────────────────────────────────────────────────────────

def perform_sync_logic(prefer_offline=False):
    db = get_db_connection()
    if not db:
        return False, 0, None, {}, "database connection failed"
    try:
        cursor = db.cursor(dictionary=True)
        now = datetime.now()

        cursor.execute("DELETE FROM clicks WHERE deal_id IN (SELECT id FROM deals WHERE expiry_time <= %s)", (now,))
        cursor.execute("DELETE FROM deals WHERE expiry_time <= %s", (now,))

        sync_batch_id = f"sync_{int(time.time())}"
        all_deals = []
        source_counts = {
            'amazon_paapi': 0,
            'partner_json_feed': 0,
            'offline_cache': 0,
            'offline_demo': 0,
        }
        warnings = []

        if not prefer_offline:
            partner_deals, partner_counts, partner_warnings = fetch_partner_deals(
                build_standard_deal=build_standard_deal,
                build_affiliate_url=build_affiliate_url,
                session_headers=HTTP_HEADERS,
            )
            all_deals.extend(partner_deals)
            source_counts.update(partner_counts)
            warnings.extend(partner_warnings)

        if not all_deals and os.getenv("ENABLE_SANDBOX_SOURCES", "").lower() in {"1", "true", "yes"}:
            sandbox_fetchers = {
                'dummyjson': fetch_dummyjson_deals,
                'fakestore': fetch_fakestore_deals,
                'escuelajs': fetch_escuelajs_deals,
                'cheapshark': fetch_cheapshark_deals,
            }
            for source_name, fetcher in sandbox_fetchers.items():
                fetched = fetcher()
                source_counts[source_name] = len(fetched)
                all_deals.extend(fetched)

        if not all_deals:
            cached_deals = load_offline_deals()
            if cached_deals:
                source_counts['offline_cache'] = len(cached_deals)
                all_deals = cached_deals
                sync_batch_id = f"offline_{int(time.time())}"

        if not all_deals:
            if prefer_offline:
                warnings.append("production sources unavailable; using offline demo inventory")
            else:
                warnings.append("production sources unavailable; falling back to offline demo inventory")
            demo_deals = build_demo_sync_deals()
            source_counts['offline_demo'] = len(demo_deals)
            all_deals = [deserialize_cached_deal(serialize_deal_for_cache(deal)) for deal in demo_deals]
            sync_batch_id = f"offline_demo_{int(time.time())}"

        if not all_deals:
            message = "; ".join(warnings) if warnings else "no production partner sources returned deals and no offline cache was available"
            return False, 0, None, source_counts, message

        new_deals_count = 0
        for deal in all_deals:
            _, created = upsert_deal(cursor, deal, sync_batch_id)
            if created:
                new_deals_count += 1

        db.commit()
        save_offline_cache(all_deals, {'batch_id': sync_batch_id, 'source_counts': source_counts, 'prefer_offline': prefer_offline})
        return True, new_deals_count, sync_batch_id, source_counts, "; ".join(warnings) if warnings else None
    except Exception as e:
        print(f"Sync Error: {e}")
        return False, 0, None, {}, str(e)
    finally:
        db.close()

# ─────────────────────────────────────────────────────────
#  PUBLIC ROUTES
# ─────────────────────────────────────────────────────────

@app.route('/')
def index():
    search_query = request.args.get('search', '').strip()
    cat_filter = request.args.get('category', '').strip()
    budget = request.args.get('budget', '').strip()
    budget_value = None
    if budget:
        try:
            budget_value = float(budget)
        except ValueError:
            budget_value = None
    effective_budget_value = budget_value if budget_value is not None else MAX_DEFAULT_SALE_PRICE
    db = get_db_connection()
    now = datetime.now()
    deals = []
    data_source = 'demo'

    if db:
        cursor = db.cursor(dictionary=True)
        query = """
            SELECT d.*,
                   COALESCE((SELECT COUNT(*) FROM clicks c WHERE c.deal_id = d.id), 0) AS click_count
            FROM deals d
            WHERE d.is_active=TRUE AND d.expiry_time > %s
        """
        params = [now]

        if search_query:
            query += " AND (product_name LIKE %s OR category LIKE %s)"
            params.extend([f"%{search_query}%", f"%{search_query}%"])
        if cat_filter:
            query += " AND category = %s"
            params.append(cat_filter)
        if effective_budget_value is not None:
            query += " AND sale_price <= %s"
            params.append(effective_budget_value)

        query += " ORDER BY CASE WHEN sale_price <= %s THEN 0 ELSE 1 END, COALESCE(is_featured, FALSE) DESC, COALESCE(trust_score, 0) DESC, created_at DESC"
        params.append(PRIMARY_PRICE_TARGET)
        cursor.execute(query, tuple(params))
        deals = [normalize_deal(row) for row in cursor.fetchall()]
        db.close()
        data_source = 'database'

    if not deals:
        deals = filter_demo_deals(search_query, cat_filter)
        if effective_budget_value is not None:
            deals = [deal for deal in deals if deal['sale_price'] <= effective_budget_value]
        data_source = 'demo'
        if cat_filter and cat_filter not in {deal['category'] for deal in deals}:
            deals = []

    page_context = build_homepage_context(deals)
    categories = page_context['categories'] or sorted({deal['category'] for deal in DEMO_DEALS})

    return render_template(
        'index.html',
        deals=deals,
        search_query=search_query,
        current_category=cat_filter,
        budget=budget,
        now=now,
        data_source=data_source,
        smtp_ready=smtp_is_configured(),
        **page_context,
    )

@app.route('/info/<page_type>')
def info_page(page_type):
    if page_type not in INFO_PAGES: return redirect(url_for('index'))
    return render_template('info.html', info=INFO_PAGES[page_type], page_type=page_type)

@app.route('/go/<int:deal_id>')
def cloaked_redirect(deal_id):
    db = get_db_connection()
    if not db: return redirect(url_for('index'))
    cursor = db.cursor(dictionary=True)
    cursor.execute("UPDATE deals SET base_views = base_views + 1 WHERE id = %s", (deal_id,))
    cursor.execute("INSERT INTO clicks (deal_id, clicked_at, referrer) VALUES (%s, NOW(), %s)", (deal_id, request.referrer))
    db.commit()
    cursor.execute("SELECT affiliate_url FROM deals WHERE id = %s", (deal_id,))
    deal = cursor.fetchone()
    db.close()
    return redirect(deal['affiliate_url']) if deal else redirect(url_for('index'))

@app.route('/wishlist')
def wishlist_page():
    suggestions = build_homepage_context(filter_demo_deals()).get('popular_deals', [])

    db = get_db_connection()
    if db:
        try:
            cursor = db.cursor(dictionary=True)
            cursor.execute("""
                SELECT d.*, COALESCE((SELECT COUNT(*) FROM clicks c WHERE c.deal_id = d.id), 0) AS click_count
                FROM deals d
                WHERE d.is_active = TRUE AND d.expiry_time > NOW()
                ORDER BY click_count DESC, d.discount_percentage DESC, d.created_at DESC
                LIMIT 6
            """)
            live_suggestions = [normalize_deal(row) for row in cursor.fetchall()]
            if live_suggestions:
                suggestions = live_suggestions
        finally:
            db.close()

    return render_template('wishlist.html', suggestions=suggestions[:6])


@app.route('/api/wishlist/deals', methods=['POST'])
def wishlist_deals_api():
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    deals = fetch_deals_by_ids(ids)
    return jsonify({'items': [serialize_deal_card(deal) for deal in deals]})


def fetch_deal_by_id(deal_id):
    db = get_db_connection()
    if not db:
        return None
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM deals WHERE id = %s LIMIT 1", (deal_id,))
        deal = cursor.fetchone()
        return normalize_deal(deal) if deal else None
    finally:
        db.close()


def fetch_deals_by_ids(deal_ids):
    ids = [int(deal_id) for deal_id in deal_ids if str(deal_id).isdigit()]
    if not ids:
        return []

    db = get_db_connection()
    if db:
        try:
            cursor = db.cursor(dictionary=True)
            placeholders = ",".join(["%s"] * len(ids))
            cursor.execute(
                f"SELECT d.*, COALESCE((SELECT COUNT(*) FROM clicks c WHERE c.deal_id = d.id), 0) AS click_count FROM deals d WHERE d.id IN ({placeholders})",
                tuple(ids),
            )
            found = {row['id']: normalize_deal(row) for row in cursor.fetchall()}
            if found:
                return [found[deal_id] for deal_id in ids if deal_id in found]
        finally:
            db.close()

    demo_lookup = {deal['id']: deal for deal in filter_demo_deals()}
    return [demo_lookup[deal_id] for deal_id in ids if deal_id in demo_lookup]


def serialize_deal_card(deal):
    return {
        'id': deal.get('id'),
        'name': deal.get('product_name'),
        'category': deal.get('category'),
        'price': deal.get('sale_price'),
        'original_price': deal.get('original_price'),
        'image_url': deal.get('image_url'),
        'discount_percentage': deal.get('discount_percentage'),
        'store_name': deal.get('store_name'),
        'badge_text': deal.get('badge_text'),
        'outbound_url': deal.get('outbound_url'),
        'savings': deal.get('savings'),
    }

# ─────────────────────────────────────────────────────────
#  ADMIN ROUTES
# ─────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'): return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        if (request.form['username'] == os.getenv('ADMIN_USERNAME') and 
            request.form['password'] == os.getenv('ADMIN_PASSWORD')):
            session.permanent = True
            session['logged_in'] = True
            session['is_admin'] = True
            flash("Welcome back, Boss! 🚀", "success")
            return redirect(url_for('admin_dashboard'))
        flash('Invalid Credentials!', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for('index'))

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    db = get_db_connection()
    if not db:
        flash("Database connection failed", "danger")
        return redirect(url_for('index'))
        
    cursor = db.cursor(dictionary=True)
    now = datetime.now()
    
    try:
        # FIXED: Revenue logic now casts raw_sales to float to prevent Decimal * float TypeError
        cursor.execute("""
            SELECT COALESCE(SUM(d.sale_price), 0) as raw_sales, 
            (SELECT COUNT(*) FROM clicks) as total_clicks 
            FROM clicks c JOIN deals d ON c.deal_id=d.id
        """)
        raw_kpis = cursor.fetchone()
        
        # Force float conversion before calculating 5% commission
        revenue = float(raw_kpis['raw_sales'] or 0) * 0.05
        kpis = {
            'total_revenue': revenue,
            'total_clicks': raw_kpis['total_clicks'] or 0
        }

        # Inventory Logic
        cursor.execute("""
            SELECT COUNT(*) as total, 
            SUM(CASE WHEN expiry_time > %s THEN 1 ELSE 0 END) as live,
            SUM(CASE WHEN expiry_time <= %s THEN 1 ELSE 0 END) as expired
            FROM deals
        """, (now, now))
        inventory = cursor.fetchone() or {'total': 0, 'live': 0, 'expired': 0}

        # Chart Data
        cursor.execute("SELECT DATE(clicked_at) as date, COUNT(*) as count FROM clicks GROUP BY DATE(clicked_at) ORDER BY date ASC LIMIT 7")
        chart_data = cursor.fetchall()

        # Recent Activity
        cursor.execute("SELECT d.product_name, c.clicked_at FROM clicks c JOIN deals d ON c.deal_id=d.id ORDER BY c.clicked_at DESC LIMIT 5")
        recent_activity = cursor.fetchall()

        # Main Stats Table
        cursor.execute("""
            SELECT d.*, 
            (SELECT COUNT(*) FROM clicks c WHERE c.deal_id=d.id) as click_count,
            (SELECT COUNT(*) FROM subscriptions s WHERE s.deal_id=d.id OR s.category=d.category) as alert_count 
            FROM deals d ORDER BY d.created_at DESC
        """)
        deal_stats = cursor.fetchall()

        # Final check: Convert all Decimal prices in deal_stats to float for safety
        for s in deal_stats:
            s['original_price'] = float(s['original_price'])
            s['sale_price'] = float(s['sale_price'])

        cursor.execute("SELECT * FROM automation_runs ORDER BY created_at DESC LIMIT 5")
        automation_runs = cursor.fetchall()
        suggestion_cards = generate_deal_suggestions([normalize_deal(row) for row in deal_stats[:12]])
        offline_cache = load_offline_cache_payload() or {}

        return render_template('admin_stats.html', 
                               kpis=kpis, 
                               inventory=inventory, 
                               chart_data=chart_data, 
                               stats=deal_stats, 
                               recent_activity=recent_activity, 
                               automation_runs=automation_runs,
                               suggestion_cards=suggestion_cards,
                               offline_cache=offline_cache,
                               smtp_ready=smtp_is_configured(),
                               now=now)
    except Exception as e:
        print(f"DASHBOARD CRASH: {e}")
        flash(f"Dashboard Error: {str(e)}", "danger")
        return redirect(url_for('index'))
    finally:
        db.close()

@app.route('/admin/add', methods=['GET', 'POST'])
@login_required
def add_deal():
    if request.method == 'POST':
        db = get_db_connection()
        try:
            cursor = db.cursor(dictionary=True)
            exp_str = request.form.get('expiry_time')
            expiry = datetime.strptime(exp_str, '%Y-%m-%dT%H:%M') if exp_str else datetime.now() + timedelta(days=2)
            orig = float(request.form.get('original_price') or 0)
            sale = float(request.form.get('sale_price') or 0)
            disc = int(round((orig-sale)/orig*100.0)) if orig > 0 else 0
            product_name = request.form.get('product_name')
            merchant_name = request.form.get('store', 'Amazon India')
            source_url = request.form.get('source_url') or request.form.get('affiliate_url')
            deal = build_standard_deal(
                product_name=product_name,
                source_name='Manual',
                source_product_id=f"manual-{slugify(product_name)}-{int(time.time())}",
                source_url=source_url,
                merchant_name=merchant_name,
                store_name=merchant_name,
                category=request.form.get('category'),
                original_price=orig,
                sale_price=sale,
                discount_percentage=disc,
                affiliate_url=build_affiliate_url(product_name, source_url, merchant_name),
                image_url=request.form.get('image_url'),
                source_rating=4.5 if request.form.get('is_trending') else 4.0,
                review_count=180 if request.form.get('is_trending') else 40,
                expiry_time=expiry,
            )
            deal['is_trending'] = 1 if request.form.get('is_trending') or deal['is_trending'] else 0
            deal['is_mega'] = 1 if request.form.get('is_mega') or deal['is_mega'] else 0
            deal['is_featured'] = 1 if deal['is_mega'] or deal['is_trending'] else deal['is_featured']
            upsert_deal(cursor, deal, f"manual_{int(time.time())}")
            db.commit()
            flash("Deal published!", "success")
        except Exception as e: flash(f"Error: {e}", "danger")
        finally: db.close()
        return redirect(url_for('admin_dashboard'))
    return render_template('admin.html', now=datetime.now(), form_mode='create', deal=None)


@app.route('/admin/edit/<int:deal_id>', methods=['GET', 'POST'])
@login_required
def edit_deal(deal_id):
    db = get_db_connection()
    if not db:
        flash("Database connection failed", "danger")
        return redirect(url_for('admin_dashboard'))

    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM deals WHERE id = %s LIMIT 1", (deal_id,))
        existing = cursor.fetchone()
        if not existing:
            flash("Deal not found.", "warning")
            return redirect(url_for('admin_dashboard'))

        if request.method == 'POST':
            exp_str = request.form.get('expiry_time')
            expiry = datetime.strptime(exp_str, '%Y-%m-%dT%H:%M') if exp_str else existing.get('expiry_time') or (datetime.now() + timedelta(days=2))
            orig = float(request.form.get('original_price') or 0)
            sale = float(request.form.get('sale_price') or 0)
            disc = int(round((orig - sale) / orig * 100.0)) if orig > 0 else 0
            product_name = request.form.get('product_name')
            merchant_name = request.form.get('store', existing.get('store_name') or 'Amazon India')
            source_url = request.form.get('source_url') or request.form.get('affiliate_url')

            deal = build_standard_deal(
                product_name=product_name,
                source_name=existing.get('source_name') or 'Manual',
                source_product_id=existing.get('source_product_id') or f"manual-{deal_id}",
                source_url=source_url,
                merchant_name=merchant_name,
                store_name=merchant_name,
                category=request.form.get('category'),
                original_price=orig,
                sale_price=sale,
                discount_percentage=disc,
                affiliate_url=build_affiliate_url(product_name, source_url, merchant_name),
                image_url=request.form.get('image_url'),
                source_rating=float(existing.get('source_rating') or 0),
                review_count=int(existing.get('review_count') or 0),
                expiry_time=expiry,
            )
            deal['is_trending'] = 1 if request.form.get('is_trending') else 0
            deal['is_mega'] = 1 if request.form.get('is_mega') else 0
            deal['is_featured'] = 1 if request.form.get('is_featured') else deal.get('is_featured', 0)
            cursor.execute("""
                UPDATE deals
                SET product_name=%s, source_url=%s, merchant_name=%s, store_name=%s, category=%s,
                    original_price=%s, sale_price=%s, discount_percentage=%s, affiliate_url=%s,
                    image_url=%s, trust_score=%s, merchandising_badge=%s, ai_summary=%s,
                    is_trending=%s, is_mega=%s, is_featured=%s, expiry_time=%s, is_active=TRUE
                WHERE id=%s
            """, (
                deal['product_name'], deal['source_url'], deal['merchant_name'], deal['store_name'], deal['category'],
                deal['original_price'], deal['sale_price'], deal['discount_percentage'], deal['affiliate_url'],
                deal['image_url'], deal['trust_score'], deal['merchandising_badge'], deal['ai_summary'],
                deal['is_trending'], deal['is_mega'], deal['is_featured'], deal['expiry_time'], deal_id
            ))
            db.commit()
            flash("Deal updated successfully.", "success")
            return redirect(url_for('admin_dashboard'))

        existing['expiry_value'] = existing['expiry_time'].strftime('%Y-%m-%dT%H:%M') if existing.get('expiry_time') else ''
        return render_template('admin.html', now=datetime.now(), form_mode='edit', deal=existing)
    except Exception as exc:
        flash(f"Edit failed: {exc}", "danger")
        return redirect(url_for('admin_dashboard'))
    finally:
        db.close()

@app.route('/admin/delete/<int:deal_id>')
@login_required
def delete_deal(deal_id):
    db = get_db_connection()
    if not db:
        return redirect(url_for('admin_dashboard'))
    try:
        cursor = db.cursor()
        cursor.execute("DELETE FROM clicks WHERE deal_id=%s", (deal_id,))
        cursor.execute("DELETE FROM subscriptions WHERE deal_id=%s", (deal_id,))
        cursor.execute("DELETE FROM deals WHERE id=%s", (deal_id,))
        db.commit()
        flash("Target Neutralized: Deal removed from inventory.", "success")
    except Exception as e:
        print(f"Delete Error: {e}")
        flash(f"Delete Failed: {str(e)}", "danger")
    finally:
        db.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/sync-deals')
@login_required
def admin_sync():
    result = run_automation_cycle('admin_sync')
    success, count, batch_id = result['success'], result['created_count'], result['batch_id']
    if success:
        session['last_sync_batch'] = batch_id
        flash(f"Automation complete. {count} new deals processed and {result['emails_sent']} emails sent.", "success")
        if result.get('error_message'):
            flash(f"Partner sync note: {result['error_message']}", "warning")
    else:
        flash(f"Sync failed: {result.get('error_message') or 'unknown error'}", "danger")
    return redirect(url_for('admin_dashboard'))

# ─────────────────────────────────────────────────────────
#  UTILITY / API ROUTES
# ─────────────────────────────────────────────────────────

@app.route('/api/chat', methods=['POST'])
def chat_api():
    data = request.get_json() or {}
    msg = data.get('message', '').strip()
    msg_lower = msg.lower()
    session_id = data.get('session_id') or f"guest-{int(time.time())}"
    db = get_db_connection()

    detected_category = next((cat for cat, kws in CATEGORY_KEYWORDS.items() if any(k in msg_lower for k in kws)), None)
    budget_match = re.search(r"(under|below|less than)\s*rs?\s*([\d,]+)", msg_lower) or re.search(r"(under|below|less than)\s*([\d,]+)", msg_lower)
    budget = float(budget_match.group(2).replace(',', '')) if budget_match else None

    found = []
    if db:
        cursor = db.cursor(dictionary=True)
        query = "SELECT * FROM deals WHERE is_active=TRUE AND expiry_time > NOW() AND sale_price <= %s"
        params = [MAX_DEFAULT_SALE_PRICE]
        if detected_category:
            query += " AND category = %s"
            params.append(detected_category)
        if budget:
            query += " AND sale_price <= %s"
            params.append(budget)
        if not detected_category and len(msg.split()) <= 4:
            query += " AND product_name LIKE %s"
            params.append(f"%{msg}%")

        query += " ORDER BY CASE WHEN sale_price <= %s THEN 0 ELSE 1 END, COALESCE(is_featured, FALSE) DESC, COALESCE(trust_score, 0) DESC, discount_percentage DESC LIMIT 4"
        params.append(PRIMARY_PRICE_TARGET)
        cursor.execute(query, tuple(params))
        found = [normalize_deal(row) for row in cursor.fetchall()]
        try:
            cursor.execute("INSERT INTO chat_messages (session_id, role, message, detected_category) VALUES (%s, %s, %s, %s)", (session_id, 'user', msg, detected_category))
            cursor.execute("INSERT INTO chat_messages (session_id, role, message, detected_category) VALUES (%s, %s, %s, %s)", (session_id, 'assistant', 'pending', detected_category))
            db.commit()
        except Exception:
            pass
        db.close()

    if not found:
        found = filter_demo_deals(msg if not detected_category else '', detected_category or '')[:4]
        if budget:
            found = [deal for deal in found if deal['sale_price'] <= budget]

    reply = f"I found {len(found)} {detected_category.lower() if detected_category else 'matching'} deal options" if found else "I could not find a close match right now. Try another category or budget."
    if found and budget:
        reply += f" under {currency_filter(budget)}."
    elif found:
        reply += "."

    db2 = get_db_connection()
    if db2:
        try:
            cursor2 = db2.cursor()
            cursor2.execute("""
                UPDATE chat_messages
                SET message = %s
                WHERE session_id = %s AND role = 'assistant' AND message = 'pending'
                ORDER BY id DESC LIMIT 1
            """, (reply, session_id))
            db2.commit()
        except Exception:
            pass
        finally:
            db2.close()

    return jsonify({'reply': reply, 'deals': found, 'detected_category': detected_category, 'budget': budget})


@app.route('/api/suggestions')
def deal_suggestions_api():
    db = get_db_connection()
    deals = []
    if db:
        try:
            cursor = db.cursor(dictionary=True)
            cursor.execute("SELECT * FROM deals WHERE is_active = TRUE AND expiry_time > NOW() ORDER BY created_at DESC LIMIT 24")
            deals = [normalize_deal(row) for row in cursor.fetchall()]
        finally:
            db.close()
    if not deals:
        deals = filter_demo_deals()[:12]
    return jsonify({'suggestions': generate_deal_suggestions(deals)})


@app.route('/admin/run-offline-agent')
@login_required
def run_offline_agent():
    result = run_automation_cycle('offline_agent', prefer_offline=True)
    if result['success']:
        flash(f"Offline automation finished. {result['created_count']} deals processed using cached or bundled data.", "success")
    else:
        flash(f"Offline automation failed: {result.get('error_message') or 'unknown error'}.", "danger")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/clear-all')
@login_required
def clear_all_deals():
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("DELETE FROM clicks")
    cursor.execute("DELETE FROM email_events")
    cursor.execute("DELETE FROM deals")
    db.commit(); db.close()
    flash("Inventory wiped.", "warning")
    return redirect(url_for('admin_dashboard'))

@app.route('/cron/sync/<token>')
def automated_sync(token):
    if token != os.getenv('CRON_SECRET_TOKEN', 'flashsale-pro-ultra-premium-2026'): return "Unauthorized", 401
    result = run_automation_cycle('cron_sync')
    return jsonify(result), 200 if result['success'] else 500

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html', sync_time="6 hours"), 404

@app.route('/subscribe', methods=['POST'])
def subscribe_alert():
    email = (request.form.get('email') or '').strip()
    deal_id = request.form.get('deal_id')
    target_price = request.form.get('target_price')
    category = (request.form.get('category') or '').strip() or None

    d_id = int(deal_id) if (deal_id and deal_id != "0") else None
    t_price = float(target_price) if (target_price and target_price != "0") else None

    db = get_db_connection()
    if not db:
        flash("System offline. Try again later.", "danger")
        return redirect(url_for('index'))

    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT * FROM subscriptions
            WHERE email = %s AND COALESCE(deal_id, 0) = %s AND COALESCE(category, '') = %s
            LIMIT 1
        """, (email, d_id or 0, category or ''))
        existing = cursor.fetchone()

        if existing:
            token = existing.get('unsubscribe_token') or f"sub_{existing['id']}_{slugify(email)}"
            cursor.execute("""
                UPDATE subscriptions
                SET target_price = %s, is_active = TRUE, unsubscribed_at = NULL, unsubscribe_token = %s
                WHERE id = %s
            """, (t_price, token, existing['id']))
        else:
            cursor.execute("""
                INSERT INTO subscriptions (email, deal_id, target_price, category, is_active, unsubscribe_token)
                VALUES (%s, %s, %s, %s, TRUE, %s)
            """, (email, d_id, t_price, category, 'pending'))
            subscription_id = cursor.lastrowid
            token = f"sub_{subscription_id}_{slugify(email)}"
            cursor.execute("UPDATE subscriptions SET unsubscribe_token = %s WHERE id = %s", (token, subscription_id))
        db.commit()
        subject, html_body, text_body = compose_welcome_email(email, token, category)
        email_sent, email_status = send_email_message(email, subject, html_body, text_body)
        if email_sent:
            flash("You are subscribed to DropDeals alerts.", "success")
        else:
            flash(f"Subscription saved, but email was not sent: {email_status}.", "warning")
    except Exception as e:
        print(f"Subscription Error: {e}")
        flash("Subscription failed. Check your details.", "danger")
    finally:
        db.close()
    return redirect(url_for('index'))


@app.route('/unsubscribe/<token>')
def unsubscribe_alert(token):
    db = get_db_connection()
    if not db:
        flash("System offline. Try again later.", "danger")
        return redirect(url_for('index'))

    try:
        cursor = db.cursor()
        cursor.execute("""
            UPDATE subscriptions
            SET is_active = FALSE, unsubscribed_at = NOW()
            WHERE unsubscribe_token = %s
        """, (token,))
        db.commit()
        flash("You have been unsubscribed from DropDeals alerts.", "info")
    finally:
        db.close()
    return redirect(url_for('index'))

@app.route('/admin/force-cleanup')
@login_required
def force_cleanup():
    db = get_db_connection()
    if not db: return redirect(url_for('admin_dashboard'))
    try:
        cursor = db.cursor()
        cursor.execute("""
            DELETE d1 FROM deals d1
            INNER JOIN deals d2 
            WHERE d1.id < d2.id AND d1.product_name = d2.product_name
        """)
        db.commit()
        flash("Database Optimized: Duplicates Purged.", "success")
    except Exception as e:
        flash(f"Optimization Error: {str(e)}", "danger")
    finally:
        db.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/undo-sync')
@login_required
def undo_sync():
    batch_id = session.get('last_sync_batch')
    if not batch_id:
        flash('No recent sync session found to undo.', 'warning')
        return redirect(url_for('admin_dashboard'))
    
    db = get_db_connection()
    try:
        cursor = db.cursor()
        cursor.execute("DELETE FROM clicks WHERE deal_id IN (SELECT id FROM deals WHERE batch_id=%s)", (batch_id,))
        cursor.execute("DELETE FROM deals WHERE batch_id=%s", (batch_id,))
        db.commit()
        session.pop('last_sync_batch', None)
        flash('Rollback Successful: Last sync data wiped.', 'success')
    except Exception as e:
        flash(f"Undo Error: {str(e)}", "danger")
    finally:
        db.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/cleanup-expired')
@login_required
def cleanup_expired():
    db = get_db_connection()
    try:
        cursor = db.cursor()
        now = datetime.now()
        cursor.execute("DELETE FROM clicks WHERE deal_id IN (SELECT id FROM deals WHERE expiry_time <= %s)", (now,))
        cursor.execute("DELETE FROM deals WHERE expiry_time <= %s", (now,))
        db.commit()
        flash("System Purge: Expired deals removed.", "info")
    except Exception as e:
        flash(f"Cleanup Error: {str(e)}", "danger")
    finally:
        db.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/run-agent')
@login_required
def run_agent():
    result = run_automation_cycle('manual_agent')
    if result['success']:
        flash(f"Automation agent finished. {result['created_count']} deals processed and {result['emails_sent']} emails sent.", "success")
        if result.get('error_message'):
            flash(f"Sync completed with fallback mode: {result['error_message']}", "warning")
    else:
        flash(f"Automation agent failed: {result.get('error_message') or 'unknown error'}. Check database and source connectivity.", "danger")
    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

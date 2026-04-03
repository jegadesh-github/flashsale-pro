import os
import random 
import requests 
import time
import json
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import mysql.connector
from dotenv import load_dotenv
from functools import wraps

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'flashsale-pro-ultra-premium-2026') 
app.permanent_session_lifetime = timedelta(hours=8)

# --- DATABASE CONNECTION ---
def get_db_connection():
    # FIXED: Added try block back to resolve IndentationError
    try:
        return mysql.connector.connect(
            host=os.getenv("DB_HOST"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            port=int(os.getenv("DB_PORT", 3306)),
            database=os.getenv("DB_NAME"),
            connection_timeout=30
        )
    except mysql.connector.Error as err:
        print(f"Database Connection Error: {err}")
        return None

def currency_filter(value):
    try:
        if value is None: return "0.00"
        return "{:,.2f}".format(float(value))
    except (ValueError, TypeError):
        return value

# Manually add it to the Jinja environment
app.jinja_env.filters['currency'] = currency_filter

# --- AUTH DECORATOR ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash("Unauthorized access. Please login.", "danger")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- MASTER CONTENT DICTIONARY ---
INFO_PAGES = {
    'about': {
        'title': 'About Elite Drops',
        'content': 'Elite Drops is the premier destination for high-performance deals. We curate daily flash sales across tech, lifestyle, and luxury categories.'
    },
    'privacy': {
        'title': 'Privacy Protocol',
        'content': 'Your data security is our priority. We utilize industry-standard encryption for all session data.'
    },
    'terms': {
        'title': 'Terms of Engagement',
        'content': 'By using Elite Drops, you agree to our automated deal-tracking protocols.'
    },
    'shipping': {
        'title': 'Logistics & Delivery',
        'content': 'Elite Drops acts as a discovery engine. Shipping is managed by the partner brand.'
    }
}

# --- SYNC LOGIC ---
def perform_sync_logic():
    db = None
    try:
        db = get_db_connection()
        if not db: return False, 0, None
        cursor = db.cursor(dictionary=True)
        now = datetime.now()

        # STEP 1: INCOME SETUP (Using your Real ID)
        # Defaults to your ID if the environment variable isn't found
        AFFILIATE_TAG = os.getenv('AFFILIATE_ID', 'elitedrops26-21')

        # STEP 1.1: PURGE EXPIRED
        cursor.execute("DELETE FROM clicks WHERE deal_id IN (SELECT id FROM deals WHERE expiry_time <= %s)", (now,))
        cursor.execute("DELETE FROM deals WHERE expiry_time <= %s", (now,))
        
        sync_batch_id = f"sync_{int(time.time())}"
        
        # We still use DummyJSON for product DATA, but we turn them into AMAZON links for MONEY
        url = "https://dummyjson.com/products?limit=50"
        response = requests.get(url, timeout=15)
        
        if response.status_code != 200:
            return False, 0, None

        data = response.json()
        all_items = data.get('products', [])
        new_deals_count = 0
        target_categories = ['smartphones', 'laptops', 'fragrances', 'skincare', 'groceries', 'home-decoration', 'furniture']

        for item in all_items:
            category_raw = item.get('category', '').lower()
            if category_raw in target_categories:
                name = item['title']
                sale_price = float(item['price'])
                discount_pct = float(item.get('discountPercentage', 10))
                # Calculate original price for a "Sale" look
                original_price = round(sale_price / (1 - (discount_pct / 100)), 2)
                expiry = now + timedelta(hours=48)
                category = category_raw.replace('-', ' ').title() 
                
                # --- NEW INCOME LOGIC: TRANSFORM TO AMAZON LINKS ---
                # We simulate an Amazon URL using the product name/id
                # In the future, this is where your Amazon API data will go
                product_query = name.replace(" ", "+")
                affiliate_url = f"https://www.amazon.in/s?k={product_query}&tag={AFFILIATE_TAG}"
                
                image_url = item['images'][0] if item.get('images') else ""

                sql = """
                    INSERT INTO deals 
                    (product_name, original_price, sale_price, discount_percentage, 
                     category, affiliate_url, image_url, expiry_time, is_active, store_name, batch_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, 'Amazon India', %s)
                    ON DUPLICATE KEY UPDATE 
                        sale_price = VALUES(sale_price),
                        discount_percentage = VALUES(discount_percentage),
                        affiliate_url = VALUES(affiliate_url),
                        expiry_time = VALUES(expiry_time),
                        batch_id = VALUES(batch_id),
                        is_active = TRUE
                """
                cursor.execute(sql, (name, original_price, sale_price, int(discount_pct), 
                                     category, affiliate_url, image_url, expiry, sync_batch_id))
                new_deals_count += 1

        db.commit()
        return True, new_deals_count, sync_batch_id
    except Exception as e:
        print(f"Sync Logic Error: {e}")
        return False, 0, None
    finally:
        if db: db.close()

# --- MAIN PUBLIC ROUTES ---

@app.route('/')
def index():
    search_query = request.args.get('search', '').strip()
    category_filter = request.args.get('category', '').strip()
    db = None
    
    hero_deals = []; trending_deals = []; flash_deals = []; daily_drops = []; categories = []
    current_time = datetime.now()
    
    try:
        db = get_db_connection()
        if not db: return "Database connection failed", 500
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("SELECT DISTINCT category FROM deals WHERE is_active = TRUE")
        categories = [row['category'] for row in cursor.fetchall()]

        query = """
            SELECT d.*, 
            (SELECT COUNT(*) FROM clicks c WHERE c.deal_id = d.id AND c.clicked_at > NOW() - INTERVAL 1 DAY) as recent_clicks,
            (SELECT COUNT(*) FROM clicks c WHERE c.deal_id = d.id AND c.clicked_at > NOW() - INTERVAL 3 HOUR) as hourly_velocity
            FROM deals d 
            WHERE d.is_active = TRUE AND d.expiry_time > %s
        """
        params = [current_time]
        
        if search_query:
            query += " AND (d.product_name LIKE %s OR d.category LIKE %s)"
            params.extend([f"%{search_query}%", f"%{search_query}%"])
        if category_filter:
            query += " AND d.category = %s"
            params.append(category_filter)
        
        query += " ORDER BY d.rating DESC, d.discount_percentage DESC"
        cursor.execute(query, tuple(params))
        all_results = cursor.fetchall()

        for deal in all_results:
            deal['is_trending'] = (deal.get('recent_clicks') or 0) > 2
            deal['is_hot'] = (deal.get('hourly_velocity') or 0) >= 5 
            deal['live_views'] = (deal.get('base_views', 15) or 15) + random.randint(0, 5)
            
            time_left = deal['expiry_time'] - current_time
            daily_drops.append(deal)

            if len(hero_deals) < 6 and deal.get('rating', 0) >= 4.0:
                hero_deals.append(deal)
            elif len(trending_deals) < 4 and (deal['is_trending'] or deal['is_hot']):
                trending_deals.append(deal)
            elif len(flash_deals) < 4 and time_left < timedelta(hours=24):
                flash_deals.append(deal)
            
    except Exception as e:
        print(f"HOME ERROR: {str(e)}")
    finally:
        if db: db.close()

    return render_template('index.html', 
                            hero_deals=hero_deals, trending_deals=trending_deals, 
                            flash_deals=flash_deals, deals=daily_drops, 
                            categories=categories, search_query=search_query, 
                            current_category=category_filter, now=current_time)

@app.route('/info/<page_type>')
def info_page(page_type):
    if page_type not in INFO_PAGES:
        return redirect(url_for('index'))
    return render_template('info.html', info=INFO_PAGES[page_type], page_type=page_type)

@app.route('/go/<int:deal_id>')
def cloaked_redirect(deal_id):
    db = None
    try:
        db = get_db_connection()
        if not db: return redirect(url_for('index'))
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("INSERT INTO clicks (deal_id, clicked_at) VALUES (%s, NOW())", (deal_id,))
        cursor.execute("UPDATE deals SET base_views = base_views + 1 WHERE id = %s", (deal_id,))
        db.commit()
        
        cursor.execute("SELECT affiliate_url FROM deals WHERE id = %s", (deal_id,))
        deal = cursor.fetchone()
        if deal:
            return redirect(deal['affiliate_url'])
            
    except Exception as e:
        print(f"Cloak Error: {e}")
    finally:
        if db: db.close()
    return redirect(url_for('index'))

@app.route('/wishlist')
def wishlist_page():
    return render_template('wishlist.html')

@app.route('/api/wishlist', methods=['POST'])
def get_wishlist_items():
    data = request.get_json()
    deal_ids = data.get('ids', [])
    if not deal_ids: return jsonify([])
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        format_strings = ','.join(['%s'] * len(deal_ids))
        cursor.execute(f"SELECT id, product_name, sale_price, image_url FROM deals WHERE id IN ({format_strings})", tuple(deal_ids))
        return jsonify(cursor.fetchall())
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if db: db.close()

@app.route('/subscribe', methods=['POST'])
def subscribe_alert():
    email = request.form.get('email')
    raw_deal_id = request.form.get('deal_id')
    raw_price = request.form.get('target_price')

    deal_id = int(raw_deal_id) if (raw_deal_id and raw_deal_id != "0") else None
    target_price = float(raw_price) if (raw_price and raw_price != "0") else None

    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        sql = "INSERT INTO subscriptions (email, deal_id, target_price) VALUES (%s, %s, %s)"
        cursor.execute(sql, (email, deal_id, target_price))
        db.commit()
        flash("Welcome to the Inner Circle!", "success")
    except Exception as e:
        flash("Subscription failed.", "danger")
    finally:
        if db: db.close()
    return redirect(url_for('index'))

# --- AUTH ROUTES ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'):
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        if request.form['username'] == os.getenv('ADMIN_USERNAME') and request.form['password'] == os.getenv('ADMIN_PASSWORD'):
            session.permanent = True 
            session['logged_in'] = True
            session['user_id'] = 1  
            session['is_admin'] = True 
            flash("Welcome back, Boss!", "success")
            return redirect(url_for('admin_dashboard'))
        flash('Invalid Credentials!', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for('index'))

# --- ADMIN ROUTES ---

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    db = None
    current_time = datetime.now()
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT COALESCE(SUM(d.sale_price * 0.05), 0) as total_revenue, 
            (SELECT COUNT(*) FROM clicks) as total_clicks 
            FROM clicks c
            JOIN deals d ON c.deal_id = d.id
        """)
        kpis = cursor.fetchone() or {'total_revenue': 0, 'total_clicks': 0}
        
        cursor.execute("""
            SELECT COUNT(*) as total,
            SUM(CASE WHEN expiry_time > %s THEN 1 ELSE 0 END) as live,
            SUM(CASE WHEN expiry_time <= %s THEN 1 ELSE 0 END) as expired
            FROM deals
        """, (current_time, current_time))
        inventory = cursor.fetchone()
        
        inventory['recently_cleaned'] = session.get('recently_cleaned', 0)
        
        cursor.execute("SELECT DATE(clicked_at) as date, COUNT(*) as count FROM clicks GROUP BY DATE(clicked_at) ORDER BY date ASC LIMIT 7")
        chart_data = cursor.fetchall()

        cursor.execute("SELECT d.product_name, c.clicked_at FROM clicks c JOIN deals d ON c.deal_id = d.id ORDER BY c.clicked_at DESC LIMIT 5")
        recent_activity = cursor.fetchall()
        
        cursor.execute("""
            SELECT d.*, 
            (SELECT COUNT(*) FROM clicks c WHERE c.deal_id = d.id) as click_count,
            (SELECT COUNT(*) FROM subscriptions s WHERE s.deal_id = d.id) as alert_count
            FROM deals d ORDER BY d.created_at DESC
        """)
        deal_stats = cursor.fetchall()
        return render_template('admin_stats.html', kpis=kpis, inventory=inventory, chart_data=chart_data, stats=deal_stats, recent_activity=recent_activity, now=current_time)
    except Exception as e:
        flash(f"Admin Dashboard Error: {str(e)}", "danger")
        return redirect(url_for('index'))
    finally:
        if db: db.close()

@app.route('/admin/add', methods=['GET', 'POST'])
@login_required
def add_deal():
    if request.method == 'POST':
        db = None
        try:
            db = get_db_connection()
            cursor = db.cursor()
            sql = """INSERT INTO deals (product_name, original_price, sale_price, discount_percentage, 
                     category, affiliate_url, image_url, expiry_time, is_active, store_name) 
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)"""
            
            expiry = datetime.now() + timedelta(days=int(request.form.get('days', 2)))
            cursor.execute(sql, (
                request.form.get('name'),
                request.form.get('original_price'),
                request.form.get('sale_price'),
                request.form.get('discount'),
                request.form.get('category'),
                request.form.get('url'),
                request.form.get('image'),
                expiry,
                request.form.get('store', 'Manual_Entry')
            ))
            db.commit()
            flash("New Elite deal added manually!", "success")
        except Exception as e:
            flash(f"Add Error: {e}", "danger")
        finally:
            if db: db.close()
        return redirect(url_for('admin_dashboard'))
    return render_template('admin.html')

@app.route('/admin/delete/<int:deal_id>')
@login_required
def delete_deal(deal_id):
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("DELETE FROM clicks WHERE deal_id = %s", (deal_id,))
        cursor.execute("DELETE FROM subscriptions WHERE deal_id = %s", (deal_id,))
        cursor.execute("DELETE FROM deals WHERE id = %s", (deal_id,))
        db.commit()
        flash("Elite deal purged from system.", "success")
    except Exception as e:
        flash(f"Delete Error: {e}", "danger")
    finally:
        if db: db.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/sync-deals')
@login_required
def admin_sync():
    success, count, batch_id = perform_sync_logic()
    if success:
        session['last_sync_batch'] = batch_id
        flash(f"Sync Complete! {count} items processed.", "success")
    else:
        flash("Sync failed.", "danger")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/undo-sync')
@login_required
def undo_sync():
    batch_id = session.get('last_sync_batch')
    if not batch_id:
        flash('No recent sync found to undo!', 'warning')
        return redirect(url_for('admin_dashboard'))
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("DELETE FROM clicks WHERE deal_id IN (SELECT id FROM deals WHERE batch_id = %s)", (batch_id,))
        cursor.execute("DELETE FROM deals WHERE batch_id = %s", (batch_id,))
        db.commit()
        session.pop('last_sync_batch', None)
        flash('Last sync undone successfully.', 'success')
    except Exception as e:
        flash(f"Undo Error: {str(e)}", "danger")
    finally:
        if db: db.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/cleanup-expired')
@login_required
def cleanup_expired():
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        now = datetime.now()
        cursor.execute("SELECT COUNT(*) FROM deals WHERE expiry_time <= %s", (now,))
        expired_count = cursor.fetchone()[0]
        cursor.execute("DELETE FROM clicks WHERE deal_id IN (SELECT id FROM deals WHERE expiry_time <= %s)", (now,))
        cursor.execute("DELETE FROM deals WHERE expiry_time <= %s", (now,))
        db.commit()
        session['recently_cleaned'] = expired_count
        flash(f"Cleaned up {expired_count} expired deals.", "info")
    except Exception as e:
        flash(f"Cleanup Error: {str(e)}", "danger")
    finally:
        if db: db.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/cron/sync/<token>')
def automated_sync(token):
    CRON_TOKEN = os.getenv('CRON_SECRET_TOKEN', 'my-super-secret-123')
    if token != CRON_TOKEN:
        return "Unauthorized", 401
    
    success, count, _ = perform_sync_logic()
    if success:
        return f"Success: {count} deals processed.", 200
    return "Sync Error", 500

@app.route('/admin/force-cleanup')
@login_required
def force_cleanup():
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("""
            DELETE d1 FROM deals d1
            INNER JOIN deals d2 
            WHERE d1.id < d2.id 
            AND d1.product_name = d2.product_name
        """)
        affected = cursor.rowcount
        db.commit()
        flash(f"Optimization Complete: {affected} duplicates removed.", "success")
    except Exception as e:
        flash(f"Cleanup Error: {e}", "danger")
    finally:
        if db: db.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/clear-all')
@login_required
def clear_all_deals():
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("DELETE FROM clicks")
        cursor.execute("DELETE FROM subscriptions")
        cursor.execute("DELETE FROM deals")
        db.commit()
        flash("System Reset: All inventory data has been wiped.", "warning")
    except Exception as e:
        flash(f"Reset Error: {e}", "danger")
    finally:
        if db: db.close()
    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__':
    # Macha, Render requires host='0.0.0.0' and dynamic port
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
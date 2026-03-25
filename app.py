import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
import mysql.connector
from dotenv import load_dotenv
from functools import wraps

# Load local .env file if it exists (for local testing)
load_dotenv()

app = Flask(__name__)

# --- SECURITY CONFIGURATION ---
# Pulls from Environment Variables for safety
app.secret_key = os.getenv('SECRET_KEY', 'dev-key-12345') 

# --- DATABASE CONNECTION ---
def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'),
        port=int(os.getenv('DB_PORT', 3306))
    )

# --- SECURITY DECORATOR ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- 1. PUBLIC HOME PAGE ---
@app.route('/')
def index():
    db = None
    all_deals = []
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        # Query active deals that haven't expired
        query = "SELECT * FROM deals WHERE is_active = TRUE AND expiry_time > NOW() ORDER BY created_at DESC"
        cursor.execute(query)
        all_deals = cursor.fetchall()
        cursor.close()
    except Exception as e:
        print(f"Database Error: {e}")
    finally:
        if db:
            db.close()
    return render_template('index.html', deals=all_deals)

# --- 2. LOGIN & LOGOUT ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # Credentials pulled from Environment Variables (set these on Render)
        env_user = os.getenv('ADMIN_USERNAME', 'admin')
        env_pass = os.getenv('ADMIN_PASSWORD', 'password123')
        
        if request.form['username'] == env_user and request.form['password'] == env_pass:
            session['logged_in'] = True
            flash('Successfully logged in!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid Credentials!', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# --- 3. CLICK TRACKER & REDIRECT ---
@app.route('/buy/<int:deal_id>')
def buy_product(deal_id):
    db = None
    deal = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        # Log the click
        cursor.execute("INSERT INTO clicks (deal_id) VALUES (%s)", (deal_id,))
        db.commit()
        # Get the URL
        cursor.execute("SELECT affiliate_url FROM deals WHERE id = %s", (deal_id,))
        deal = cursor.fetchone()
        cursor.close()
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if db:
            db.close()
            
    if deal:
        return redirect(deal['affiliate_url'])
    return "Deal not found", 404

# --- 4. ADMIN DASHBOARD ---
@app.route('/admin')
@login_required
def admin_dashboard():
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    query = """
    SELECT d.id, d.product_name, d.sale_price, COUNT(c.id) as click_count
    FROM deals d
    LEFT JOIN clicks c ON d.id = c.deal_id
    GROUP BY d.id
    ORDER BY click_count DESC
    """
    cursor.execute(query)
    stats = cursor.fetchall()
    cursor.close()
    db.close()
    return render_template('admin_stats.html', stats=stats)

# --- 5. ADD NEW DEAL ---
@app.route('/admin/add', methods=['GET', 'POST'])
@login_required
def add_deal():
    if request.method == 'POST':
        db = get_db_connection()
        cursor = db.cursor()
        sql = """INSERT INTO deals (product_name, original_price, sale_price, 
                 affiliate_url, image_url, expiry_time) 
                 VALUES (%s, %s, %s, %s, %s, %s)"""
        values = (
            request.form['product_name'], 
            request.form['original_price'], 
            request.form['sale_price'], 
            request.form['affiliate_url'], 
            request.form['image_url'], 
            request.form['expiry_time']
        )
        cursor.execute(sql, values)
        db.commit()
        cursor.close()
        db.close()
        return redirect(url_for('admin_dashboard'))
    return render_template('admin.html')

# --- 6. DELETE DEAL ---
@app.route('/admin/delete/<int:deal_id>')
@login_required
def delete_deal(deal_id):
    db = get_db_connection()
    cursor = db.cursor()
    # Delete clicks first to avoid foreign key errors
    cursor.execute("DELETE FROM clicks WHERE deal_id = %s", (deal_id,))
    cursor.execute("DELETE FROM deals WHERE id = %s", (deal_id,))
    db.commit()
    cursor.close()
    db.close()
    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__':
    # Local development settings
    app.run(host='0.0.0.0', port=5000, debug=True)
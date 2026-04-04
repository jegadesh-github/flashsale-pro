CREATE TABLE IF NOT EXISTS deals (
    id INT AUTO_INCREMENT PRIMARY KEY,
    product_name VARCHAR(255) NOT NULL,
    source_name VARCHAR(80),
    source_product_id VARCHAR(120),
    source_url TEXT,
    merchant_name VARCHAR(120),
    store_name VARCHAR(120),
    category VARCHAR(120),
    original_price DECIMAL(12,2) DEFAULT 0,
    sale_price DECIMAL(12,2) DEFAULT 0,
    discount_percentage INT DEFAULT 0,
    currency_code VARCHAR(12) DEFAULT 'INR',
    affiliate_url TEXT,
    image_url TEXT,
    expiry_time DATETIME,
    is_active BOOLEAN DEFAULT TRUE,
    batch_id VARCHAR(120),
    is_trending BOOLEAN DEFAULT FALSE,
    is_mega BOOLEAN DEFAULT FALSE,
    is_featured BOOLEAN DEFAULT FALSE,
    source_rating DECIMAL(4,2) DEFAULT 0,
    review_count INT DEFAULT 0,
    trust_score INT DEFAULT 70,
    merchandising_badge VARCHAR(120),
    ai_summary TEXT,
    live_views INT DEFAULT 0,
    base_views INT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS clicks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    deal_id INT NOT NULL,
    clicked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    referrer VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    deal_id INT NULL,
    target_price DECIMAL(12,2) NULL,
    category VARCHAR(120) NULL,
    is_active BOOLEAN DEFAULT TRUE,
    last_notified_at DATETIME NULL,
    unsubscribe_token VARCHAR(120) NULL,
    unsubscribed_at DATETIME NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(120),
    role VARCHAR(20) NOT NULL,
    message TEXT NOT NULL,
    detected_category VARCHAR(120),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS email_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    subscription_id INT,
    deal_id INT,
    event_type VARCHAR(60),
    status VARCHAR(40),
    sent_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS automation_runs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    run_type VARCHAR(80),
    status VARCHAR(40),
    summary TEXT,
    deals_processed INT DEFAULT 0,
    emails_sent INT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta

import requests


AMAZON_SEARCH_PLANS = [
    {"category": "Smartphones", "search_index": "Electronics", "keyword": "5G smartphone"},
    {"category": "Laptops", "search_index": "Computers", "keyword": "laptop"},
    {"category": "Fragrances", "search_index": "Beauty", "keyword": "perfume"},
    {"category": "Skincare", "search_index": "Beauty", "keyword": "face serum"},
    {"category": "Groceries", "search_index": "GroceryAndGourmetFood", "keyword": "healthy snacks"},
    {"category": "Home Decoration", "search_index": "HomeAndKitchen", "keyword": "home decor"},
    {"category": "Furniture", "search_index": "Furniture", "keyword": "office chair"},
]


def _env_json(name, default):
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return default


def _to_float(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _price_amount(price_obj):
    if not isinstance(price_obj, dict):
        return 0
    if price_obj.get("Amount") is not None:
        return _to_float(price_obj.get("Amount"))
    if price_obj.get("DisplayAmount"):
        digits = "".join(ch for ch in str(price_obj["DisplayAmount"]) if ch.isdigit() or ch == ".")
        return _to_float(digits)
    return 0


def _extract_image_url(item):
    images = item.get("Images") or {}
    for image_type in ("Primary", "Variant"):
        image_obj = images.get(image_type) or {}
        for size in ("Large", "Medium", "Small"):
            url = ((image_obj.get(size) or {}).get("URL") if isinstance(image_obj.get(size), dict) else None)
            if url:
                return url
    return ""


def _extract_title(item):
    return (((item.get("ItemInfo") or {}).get("Title") or {}).get("DisplayValue") or "").strip()


def _sha256_hex(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AmazonIndiaPAAPIProvider:
    def __init__(self):
        self.access_key = os.getenv("AMAZON_PAAPI_ACCESS_KEY", "").strip()
        self.secret_key = os.getenv("AMAZON_PAAPI_SECRET_KEY", "").strip()
        self.partner_tag = (
            os.getenv("AMAZON_PAAPI_PARTNER_TAG", "").strip()
            or os.getenv("AMAZON_AFFILIATE_ID", "").strip()
            or os.getenv("AFFILIATE_ID", "").strip()
        )
        self.host = os.getenv("AMAZON_PAAPI_HOST", "webservices.amazon.in").strip()
        self.region = os.getenv("AMAZON_PAAPI_REGION", "eu-west-1").strip()
        self.marketplace = os.getenv("AMAZON_PAAPI_MARKETPLACE", "www.amazon.in").strip()
        self.language = os.getenv("AMAZON_PAAPI_LANGUAGE", "en_IN").strip()
        self.item_count = max(1, min(_to_int(os.getenv("AMAZON_PAAPI_ITEM_COUNT", "4"), 4), 10))

    def is_configured(self):
        return bool(self.access_key and self.secret_key and self.partner_tag)

    def missing_fields(self):
        missing = []
        if not self.access_key:
            missing.append("AMAZON_PAAPI_ACCESS_KEY")
        if not self.secret_key:
            missing.append("AMAZON_PAAPI_SECRET_KEY")
        if not self.partner_tag:
            missing.append("AMAZON_PAAPI_PARTNER_TAG or AFFILIATE_ID")
        return missing

    def _sign(self, amz_date, payload_json):
        canonical_headers = (
            f"content-encoding:amz-1.0\n"
            f"content-type:application/json; charset=utf-8\n"
            f"host:{self.host}\n"
            f"x-amz-date:{amz_date}\n"
            f"x-amz-target:com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems\n"
        )
        signed_headers = "content-encoding;content-type;host;x-amz-date;x-amz-target"
        canonical_request = "\n".join([
            "POST",
            "/paapi5/searchitems",
            "",
            canonical_headers,
            signed_headers,
            _sha256_hex(payload_json),
        ])
        date_stamp = amz_date[:8]
        credential_scope = f"{date_stamp}/{self.region}/ProductAdvertisingAPIv1/aws4_request"
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ])
        k_date = hmac.new(("AWS4" + self.secret_key).encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
        k_region = hmac.new(k_date, self.region.encode("utf-8"), hashlib.sha256).digest()
        k_service = hmac.new(k_region, b"ProductAdvertisingAPIv1", hashlib.sha256).digest()
        k_signing = hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return authorization

    def _request(self, plan, session_headers):
        payload = {
            "Keywords": plan["keyword"],
            "SearchIndex": plan["search_index"],
            "ItemCount": self.item_count,
            "PartnerTag": self.partner_tag,
            "PartnerType": "Associates",
            "Marketplace": self.marketplace,
            "LanguagesOfPreference": [self.language],
            "Merchant": "Amazon",
            "Resources": [
                "Images.Primary.Large",
                "Images.Primary.Medium",
                "ItemInfo.Title",
                "Offers.Listings.Price",
            ],
        }
        payload_json = json.dumps(payload, separators=(",", ":"))
        amz_date = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        headers = {
            "Content-Encoding": "amz-1.0",
            "Content-Type": "application/json; charset=utf-8",
            "Host": self.host,
            "X-Amz-Date": amz_date,
            "X-Amz-Target": "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems",
            "Authorization": self._sign(amz_date, payload_json),
        }
        if session_headers:
            headers.update(session_headers)
        response = requests.post(f"https://{self.host}/paapi5/searchitems", data=payload_json, headers=headers, timeout=20)
        response.raise_for_status()
        return response.json()

    def fetch(self, build_standard_deal, build_affiliate_url, session_headers=None):
        if not self.is_configured():
            return [], f"amazon_paapi missing {', '.join(self.missing_fields())}"

        deals = []
        seen_ids = set()
        for plan in AMAZON_SEARCH_PLANS:
            try:
                payload = self._request(plan, session_headers or {})
            except Exception as exc:
                return [], f"amazon_paapi request failed: {exc}"

            items = (((payload.get("SearchResult") or {}).get("Items")) or [])
            for item in items:
                asin = item.get("ASIN")
                if not asin or asin in seen_ids:
                    continue

                title = _extract_title(item)
                detail_page_url = item.get("DetailPageURL", "")
                image_url = _extract_image_url(item)
                listing = (((item.get("Offers") or {}).get("Listings")) or [{}])[0]
                price_obj = listing.get("Price") or {}
                sale_price = _price_amount(price_obj)
                original_price = _price_amount(price_obj.get("SavingBasis") or {}) or sale_price
                discount_percentage = _to_int(((price_obj.get("Savings") or {}).get("Percentage")))
                if not discount_percentage and original_price and sale_price and original_price > sale_price:
                    discount_percentage = int(round(((original_price - sale_price) / original_price) * 100))
                if not title or not sale_price:
                    continue
                if original_price < sale_price:
                    original_price = sale_price

                deals.append(build_standard_deal(
                    product_name=title,
                    source_name="AmazonPAAPI",
                    source_product_id=asin,
                    source_url=detail_page_url,
                    merchant_name="Amazon India",
                    store_name="Amazon India",
                    category=plan["category"],
                    original_price=original_price,
                    sale_price=sale_price,
                    discount_percentage=discount_percentage,
                    affiliate_url=build_affiliate_url(title, detail_page_url, "Amazon India"),
                    image_url=image_url,
                    source_rating=0,
                    review_count=0,
                    currency_code="INR",
                    expiry_time=datetime.now() + timedelta(hours=12),
                ))
                seen_ids.add(asin)
        return deals, None


class PartnerJsonFeedProvider:
    def __init__(self):
        self.feed_url = os.getenv("PARTNER_FEED_URL", "").strip()
        self.feed_name = os.getenv("PARTNER_FEED_NAME", "PartnerFeed").strip()
        self.store_name = os.getenv("PARTNER_FEED_STORE_NAME", "Partner Store").strip()
        self.headers = _env_json("PARTNER_FEED_HEADERS", {})
        self.mappings = _env_json("PARTNER_FEED_MAPPINGS", {})
        self.default_category = os.getenv("PARTNER_FEED_DEFAULT_CATEGORY", "Featured").strip()

    def is_configured(self):
        return bool(self.feed_url)

    def fetch(self, build_standard_deal, build_affiliate_url, session_headers=None):
        if not self.is_configured():
            return [], "partner_json_feed missing PARTNER_FEED_URL"

        try:
            headers = {}
            if session_headers:
                headers.update(session_headers)
            headers.update(self.headers)
            response = requests.get(self.feed_url, headers=headers, timeout=20)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return [], f"partner_json_feed request failed: {exc}"

        items = payload if isinstance(payload, list) else payload.get("items", [])
        deals = []
        for index, item in enumerate(items):
            title_key = self.mappings.get("title", "title")
            url_key = self.mappings.get("url", "url")
            image_key = self.mappings.get("image_url", "image_url")
            sale_key = self.mappings.get("sale_price", "sale_price")
            original_key = self.mappings.get("original_price", "original_price")
            category_key = self.mappings.get("category", "category")
            source_id_key = self.mappings.get("source_id", "id")
            rating_key = self.mappings.get("rating", "rating")
            reviews_key = self.mappings.get("review_count", "review_count")
            merchant_key = self.mappings.get("merchant_name", "merchant_name")

            title = str(item.get(title_key, "")).strip()
            source_url = str(item.get(url_key, "")).strip()
            image_url = str(item.get(image_key, "")).strip()
            sale_price = _to_float(item.get(sale_key))
            original_price = _to_float(item.get(original_key)) or sale_price
            if not title or not sale_price:
                continue
            category = str(item.get(category_key) or self.default_category).strip()
            merchant_name = str(item.get(merchant_key) or self.store_name).strip()
            deals.append(build_standard_deal(
                product_name=title,
                source_name=self.feed_name,
                source_product_id=item.get(source_id_key) or f"{self.feed_name}_{index}",
                source_url=source_url,
                merchant_name=merchant_name,
                store_name=merchant_name,
                category=category,
                original_price=max(original_price, sale_price),
                sale_price=sale_price,
                discount_percentage=int(round(((max(original_price, sale_price) - sale_price) / max(original_price, sale_price)) * 100)) if max(original_price, sale_price) else 0,
                affiliate_url=build_affiliate_url(title, source_url, merchant_name),
                image_url=image_url,
                source_rating=_to_float(item.get(rating_key)),
                review_count=_to_int(item.get(reviews_key)),
                currency_code=str(item.get(self.mappings.get("currency_code", "currency_code")) or "INR"),
                expiry_time=datetime.now() + timedelta(hours=18),
            ))
        return deals, None


def fetch_partner_deals(build_standard_deal, build_affiliate_url, session_headers=None):
    providers = [
        ("amazon_paapi", AmazonIndiaPAAPIProvider()),
        ("partner_json_feed", PartnerJsonFeedProvider()),
    ]

    source_counts = {name: 0 for name, _ in providers}
    warnings = []
    all_deals = []

    for name, provider in providers:
        if hasattr(provider, "is_configured") and not provider.is_configured():
            if hasattr(provider, "missing_fields"):
                warnings.append(f"{name} skipped: missing {', '.join(provider.missing_fields())}")
            else:
                warnings.append(f"{name} skipped: source not configured")
            continue
        deals, warning = provider.fetch(build_standard_deal, build_affiliate_url, session_headers=session_headers or {})
        source_counts[name] = len(deals)
        all_deals.extend(deals)
        if warning:
            warnings.append(warning)

    return all_deals, source_counts, warnings

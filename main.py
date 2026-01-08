from flask import Flask, render_template_string, request, send_file, jsonify, redirect, url_for
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import pandas as pd
import time
import logging
from io import StringIO
import sys
from pymongo import MongoClient
from datetime import datetime
import re
import os
from urllib.parse import quote_plus
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
debug = os.getenv("DEBUG", "False").lower() == "true"
port = os.getenv("PORT", 52021)


class WebLogger:
    def __init__(self):
        self.logs = []
    def write(self, message):
        if message.strip():
            self.logs.append(message.strip())
    def flush(self):
        pass

web_logger = WebLogger()
sys.stdout = web_logger

mongo_user = os.getenv("MONGO_USER")
mongo_password = os.getenv("MONGO_PASSWORD")
mongo_host = os.getenv("MONGO_HOST", "localhost")
mongo_port = os.getenv("MONGO_PORT", "27017")
mongo_db_name = os.getenv("MONGO_DB", "ml")
mongo_auth_source = os.getenv("MONGO_AUTH_SOURCE", "admin")

constructed_uri = None
if mongo_user and mongo_password:
    constructed_uri = f"mongodb://{quote_plus(mongo_user)}:{quote_plus(mongo_password)}@{mongo_host}:{mongo_port}/{mongo_db_name}?authSource={mongo_auth_source}"

env_mongo_uri = os.getenv("MONGO_URI")

if env_mongo_uri and "@" in env_mongo_uri:
    final_mongo_uri = env_mongo_uri
    logger.info("Configuration: Using MONGO_URI from environment (contains credentials).")
elif constructed_uri:
    final_mongo_uri = constructed_uri
    logger.info("Configuration: Using constructed URI from MONGO_USER and MONGO_PASSWORD (ignoring credential-less MONGO_URI if present).")
elif env_mongo_uri:
    final_mongo_uri = env_mongo_uri
    logger.info("Configuration: Using MONGO_URI from environment (no explicit credentials found in URI or env vars).")
else:
    final_mongo_uri = f"mongodb://{mongo_host}:{mongo_port}/"
    logger.info("Configuration: Using default localhost URI.")

def diagnose_mongo_connection(uri):
    """
    Diagnoses MongoDB connection issues by attempting a ping and logging details.
    """
    masked_uri_log = re.sub(r':([^@]+)@', ':****@', uri)
    logger.info(f"Diagnostic: Attempting connection to: {masked_uri_log}")

    # Check what variables are present (keys only for security)
    env_vars = {
        key: bool(os.getenv(key))
        for key in ["MONGO_URI", "MONGO_USER", "MONGO_PASSWORD", "MONGO_HOST", "MONGO_PORT", "MONGO_DB", "MONGO_AUTH_SOURCE"]
    }
    logger.info(f"Diagnostic: Environment Variables Presence: {env_vars}")

    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        # Try a simple administrative command to verify connectivity and auth
        info = client.server_info()
        logger.info("Diagnostic: Connection Successful. Server Info available.")
        logger.debug(f"Server Info: {info}")
        return client
    except Exception as e:
        logger.error("Diagnostic: Connection FAILED.")
        logger.error(f"Error Type: {type(e).__name__}")
        logger.error(f"Error Details: {e}")
        # Log authentication specific details if possible
        if "Authentication failed" in str(e) or "requires authentication" in str(e):
             logger.error("Diagnostic: This is an AUTHENTICATION error. Check username, password, and authSource.")
             if not os.getenv("MONGO_AUTH_SOURCE"):
                 logger.error("Diagnostic: MONGO_AUTH_SOURCE is not set. Defaulting to 'admin'. Try setting it to your database name.")
        raise e

# Perform diagnosis
try:
    mongo_client = diagnose_mongo_connection(final_mongo_uri)
except Exception as e:
    logger.critical("Failed to connect to MongoDB during startup diagnostics. Application might crash on first request.")
    # We allow the app to continue so the logs are flushed/visible, but the client might be in a bad state.
    mongo_client = MongoClient(final_mongo_uri)

mongo_db = mongo_client[mongo_db_name]
cars_collection = mongo_db[os.getenv("MONGO_COLLECTION", "cars")]

def extract_unique_id(url):
    match = re.search(r"MLA-(\d+)", url or "")
    return match.group(1) if match else None

def extract_picture_url(item):
    # Busca el primer <img class="poly-component__picture">
    img = item.find('img', class_='poly-component__picture')
    if not img:
        return ""
    src = img.get('src', "")
    # Si src es placeholder GIF o vacío, usa data-src o data-original
    if not src or src.startswith("data:image"):
        # Algunos sitios usan data-src, otros data-original
        src = img.get('data-src') or img.get('data-original') or ""
    return src

def determine_currency_and_format(price_num):
    """
    Determines currency based on price magnitude and formats the price string.
    Rule: Price > 1,000,000 -> ARS (Pesos Argentinos)
          Price <= 1,000,000 -> USD (Dólares)
    Returns: (currency_code, formatted_price_string)
    """
    if not price_num:
        return 'N/A', 'N/A'

    # Threshold logic: > 1 million is likely ARS
    if price_num > 1000000:
        currency = 'ARS'
        # Format with dots for thousands: 15000000 -> 15.000.000
        formatted_num = f"{price_num:,.0f}".replace(',', '.')
        price_formatted = f"$ {formatted_num}"
    else:
        currency = 'USD'
        formatted_num = f"{price_num:,.0f}".replace(',', '.')
        price_formatted = f"US$ {formatted_num}"

    return currency, price_formatted

def get_session():
    """Creates a session with retry logic and a modern User-Agent."""
    session = requests.Session()
    retry = Retry(
        total=3,
        read=3,
        connect=3,
        backoff_factor=0.5,
        status_forcelist=(500, 502, 503, 504),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
        'Sec-Ch-Ua': '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'Upgrade-Insecure-Requests': '1',
        'Referer': 'https://www.mercadolibre.com.ar/'
    })
    return session

def scrape_mercado_libre(search_term):
    base_url = "https://listado.mercadolibre.com.ar/"
    # Headers are now managed by the session
    all_items = []
    page = 1
    session = get_session()

    while True:
        url = f"{base_url}{search_term.replace(' ', '-')}_Desde_{(page - 1) * 48 + 1}"
        print(f"Scraping page {page}: {url}")
        try:
            response = session.get(url, timeout=10)
            if response.status_code == 404:
                print(f"No more pages available (404 error)")
                break
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Check for blocking/verification
            if "account-verification" in response.text or "suspicious-traffic" in response.text:
                print("Scraper blocked by MercadoLibre security check (account verification/suspicious traffic).")
                break

            no_results = soup.find('p', class_='ui-search-sidebar__no-results-message')
            if no_results:
                print(f"No results message detected: {no_results.text.strip()}")
                break

            # Try multiple selectors for items
            items = soup.find_all('div', class_='ui-search-result__wrapper')
            if not items:
                items = soup.find_all('div', class_='ui-search-result__content-wrapper')
            if not items:
                items = soup.find_all('li', class_='ui-search-layout__item')

            if not items:
                print("No items found in page")
                # Debug: Print title to see what page we are on if not blocked but no items
                print(f"Page title: {soup.title.text if soup.title else 'No title'}")
                break
            for item in items:
                try:
                    title_elem = item.find('a', class_='poly-component__title')
                    title = title_elem.text.strip() if title_elem and title_elem.text else 'No title'
                    link = title_elem.get('href', '#') if title_elem else '#'
                    unique_id = extract_unique_id(link)
                    if not unique_id:
                        continue
                    picture_url = extract_picture_url(item)
                    price_elem = item.find('span', class_='andes-money-amount__fraction')
                    price_text = price_elem.text.strip() if price_elem and price_elem.text else 'N/A'

                    price_num = 0
                    if price_text != 'N/A':
                        try:
                            # Remove existing formatting to get raw number
                            price_num = int(price_text.replace('.', '').replace(',', '').strip())
                        except ValueError:
                            price_num = 0

                    currency, price_formatted = determine_currency_and_format(price_num)

                    details = item.find_all('li', class_='poly-attributes_list__item')
                    year = details[0].text.strip() if len(details) > 0 and details[0].text else 'N/A'
                    km = details[1].text.strip() if len(details) > 1 and details[1].text else 'N/A'
                    location_elem = item.find('span', class_='poly-component__location')
                    location = location_elem.text.strip() if location_elem and location_elem.text else 'N/A'

                    year_num = int(year) if year != 'N/A' else 0
                    km_num = int(km.replace('Km', '').replace('.', '').strip()) if km != 'N/A' else 0
                    all_items.append({
                        'unique_id': unique_id,
                        'image': picture_url,
                        'description': title,
                        'price': price_formatted,
                        'price_num': price_num,
                        'currency': currency,
                        'year': year,
                        'year_num': year_num,
                        'kilometers': km,
                        'kilometers_num': km_num,
                        'location': location,
                        'link': link,
                        'search_term': search_term
                    })
                    print(f"Added item: {title[:50]}... (ID: {unique_id})")
                except Exception as e:
                    print(f"Error processing item: {e}")
                    continue
            page += 1
            time.sleep(2)
        except Exception as e:
            print(f"Error scraping page {page}: {e}")
            break
    df = pd.DataFrame(all_items)
    if not df.empty:
        records = df.to_dict(orient='records')
        timestamp = datetime.utcnow()
        today_str = timestamp.strftime('%Y-%m-%d')
        for rec in records:
            rec['timestamp'] = timestamp
            rec['search_term'] = search_term
            rec['date_str'] = today_str
            filter_query = {
                'unique_id': rec['unique_id'],
                'search_term': search_term,
                'date_str': today_str
            }
            cars_collection.replace_one(filter_query, rec, upsert=True)
    return df

def get_historical_data(search_term):
    print(f"Recuperando datos históricos para: {search_term}")
    pipeline = [
        {"$match": {"search_term": search_term}},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id": "$unique_id",
            "doc": {"$first": "$$ROOT"}
        }},
        {"$replaceRoot": {"newRoot": "$doc"}}
    ]
    results = list(cars_collection.aggregate(pipeline))
    df = pd.DataFrame(results)
    return df

def get_inventory_stats(search_term=None):
    match = {}
    if search_term:
        match = {"search_term": search_term}
    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$search_term", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}
    ]
    return list(cars_collection.aggregate(pipeline))

def get_price_stats(search_term=None):
    match = {}
    if search_term:
        match = {"search_term": search_term}
    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {"term": "$search_term", "currency": "$currency"},
            "avg_price": {"$avg": "$price_num"}
        }},
        {"$sort": {"_id.term": 1}}
    ]
    return list(cars_collection.aggregate(pipeline))

def get_year_stats(search_term=None):
    match = {"year_num": {"$gt": 0}}
    if search_term:
        match["search_term"] = search_term
    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {"year": "$year_num", "currency": "$currency"},
            "avg_price": {"$avg": "$price_num"},
            "avg_km": {"$avg": "$kilometers_num"},
            "count": {"$sum": 1}
        }},
        {"$sort": {"_id.year": 1}}
    ]
    return list(cars_collection.aggregate(pipeline))

def get_location_stats(search_term=None):
    match = {}
    if search_term:
        match = {"search_term": search_term}
    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$location", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10}
    ]
    return list(cars_collection.aggregate(pipeline))

def get_daily_price_stats(search_term=None):
    match = {}
    if search_term:
        match = {"search_term": search_term}
    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {"date": "$date_str", "currency": "$currency"},
            "avg_price": {"$avg": "$price_num"},
            "count": {"$sum": 1}
        }},
        {"$sort": {"_id.date": 1}}
    ]
    return list(cars_collection.aggregate(pipeline))

def get_all_prices(search_term=None):
    query = {}
    if search_term:
        query["search_term"] = search_term
    return list(cars_collection.find(query, {"price_num": 1, "currency": 1, "_id": 0}))

@app.route('/', methods=['GET', 'POST'])
def index():
    sort = request.args.get('sort', '')
    order = request.args.get('order', 'asc')
    search_terms = sorted(set(doc['search_term'] for doc in cars_collection.find({}, {'search_term': 1})))
    search_term = ""
    exchange_rate = ""
    target_currency = "USD"

    if request.method == 'POST':
        search_term = request.form.get('search_term') or request.form.get('dropdown_search_term') or ""
        exchange_rate = request.form.get('exchange_rate', '')
        target_currency = request.form.get('target_currency', 'USD')

        try:
            exchange_rate_val = float(exchange_rate) if exchange_rate else 0
        except ValueError:
            exchange_rate_val = 0

        action = request.form.get('action', 'scrape')
        web_logger.logs = []  # Clear previous logs

        if action == 'history':
            df = get_historical_data(search_term)
        elif action == 'charts':
            return redirect(url_for('charts_view', search_term=search_term, exchange_rate=exchange_rate))
        elif action == 'scrape_all':
            all_dfs = []
            for term in search_terms:
                web_logger.write(f"Iniciando scrape masivo para: {term}")
                try:
                    d = scrape_mercado_libre(term)
                    if not d.empty:
                        all_dfs.append(d)
                except Exception as e:
                    web_logger.write(f"Error scraping {term}: {e}")
            if all_dfs:
                df = pd.concat(all_dfs, ignore_index=True)
            else:
                df = pd.DataFrame()
            search_term = "Todos (Batch)"
        else:
            df = scrape_mercado_libre(search_term)

        # Post-process DataFrame to ensure currency and correct price formatting
        # This handles both new scrapes (which already have it) and historical data (which might not)
        variation_list = []
        currencies = []
        formatted_prices = []
        normalized_prices = []

        for _, row in df.iterrows():
            # Original Currency detection
            p_num = row.get('price_num', 0)
            src_curr, _ = determine_currency_and_format(p_num)

            # Conversion Logic
            final_price_val = p_num
            final_curr = src_curr

            if exchange_rate_val > 0:
                if target_currency == 'USD' and src_curr == 'ARS':
                    final_price_val = p_num / exchange_rate_val
                    final_curr = 'USD'
                elif target_currency == 'ARS' and src_curr == 'USD':
                    final_price_val = p_num * exchange_rate_val
                    final_curr = 'ARS'
                elif target_currency == src_curr:
                    final_curr = target_currency
            else:
                 # If no exchange rate, we can't convert, but user requested target_currency.
                 # Current logic: default to original.
                 # Or should we try to respect the target currency request if possible (e.g. they match)?
                 if target_currency == src_curr:
                     final_curr = target_currency

            # Format the final price
            if final_curr == 'ARS':
                formatted_num = f"{final_price_val:,.0f}".replace(',', '.')
                p_fmt = f"$ {formatted_num}"
            else:
                formatted_num = f"{final_price_val:,.0f}".replace(',', '.')
                p_fmt = f"US$ {formatted_num}"

            currencies.append(final_curr)
            formatted_prices.append(p_fmt)

            # Normalized price (for sorting) should align with the displayed currency
            normalized_prices.append(final_price_val)

            # Variation logic
            current_date_str = row.get('date_str', datetime.utcnow().strftime('%Y-%m-%d'))
            prev_doc = cars_collection.find_one({
                'unique_id': row['unique_id'],
                'search_term': row['search_term'],
                'date_str': {'$lt': current_date_str}
            }, sort=[('date_str', -1)])
            prev_price = prev_doc['price_num'] if prev_doc else None
            curr_price = row['price_num']
            if prev_price is None:
                variation = ''
            elif curr_price > prev_price:
                variation = '↑'
            elif curr_price < prev_price:
                variation = '↓'
            else:
                variation = '='
            variation_list.append(variation)

        df['currency'] = currencies
        df['price'] = formatted_prices
        df['variación'] = variation_list
        df['normalized_price'] = normalized_prices

        if 'image' not in df.columns:
            df['image'] = ""

        # Explicit column order is not strictly necessary here because we will use explicit columns in HTML
        # But we can keep it clean if we want
        if sort and sort in df.columns:
            df = df.sort_values(by=sort, ascending=(order == 'asc'))
        #csv_filename = f"mercado_libre_{search_term.replace(' ', '_')}.csv"
        #df.to_csv(csv_filename, index=False)
        return render_template_string('''
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <title>Mercado Libre Scraper</title>
            <!-- Bootstrap 5 CSS -->
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
            <!-- DataTables Bootstrap 5 -->
            <link href="https://cdn.datatables.net/1.13.7/css/dataTables.bootstrap5.min.css" rel="stylesheet">
            <style>
                body { background: #f6f7fa; }
                .container { max-width: 1300px; margin-top: 35px; }
                .table img.product-thumb { width:100px; height:100px; object-fit:cover; border-radius:8px; }
                .evol-btn { padding: 3px 12px; font-size: 0.95em; }
                .modal-content { border-radius: 16px; }
                .form-label { margin-bottom: 0.3em; }
                .card { box-shadow: 0 3px 10px rgba(0,0,0,0.09);}
                .table thead th { background: #33416c; color: #fff; }
            </style>
        </head>
        <body>
        <div class="container">
            <h1 class="mb-3 display-5 fw-bold text-primary">Mercado Libre Scraper</h1>
            <div class="card shadow-sm mb-4">
                <div class="card-body">
                    <form method="POST" id="searchForm" class="row g-3 align-items-end">
                        <div class="col-md-4">
                            <label for="searchInput" class="form-label">Término a buscar</label>
                            <input type="text" name="search_term" id="searchInput" class="form-control" placeholder="Ejemplo: BMW X3" value="{{ search_term }}">
                        </div>
                        <div class="col-md-4">
                            <label for="dropdown_search_term" class="form-label">Búsquedas anteriores</label>
                            <select name="dropdown_search_term" id="dropdown_search_term" class="form-select" onchange="onDropdownChange(this)">
                                <option value="">-- Seleccione búsqueda anterior --</option>
                                {% for term in search_terms %}
                                    <option value="{{ term }}" {% if term == search_term %}selected{% endif %}>{{ term }}</option>
                                {% endfor %}
                            </select>
                        </div>
                        <div class="col-md-2">
                            <label for="exchangeRate" class="form-label">Tipo de Cambio</label>
                            <input type="number" step="0.01" name="exchange_rate" id="exchangeRate" class="form-control" placeholder="ARS/USD" value="{{ exchange_rate }}">
                        </div>
                        <div class="col-md-2">
                            <label for="targetCurrency" class="form-label">Moneda Base</label>
                            <select name="target_currency" id="targetCurrency" class="form-select">
                                <option value="USD" {% if target_currency == 'USD' %}selected{% endif %}>USD</option>
                                <option value="ARS" {% if target_currency == 'ARS' %}selected{% endif %}>ARS</option>
                            </select>
                        </div>
                        <div class="col-md-2 d-grid gap-2">
                            <button type="submit" name="action" value="scrape" class="btn btn-success fw-semibold">Scrapear</button>
                            <button type="submit" name="action" value="scrape_all" class="btn btn-warning fw-semibold">Scrapear Todos</button>
                            <button type="submit" name="action" value="history" class="btn btn-secondary fw-semibold">Ver Histórico</button>
                            <button type="submit" name="action" value="charts" class="btn btn-info fw-semibold">Ver Estadísticas</button>
                        </div>
                    </form>
                </div>
            </div>

            <!-- Filter Card -->
            <div class="card shadow-sm mb-4">
                <div class="card-header bg-light py-2 d-flex justify-content-between align-items-center">
                    <h5 class="mb-0 h6 fw-bold text-primary">Filtros de resultados</h5>
                    <button type="button" id="resetFilters" class="btn btn-outline-danger btn-sm" style="font-size: 0.8em; padding: 2px 8px;">Reset</button>
                </div>
                <div class="card-body py-2">
                    <div class="row g-2">
                        <!-- Price Filter -->
                        <div class="col-md-3">
                            <label class="form-label small fw-semibold text-secondary mb-1">Precio (Normalizado)</label>
                            <div class="input-group input-group-sm">
                                <input type="number" id="minPrice" class="form-control" placeholder="Min">
                                <span class="input-group-text px-1">-</span>
                                <input type="number" id="maxPrice" class="form-control" placeholder="Max">
                            </div>
                        </div>
                        <!-- Year Filter -->
                        <div class="col-md-2">
                            <label class="form-label small fw-semibold text-secondary mb-1">Año</label>
                            <div class="input-group input-group-sm">
                                <input type="number" id="minYear" class="form-control" placeholder="Min">
                                <span class="input-group-text px-1">-</span>
                                <input type="number" id="maxYear" class="form-control" placeholder="Max">
                            </div>
                        </div>
                        <!-- Km Filter -->
                        <div class="col-md-3">
                            <label class="form-label small fw-semibold text-secondary mb-1">Kilómetros</label>
                            <div class="input-group input-group-sm">
                                <input type="number" id="minKm" class="form-control" placeholder="Min">
                                <span class="input-group-text px-1">-</span>
                                <input type="number" id="maxKm" class="form-control" placeholder="Max">
                            </div>
                        </div>
                        <!-- Location Filter -->
                        <div class="col-md-2">
                            <label class="form-label small fw-semibold text-secondary mb-1">Ubicación</label>
                            <input type="text" id="locationFilter" class="form-control form-control-sm" placeholder="Buscar...">
                        </div>
                        <!-- Evolution Filter -->
                        <div class="col-md-2">
                            <label class="form-label small fw-semibold text-secondary mb-1">Evolución</label>
                            <select id="evolutionFilter" class="form-select form-select-sm">
                                <option value="">Todos</option>
                                <option value="↑">Ascendente (↑)</option>
                                <option value="↓">Descendente (↓)</option>
                                <option value="=">Igual (=)</option>
                            </select>
                        </div>
                    </div>
                </div>
            </div>

            <div class="bg-white rounded p-3 shadow-sm mb-4">
                <h2 class="mb-3 h4">Resultados <span class="text-secondary small">({{ df|length }} ítems)</span></h2>
                <div class="table-responsive">
                    <table id="resultsTable" class="table table-striped table-hover align-middle">
                        <thead>
                            <tr>
                                <th>Imagen</th>
                                <th>Descripción</th>
                                <th>Precio</th>
                                <th>Moneda</th>
                                <th>Año</th>
                                <th>Km</th>
                                <th>Ubicación</th>
                                <th>Enlace</th>
                                <th>Evolución</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for _, row in df.iterrows() %}
                            <tr data-evolution="{{ row.variación }}">
                                <td>
                                    {% if row.image %}
                                        <img src="{{ row.image }}" class="product-thumb" loading="lazy">
                                    {% else %}
                                        <span class="text-secondary">N/A</span>
                                    {% endif %}
                                </td>
                                <td>{{ row.description }}</td>
                                <td data-order="{{ row.normalized_price }}">{{ row.price }}</td>
                                <td>{{ row.currency }}</td>
                                <td data-order="{{ row.year_num }}">{{ row.year }}</td>
                                <td data-order="{{ row.kilometers_num }}">{{ row.kilometers }}</td>
                                <td>{{ row.location }}</td>
                                <td>
                                    {% if row.link %}
                                    <a href="{{ row.link }}" class="btn btn-outline-primary btn-sm fw-semibold" target="_blank">
                                        Ver producto
                                    </a>
                                    {% else %}
                                    <span class="text-secondary">N/A</span>
                                    {% endif %}
                                </td>
                                <td>
                                    <span class="badge bg-light text-dark border me-1">{{ row.variación }}</span>
                                    <button type="button" class="btn btn-outline-secondary btn-sm evol-btn show-history"
                                            data-uniqueid="{{ row.unique_id }}"
                                            data-searchterm="{{ row.search_term }}">
                                        Ver
                                    </button>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Modal Bootstrap para gráfico -->
        <div class="modal fade" id="chartModal" tabindex="-1" aria-labelledby="chartModalLabel" aria-hidden="true">
          <div class="modal-dialog modal-lg modal-dialog-centered">
            <div class="modal-content p-2">
              <div class="modal-header">
                <h5 class="modal-title" id="chartModalLabel">Evolución histórica del precio promedio</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Cerrar"></button>
              </div>
              <div class="modal-body text-center">
                <canvas id="priceChart" width="700" height="320"></canvas>
              </div>
            </div>
          </div>
        </div>

        <!-- Bootstrap & JS -->
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
        <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
        <script src="https://cdn.datatables.net/1.13.7/js/jquery.dataTables.min.js"></script>
        <script src="https://cdn.datatables.net/1.13.7/js/dataTables.bootstrap5.min.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <script>
        function onDropdownChange(sel) {
            if(sel.value) {
                document.getElementById('searchInput').value = sel.value;
            }
        }
        $(document).ready(function() {
            // Restore filter values from localStorage
            const filterIds = ['minPrice', 'maxPrice', 'minYear', 'maxYear', 'minKm', 'maxKm', 'locationFilter', 'evolutionFilter'];
            filterIds.forEach(id => {
                const storedVal = localStorage.getItem('ml_scraper_' + id);
                if (storedVal !== null) {
                    $('#' + id).val(storedVal);
                }
            });

            // Custom filtering function which will search data in column four between two values
            $.fn.dataTable.ext.search.push(
                function(settings, data, dataIndex, rowData, counter) {
                    // Normalize inputs
                    var minPrice = parseFloat($('#minPrice').val()) || 0;
                    var maxPrice = parseFloat($('#maxPrice').val()) || Infinity;
                    if ($('#maxPrice').val() === "") maxPrice = Infinity;

                    var minYear = parseInt($('#minYear').val()) || 0;
                    var maxYear = parseInt($('#maxYear').val()) || Infinity;
                    if ($('#maxYear').val() === "") maxYear = Infinity;

                    var minKm = parseInt($('#minKm').val()) || 0;
                    var maxKm = parseInt($('#maxKm').val()) || Infinity;
                    if ($('#maxKm').val() === "") maxKm = Infinity;

                    var locationTerm = $('#locationFilter').val().toLowerCase();
                    var evolutionTerm = $('#evolutionFilter').val();

                    // Get row data (using data-order attributes where available via DataTables API usually, but here 'data' array contains rendered text)
                    // However, we want the raw numeric values for range filtering.

                    var api = new $.fn.dataTable.Api(settings);
                    var rowNode = api.row(dataIndex).node();
                    var evolution = $(rowNode).data('evolution') || "";

                    // Filter by Evolution
                    if (evolutionTerm && evolution !== evolutionTerm) {
                        return false;
                    }

                    // Filter by Location (simple text match)
                    var location = data[6].toLowerCase();
                    if (locationTerm && !location.includes(locationTerm)) {
                        return false;
                    }

                    // Numeric Filters
                    // Column 2: Price (normalized in data-order)
                    var priceVal = parseFloat(api.cell(dataIndex, 2).render('sort')) || 0;
                    if (priceVal < minPrice || priceVal > maxPrice) {
                        return false;
                    }

                    // Column 4: Year
                    var yearVal = parseFloat(api.cell(dataIndex, 4).render('sort')) || 0;
                    if (yearVal < minYear || yearVal > maxYear) {
                        return false;
                    }

                    // Column 5: Km
                    var kmVal = parseFloat(api.cell(dataIndex, 5).render('sort')) || 0;
                    if (kmVal < minKm || kmVal > maxKm) {
                        return false;
                    }

                    return true;
                }
            );

            var table = $('#resultsTable').DataTable({
                paging: false,
                info: false,
                language: {search: "Buscar:", zeroRecords: "No se encontraron registros"}
            });

            // Event listeners for inputs to redraw table and save to localStorage
            $('#minPrice, #maxPrice, #minYear, #maxYear, #minKm, #maxKm, #locationFilter, #evolutionFilter').on('keyup change', function() {
                localStorage.setItem('ml_scraper_' + this.id, $(this).val());
                table.draw();
            });

            // Reset filters button
            $('#resetFilters').on('click', function() {
                const filterIds = ['minPrice', 'maxPrice', 'minYear', 'maxYear', 'minKm', 'maxKm', 'locationFilter', 'evolutionFilter'];
                filterIds.forEach(id => {
                    localStorage.removeItem('ml_scraper_' + id);
                    $('#' + id).val('');
                });
                table.draw();
            });

        });

        // Modal gráfico evolución con Bootstrap
        let chart = null;
        let chartModal = new bootstrap.Modal(document.getElementById('chartModal'));
        function updateChart(history) {
            const labels = history.map(point => point.date);
            const data = history.map(point => point.avg_price);
            if(chart) { chart.destroy(); }
            chart = new Chart(document.getElementById('priceChart').getContext('2d'), {
                type: 'line',
                data: { labels: labels, datasets: [{ label: 'Precio promedio (USD)', data: data, fill: false, borderColor: 'rgb(75, 192, 192)', tension: 0.1 }] },
                options: { responsive: true, maintainAspectRatio: false, scales: { x: { display: true, title: { display: true, text: 'Fecha' }}, y: { display: true, title: { display: true, text: 'Precio USD'}}}}
            });
        }
        $(document).on('click', '.show-history', function() {
            const unique_id = $(this).data('uniqueid');
            const search_term = $(this).data('searchterm');
            fetch('/history', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({unique_id, search_term})
            })
            .then(response => response.json())
            .then(result => {
                updateChart(result.history);
                chartModal.show();
            });
        });

        // Logging en consola
        {% if logs %}
            {% for log in logs %}
                console.log("[SCRAPER]", `{{ log|e }}`);
            {% endfor %}
        {% endif %}
        </script>
        </body>
        </html>
        ''', logs=web_logger.logs, df=df, search_terms=search_terms, search_term=search_term, exchange_rate=exchange_rate, target_currency=target_currency)

    # GET (página inicial)
    return render_template_string('''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Mercado Libre Scraper</title>
            <link rel="stylesheet" type="text/css" href="https://cdn.datatables.net/1.11.5/css/jquery.dataTables.min.css">
            <link rel="stylesheet" type="text/css" href="https://cdn.datatables.net/buttons/2.2.2/css/buttons.dataTables.min.css">
<style>
    :root { --bg-dark: #1a1a1a; --text-light: #e0e0e0; --primary-accent: #4a6fa5; --secondary-accent: #6d8bc7; --table-border: #3a3a3a; }
    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 20px; background-color: var(--bg-dark); color: var(--text-light); }
    h1, h2, h3 { color: var(--primary-accent); }
</style>
        </head>
        <body>
            <h1>Mercado Libre Scraper</h1>
            <form method="POST" id="searchForm">
                <input type="text" name="search_term" id="searchInput" placeholder="Enter search term">
                <select name="dropdown_search_term" id="dropdown_search_term" onchange="onDropdownChange(this)">
                    <option value="">-- Seleccione búsqueda anterior --</option>
                    {% for term in search_terms %}
                        <option value="{{ term }}">{{ term }}</option>
                    {% endfor %}
                </select>
                <input type="number" step="0.01" name="exchange_rate" placeholder="Tipo de cambio (ARS/USD)">
                <select name="target_currency">
                    <option value="USD" selected>USD</option>
                    <option value="ARS">ARS</option>
                </select>
                <button type="submit" name="action" value="scrape">Scrape</button>
                <button type="submit" name="action" value="scrape_all">Scrapear Todos</button>
                <button type="submit" name="action" value="history">Ver Histórico</button>
                <button type="submit" name="action" value="charts" style="margin-left:10px;">Ver Estadísticas</button>
            </form>
            <script>
            function onDropdownChange(sel) {
                if(sel.value) {
                    document.getElementById('searchInput').value = sel.value;
                }
            }
            </script>
            <script>
                {% if logs %}
                    {% for log in logs %}
                        console.log("[SCRAPER]", `{{ log|e }}`);
                    {% endfor %}
                {% endif %}
            </script>
        </body>
        </html>
    ''', logs=web_logger.logs, search_terms=search_terms)

@app.route('/history', methods=['POST'])
def history():
    data = request.json
    unique_id = str(data.get('unique_id')).strip()
    search_term = data['search_term']
    docs = list(cars_collection.find({
        'unique_id': unique_id,
        'search_term': search_term
    }))
    history_points = {}
    for doc in docs:
        date = doc['timestamp'].strftime('%Y-%m-%d') if hasattr(doc['timestamp'], 'strftime') else str(doc['timestamp'])
        price = doc.get('price_num', 0)
        if price:
            history_points.setdefault(date, []).append(price)
    history_list = [{'date': date, 'avg_price': sum(prices)//len(prices)} for date, prices in sorted(history_points.items())]
    return jsonify({'history': history_list})

@app.route('/charts', methods=['GET', 'POST'])
def charts_view():
    search_term = request.args.get('search_term', request.form.get('search_term', ''))
    exchange_rate = request.args.get('exchange_rate', request.form.get('exchange_rate', ''))

    try:
        exchange_rate_val = float(exchange_rate) if exchange_rate else 0
    except ValueError:
        exchange_rate_val = 0

    # Fetch data
    inventory_stats = get_inventory_stats(search_term)
    price_stats = get_price_stats(search_term)
    year_stats = get_year_stats(search_term)
    location_stats = get_location_stats(search_term)
    daily_price_stats = get_daily_price_stats(search_term)
    all_prices = get_all_prices(search_term)

    # Calculate Price Distribution in Python
    price_dist = {'labels': [], 'data': []}
    if all_prices:
        normalized = []
        for item in all_prices:
            p = item.get('price_num', 0)
            c = item.get('currency', 'USD')
            val = p
            if c == 'ARS' and exchange_rate_val > 0:
                val = p / exchange_rate_val
            elif c == 'ARS' and exchange_rate_val == 0:
                continue # Skip unconvertible ARS if we want USD distribution

            if val > 0:
                normalized.append(val)

        if normalized:
            # Simple histogram logic
            min_p = min(normalized)
            max_p = max(normalized)
            # Create 10 bins
            if min_p == max_p:
                price_dist['labels'] = [f"{min_p:,.0f}"]
                price_dist['data'] = [len(normalized)]
            else:
                import numpy as np
                counts, bin_edges = np.histogram(normalized, bins=10)
                price_dist['data'] = counts.tolist()
                labels = []
                for i in range(len(bin_edges)-1):
                    labels.append(f"{bin_edges[i]:,.0f} - {bin_edges[i+1]:,.0f}")
                price_dist['labels'] = labels

    return render_template_string('''
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <title>Estadísticas y Gráficos - Mercado Libre Scraper</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body { background: #f6f7fa; }
            .container { max-width: 1400px; margin-top: 35px; margin-bottom: 50px; }
            .card { border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); border: none; }
            .card-header { background: #fff; border-bottom: 1px solid #eee; padding: 15px 20px; font-weight: 600; color: #33416c; border-radius: 12px 12px 0 0 !important; }
            .chart-container { position: relative; height: 350px; width: 100%; }
        </style>
    </head>
    <body>
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h1 class="display-5 fw-bold text-primary">Estadísticas del Mercado</h1>
            <a href="/" class="btn btn-outline-secondary">← Volver al Listado</a>
        </div>

        {% if search_term %}
        <div class="alert alert-info d-flex align-items-center" role="alert">
            <svg class="bi flex-shrink-0 me-2" width="24" height="24" role="img" aria-label="Info:"><use xlink:href="#info-fill"/></svg>
            <div>
                Mostrando resultados para: <strong>{{ search_term }}</strong>
            </div>
        </div>
        {% endif %}

        <div class="card mb-4">
            <div class="card-body">
                <form method="GET" class="row align-items-end">
                    <div class="col-md-3">
                        <label class="form-label">Término de Búsqueda</label>
                        <input type="text" name="search_term" class="form-control" value="{{ search_term }}" placeholder="Opcional">
                    </div>
                    <div class="col-md-3">
                        <label class="form-label">Tasa de Cambio (ARS/USD)</label>
                        <input type="number" step="0.01" name="exchange_rate" class="form-control" placeholder="Ej: 1200" value="{{ exchange_rate }}">
                        <div class="form-text small">Para unificar precios en los gráficos.</div>
                    </div>
                    <div class="col-md-2">
                         <button type="submit" class="btn btn-primary w-100">Actualizar</button>
                    </div>
                </form>
            </div>
        </div>

        <div class="row g-4">
            <!-- 1. Inventory Volume (or single stat if filtered) -->
            <div class="col-md-4">
                <div class="card h-100">
                    <div class="card-header">Inventario</div>
                    <div class="card-body">
                         <div class="chart-container">
                            <canvas id="inventoryChart"></canvas>
                         </div>
                    </div>
                </div>
            </div>

            <!-- 2. Location Distribution -->
            <div class="col-md-4">
                <div class="card h-100">
                    <div class="card-header">Top Ubicaciones</div>
                    <div class="card-body">
                        <div class="chart-container">
                            <canvas id="locationChart"></canvas>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 3. Price Distribution -->
            <div class="col-md-4">
                <div class="card h-100">
                    <div class="card-header">Distribución de Precios (USD)</div>
                    <div class="card-body">
                        <div class="chart-container">
                            <canvas id="priceDistChart"></canvas>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 4. Price Evolution (Time) -->
            <div class="col-12">
                <div class="card h-100">
                    <div class="card-header">Evolución de Precio Promedio en el Tiempo</div>
                    <div class="card-body">
                        <div class="chart-container" style="height: 300px;">
                            <canvas id="evolutionChart"></canvas>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 5. Price by Model (Legacy) -->
            <div class="col-md-6">
                <div class="card h-100">
                    <div class="card-header">Precio Promedio por Modelo (Estimado en USD)</div>
                    <div class="card-body">
                        <div class="chart-container">
                            <canvas id="priceChart"></canvas>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 6. Price Depreciaton by Year -->
            <div class="col-md-6">
                <div class="card h-100">
                    <div class="card-header">Curva de Depreciación (Precio vs Año)</div>
                    <div class="card-body">
                        <div class="chart-container">
                            <canvas id="depreciationChart"></canvas>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 7. Usage by Year -->
            <div class="col-md-6">
                <div class="card h-100">
                    <div class="card-header">Uso Promedio (Km) por Año del Vehículo</div>
                    <div class="card-body">
                        <div class="chart-container">
                            <canvas id="usageChart"></canvas>
                        </div>
                    </div>
                </div>
            </div>
        </div>

    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script>
        // Data passed from Flask
        const inventoryData = {{ inventory_stats | tojson }};
        const priceStats = {{ price_stats | tojson }};
        const yearStats = {{ year_stats | tojson }};
        const locationStats = {{ location_stats | tojson }};
        const dailyPriceStats = {{ daily_price_stats | tojson }};
        const priceDist = {{ price_dist | tojson }};
        const exchangeRate = {{ exchange_rate_val }};

        // Helper for colors
        const colors = ['#4a6fa5', '#6d8bc7', '#92a8d1', '#b6c5e3', '#dbe2f5', '#ff6b6b', '#ffd93d', '#6bcb77', '#ff9f43', '#54a0ff'];

        // --- Helper for Currency Normalization ---
        function normalizePrice(price, currency) {
            if (currency === 'USD') return price;
            if (currency === 'ARS' && exchangeRate > 0) return price / exchangeRate;
            return null; // Ignore if cannot convert
        }

        // --- 1. Inventory Chart ---
        const invLabels = inventoryData.map(d => d._id);
        const invCounts = inventoryData.map(d => d.count);

        new Chart(document.getElementById('inventoryChart'), {
            type: 'doughnut',
            data: {
                labels: invLabels,
                datasets: [{
                    data: invCounts,
                    backgroundColor: colors,
                }]
            },
            options: { maintainAspectRatio: false }
        });

        // --- 2. Location Chart ---
        const locLabels = locationStats.map(d => d._id);
        const locCounts = locationStats.map(d => d.count);

        new Chart(document.getElementById('locationChart'), {
            type: 'bar',
            data: {
                labels: locLabels,
                datasets: [{
                    label: 'Publicaciones',
                    data: locCounts,
                    backgroundColor: '#6d8bc7'
                }]
            },
            options: { maintainAspectRatio: false, indexAxis: 'y' }
        });

        // --- 3. Price Distribution Chart ---
        new Chart(document.getElementById('priceDistChart'), {
            type: 'bar',
            data: {
                labels: priceDist.labels,
                datasets: [{
                    label: 'Frecuencia',
                    data: priceDist.data,
                    backgroundColor: '#ff9f43'
                }]
            },
            options: { maintainAspectRatio: false }
        });

        // --- 4. Evolution Chart ---
        const dates = [...new Set(dailyPriceStats.map(d => d._id.date))].sort();
        const evolPrices = dates.map(d => {
             const entries = dailyPriceStats.filter(stat => stat._id.date === d);
             let total = 0;
             let count = 0;
             entries.forEach(e => {
                 let val = normalizePrice(e.avg_price, e._id.currency);
                 if (val !== null) {
                     total += val * e.count;
                     count += e.count;
                 }
             });
             return count > 0 ? (total / count) : null;
        });

        new Chart(document.getElementById('evolutionChart'), {
            type: 'line',
            data: {
                labels: dates,
                datasets: [{
                    label: 'Precio Promedio Diario (USD)',
                    data: evolPrices,
                    borderColor: '#54a0ff',
                    backgroundColor: 'rgba(84, 160, 255, 0.2)',
                    fill: true,
                    tension: 0.1
                }]
            },
            options: { maintainAspectRatio: false }
        });

        // --- 5. Average Price by Model ---
        const terms = [...new Set(priceStats.map(d => d._id.term))];
        const avgPrices = terms.map(term => {
            // Find all entries for this term
            const entries = priceStats.filter(d => d._id.term === term);
            let totalVal = 0;
            let count = 0;
            entries.forEach(e => {
                let val = normalizePrice(e.avg_price, e._id.currency);
                // If no exchange rate, only take USD
                if (val !== null) {
                    totalVal += val;
                    count++;
                } else if (exchangeRate === 0 && e._id.currency === 'USD') {
                     totalVal += e.avg_price;
                     count++;
                }
            });
            return count > 0 ? (totalVal / count) : 0;
        });

        new Chart(document.getElementById('priceChart'), {
            type: 'bar',
            data: {
                labels: terms,
                datasets: [{
                    label: 'Precio Promedio (USD)',
                    data: avgPrices,
                    backgroundColor: '#4a6fa5'
                }]
            },
            options: { maintainAspectRatio: false }
        });

        // --- 6 & 7 Year Stats (Depreciation & Usage) ---
        // Group by Year
        const years = [...new Set(yearStats.map(d => d._id.year))].sort((a,b) => a - b);

        const yearPrices = years.map(y => {
            const entries = yearStats.filter(d => d._id.year === y);
            let totalVal = 0;
            let count = 0;
            entries.forEach(e => {
                let val = normalizePrice(e.avg_price, e._id.currency);
                if (val !== null) {
                    totalVal += val * e.count; // Weighted average
                    count += e.count;
                } else if (exchangeRate === 0 && e._id.currency === 'USD') {
                    totalVal += e.avg_price * e.count;
                    count += e.count;
                }
            });
            return count > 0 ? (totalVal / count) : null;
        });

        const yearKms = years.map(y => {
            const entries = yearStats.filter(d => d._id.year === y);
            let totalKm = 0;
            let count = 0;
            entries.forEach(e => {
                totalKm += e.avg_km * e.count;
                count += e.count;
            });
            return count > 0 ? (totalKm / count) : 0;
        });

        new Chart(document.getElementById('depreciationChart'), {
            type: 'line',
            data: {
                labels: years,
                datasets: [{
                    label: 'Precio Promedio (USD)',
                    data: yearPrices,
                    borderColor: '#ff6b6b',
                    tension: 0.1
                }]
            },
            options: { maintainAspectRatio: false }
        });

        new Chart(document.getElementById('usageChart'), {
            type: 'bar',
            data: {
                labels: years,
                datasets: [{
                    label: 'Km Promedio',
                    data: yearKms,
                    backgroundColor: '#6bcb77'
                }]
            },
            options: { maintainAspectRatio: false }
        });

    </script>
    </body>
    </html>
    ''', inventory_stats=inventory_stats, price_stats=price_stats, year_stats=year_stats, location_stats=location_stats, daily_price_stats=daily_price_stats, price_dist=price_dist, exchange_rate=exchange_rate, exchange_rate_val=exchange_rate_val, search_term=search_term)

@app.route('/download/<filename>')
def download(filename):
    return send_file(filename, as_attachment=True)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=port, debug=debug, use_reloader=False)

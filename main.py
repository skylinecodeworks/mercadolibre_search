from flask import Flask, render_template_string, request, send_file, jsonify
import requests
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

if mongo_user and mongo_password:
    mongo_uri = f"mongodb://{quote_plus(mongo_user)}:{quote_plus(mongo_password)}@{mongo_host}:{mongo_port}/{mongo_db_name}?authSource={mongo_auth_source}"
else:
    mongo_uri = f"mongodb://{mongo_host}:{mongo_port}/"

final_mongo_uri = os.getenv("MONGO_URI", mongo_uri)
# Log the URI with masked password for debugging
masked_uri = re.sub(r':([^@]+)@', ':****@', final_mongo_uri)
print(f"Connecting to MongoDB: {masked_uri}")

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



def scrape_mercado_libre(search_term):
    base_url = "https://listado.mercadolibre.com.ar/"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    all_items = []
    page = 1
    while True:
        url = f"{base_url}{search_term.replace(' ', '-')}_Desde_{(page - 1) * 48 + 1}"
        print(f"Scraping page {page}: {url}")
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 404:
                print(f"No more pages available (404 error)")
                break
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            no_results = soup.find('p', class_='ui-search-sidebar__no-results-message')
            if no_results:
                print(f"No results message detected: {no_results.text.strip()}")
                break
            items = soup.find_all('div', class_='ui-search-result__wrapper')
            if not items:
                print("No items found in page")
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
                    price = f"US${price_elem.text.strip()}" if price_elem and price_elem.text else 'N/A'
                    details = item.find_all('li', class_='poly-attributes_list__item')
                    year = details[0].text.strip() if len(details) > 0 and details[0].text else 'N/A'
                    km = details[1].text.strip() if len(details) > 1 and details[1].text else 'N/A'
                    location_elem = item.find('span', class_='poly-component__location')
                    location = location_elem.text.strip() if location_elem and location_elem.text else 'N/A'
                    price_num = int(price.replace('US$', '').replace('.', '').strip()) if price != 'N/A' else 0
                    year_num = int(year) if year != 'N/A' else 0
                    km_num = int(km.replace('Km', '').replace('.', '').strip()) if km != 'N/A' else 0
                    all_items.append({
                        'unique_id': unique_id,
                        'image': picture_url,
                        'description': title,
                        'price': price,
                        'price_num': price_num,
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

@app.route('/', methods=['GET', 'POST'])
def index():
    sort = request.args.get('sort', '')
    order = request.args.get('order', 'asc')
    search_terms = sorted(set(doc['search_term'] for doc in cars_collection.find({}, {'search_term': 1})))
    search_term = ""
    if request.method == 'POST':
        search_term = request.form.get('search_term') or request.form.get('dropdown_search_term') or ""
        action = request.form.get('action', 'scrape')
        web_logger.logs = []  # Clear previous logs

        if action == 'history':
            df = get_historical_data(search_term)
        else:
            df = scrape_mercado_libre(search_term)

        variation_list = []
        for _, row in df.iterrows():
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
        df['variación'] = variation_list
        if 'image' not in df.columns:
            df['image'] = ""
        # Orden de columnas
        cols = ['image'] + [col for col in df.columns if col not in ['image', 'variación']] + ['variación']
        df = df[cols]
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
                        <div class="col-md-5">
                            <label for="searchInput" class="form-label">Término a buscar</label>
                            <input type="text" name="search_term" id="searchInput" class="form-control" placeholder="Ejemplo: BMW X3" value="{{ search_term }}">
                        </div>
                        <div class="col-md-5">
                            <label for="dropdown_search_term" class="form-label">Búsquedas anteriores</label>
                            <select name="dropdown_search_term" id="dropdown_search_term" class="form-select" onchange="onDropdownChange(this)">
                                <option value="">-- Seleccione búsqueda anterior --</option>
                                {% for term in search_terms %}
                                    <option value="{{ term }}" {% if term == search_term %}selected{% endif %}>{{ term }}</option>
                                {% endfor %}
                            </select>
                        </div>
                        <div class="col-md-2 d-grid gap-2">
                            <button type="submit" name="action" value="scrape" class="btn btn-success fw-semibold">Scrapear</button>
                            <button type="submit" name="action" value="history" class="btn btn-secondary fw-semibold">Ver Histórico</button>
                        </div>
                    </form>
                </div>
            </div>

            <div class="bg-white rounded p-3 shadow-sm mb-4">
                <h2 class="mb-3 h4">Resultados <span class="text-secondary small">({{ df|length }} ítems)</span></h2>
                <div class="table-responsive">
                    <table id="resultsTable" class="table table-striped table-hover align-middle">
                        <thead>
                            <tr>
                                {% for column in df.columns %}
                                    {% if column != "link" %}
                                        <th>{{ column.capitalize() }}</th>
                                    {% endif %}
                                {% endfor %}
                                <th>Enlace</th>
                                <th>Evolución</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for _, row in df.iterrows() %}
                            <tr>
                                {% for col in df.columns %}
                                    {% if col == "image" %}
                                        <td>
                                            {% if row.image %}
                                                <img src="{{ row.image }}" class="product-thumb" loading="lazy">
                                            {% else %}
                                                <span class="text-secondary">N/A</span>
                                            {% endif %}
                                        </td>
                                    {% elif col == "link" %}
                                        <!-- la columna 'link' va como botón fuera -->
                                    {% else %}
                                        <td>{{ row[col] if row[col] is not none else '' }}</td>
                                    {% endif %}
                                {% endfor %}
                                <td>
                                    {% if 'link' in row %}
                                    <a href="{{ row.link }}" class="btn btn-outline-primary btn-sm fw-semibold" target="_blank">
                                        Ver producto
                                    </a>
                                    {% else %}
                                    <span class="text-secondary">N/A</span>
                                    {% endif %}
                                </td>
                                <td>
                                    <button type="button" class="btn btn-outline-secondary btn-sm evol-btn show-history"
                                            data-uniqueid="{{ row.unique_id }}"
                                            data-searchterm="{{ row.search_term }}">
                                        Ver evolución
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
            $('#resultsTable').DataTable({
                paging: false,
                info: false,
                language: {search: "Buscar:", zeroRecords: "No se encontraron registros"}
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
        ''', logs=web_logger.logs, df=df, search_terms=search_terms, search_term=search_term)

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
                <button type="submit" name="action" value="scrape">Scrape</button>
                <button type="submit" name="action" value="history">Ver Histórico</button>
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

@app.route('/download/<filename>')
def download(filename):
    return send_file(filename, as_attachment=True)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=port, debug=debug, use_reloader=False)

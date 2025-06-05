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

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

mongo_client = MongoClient("mongodb://localhost:27017/")
mongo_db = mongo_client["ml"]
cars_collection = mongo_db["cars"]

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

@app.route('/', methods=['GET', 'POST'])
def index():
    sort = request.args.get('sort', '')
    order = request.args.get('order', 'asc')
    search_terms = sorted(set(doc['search_term'] for doc in cars_collection.find({}, {'search_term': 1})))
    search_term = ""
    if request.method == 'POST':
        search_term = request.form.get('search_term') or request.form.get('dropdown_search_term') or ""
        web_logger.logs = []  # Clear previous logs
        df = scrape_mercado_libre(search_term)
        variation_list = []
        for _, row in df.iterrows():
            prev_doc = cars_collection.find_one({
                'unique_id': row['unique_id'],
                'search_term': row['search_term'],
                'date_str': {'$lt': datetime.utcnow().strftime('%Y-%m-%d')}
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
        csv_filename = f"mercado_libre_{search_term.replace(' ', '_')}.csv"
        df.to_csv(csv_filename, index=False)
        return render_template_string('''
            <!DOCTYPE html>
            <html>
            <head>
                <title>Mercado Libre Scraper</title>
                <style>
                    th a { text-decoration: none; color: inherit; }
                    th a:hover { text-decoration: underline; }
                    td img.product-thumb { width:100px; height:100px; object-fit:cover; border-radius:8px;}
                </style>
            </head>
            <body>
                <h1>Mercado Libre Scraper</h1>
                <form method="POST" id="searchForm">
                    <input type="text" name="search_term" id="searchInput" placeholder="Enter search term">
                    <select name="dropdown_search_term" id="dropdown_search_term" onchange="onDropdownChange(this)">
                        <option value="">-- Seleccione búsqueda anterior --</option>
                        {% for term in search_terms %}
                            <option value="{{ term }}" {% if term == search_term %}selected{% endif %}>{{ term }}</option>
                        {% endfor %}
                    </select>
                    <button type="submit">Scrape</button>
                </form>
                <script>
                function onDropdownChange(sel) {
                    if(sel.value) {
                        document.getElementById('searchInput').value = '';
                        document.getElementById('searchForm').submit();
                    }
                }
                </script>
                <h2>Resultados ({{ df|length }} ítems)</h2>
                <div class="table-container">
                    <table id="resultsTable" class="display">
                        <thead>
                            <tr>
                                {% for column in df.columns %}
                                    {% if column != "link" %}
                                        <th>{{ column }}</th>
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
                                                <span class="no-link">N/A</span>
                                            {% endif %}
                                        </td>
                                    {% elif col == "link" %}
                                        <!-- no renderizamos link aquí porque va en la columna 'Enlace' -->
                                    {% else %}
                                        <td>{{ row[col] if row[col] is not none else '' }}</td>
                                    {% endif %}
                                {% endfor %}
                                <td>
                                    {% if 'link' in row %}
                                    <a href="{{ row.link }}" class="poly-component__title" target="_blank">Ver producto</a>
                                    {% else %}
                                    <span class="no-link">N/A</span>
                                    {% endif %}
                                </td>
                                <td>
                                    <button class="show-history"
                                        data-uniqueid="{{ row.unique_id }}"
                                        data-searchterm="{{ row.search_term }}">
                                        Ver evolución
                                    </button>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                    <script type="text/javascript" src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
                    <script type="text/javascript" src="https://cdn.datatables.net/1.11.5/js/jquery.dataTables.js"></script>
                    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
                </div>
                <!-- Modal overlay para el diagrama -->
                <div id="chartModal" class="modal-overlay" style="display:none;">
                    <div class="modal-content">
                        <span class="close-btn" id="closeChartModal">&times;</span>
                        <h2>Evolución histórica del precio promedio</h2>
                        <canvas id="priceChart" width="600" height="300"></canvas>
                    </div>
                </div>
                <style>
                .modal-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: rgba(34, 34, 34, 0.93); z-index: 9999; display: flex; align-items: center; justify-content: center; }
                .modal-content { background: #23272f; border-radius: 16px; box-shadow: 0 8px 32px rgba(0,0,0,0.3); padding: 32px 32px 16px 32px; min-width: 340px; min-height: 200px; max-width: 700px; position: relative; color: #e0e0e0; text-align: center; }
                .close-btn { position: absolute; top: 8px; right: 16px; font-size: 26px; color: #aaa; cursor: pointer; background: none; border: none; z-index: 10000; transition: color 0.18s; }
                .close-btn:hover { color: #fff; }
                </style>
                <script>
                    $(document).ready(function() {
                        $('#resultsTable').DataTable({
                            "paging": false,
                            "info": false,
                            "language": { "search": "Buscar:", "zeroRecords": "No se encontraron registros" }
                        });
                    });

                    let chart = null;
                    function updateChart(history) {
                        const labels = history.map(point => point.date);
                        const data = history.map(point => point.avg_price);
                        if(chart) { chart.destroy(); }
                        chart = new Chart(document.getElementById('priceChart').getContext('2d'), {
                            type: 'line',
                            data: { labels: labels, datasets: [{ label: 'Precio promedio (USD)', data: data, fill: false, borderColor: 'rgb(75, 192, 192)', tension: 0.1 }] },
                            options: { responsive: false, maintainAspectRatio: false, scales: { x: { display: true, title: { display: true, text: 'Fecha' }}, y: { display: true, title: { display: true, text: 'Precio USD'}}}}
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
                            $('#chartModal').fadeIn(160);
                            document.body.style.overflow = 'hidden';
                        });
                    });
                    $('#closeChartModal').on('click', function() {
                        $('#chartModal').fadeOut(120);
                        document.body.style.overflow = '';
                    });
                    $(document).on('keydown', function(e) {
                        if (e.key === 'Escape') {
                            $('#chartModal').fadeOut(120);
                            document.body.style.overflow = '';
                        }
                    });
                    {% if logs %}
                        {% for log in logs %}
                            console.log("[SCRAPER]", `{{ log|e }}`);
                        {% endfor %}
                    {% endif %}
                </script>
                <p><a href="/download/{{ csv_filename }}">Descargar CSV</a></p>
            </body>
            </html>
        ''', logs=web_logger.logs, df=df, csv_filename=csv_filename, search_terms=search_terms, search_term=search_term)

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
                <button type="submit">Scrape</button>
            </form>
            <script>
            function onDropdownChange(sel) {
                if(sel.value) {
                    document.getElementById('searchInput').value = '';
                    document.getElementById('searchForm').submit();
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
    app.run(host='0.0.0.0', port=52021, debug=False, use_reloader=False)

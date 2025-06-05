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
from collections import defaultdict

app = Flask(__name__)

# Configure logging
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

# MongoDB connection (no auth)
mongo_client = MongoClient("mongodb://localhost:27017/")
mongo_db = mongo_client["ml"]
cars_collection = mongo_db["cars"]


def scrape_mercado_libre(search_term):
    base_url = "https://listado.mercadolibre.com.ar/"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

    all_items = []
    page = 1
    # Eliminado el límite de páginas (max_pages)

    while True:
        url = f"{base_url}{search_term.replace(' ', '-')}_Desde_{(page - 1) * 48 + 1}"
        print(f"Scraping page {page}: {url}")

        try:
            response = requests.get(url, headers=headers)

            # Verificar si la página no existe (404)
            if response.status_code == 404:
                print(f"No more pages available (404 error)")
                break

            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Verificar página vacía mediante mensaje de "no resultados"
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
                    print(f"Link: {link}")

                    price_elem = item.find('span', class_='andes-money-amount__fraction')
                    price = f"US${price_elem.text.strip()}" if price_elem and price_elem.text else 'N/A'

                    details = item.find_all('li', class_='poly-attributes_list__item')
                    year = details[0].text.strip() if len(details) > 0 and details[0].text else 'N/A'
                    km = details[1].text.strip() if len(details) > 1 and details[1].text else 'N/A'

                    location_elem = item.find('span', class_='poly-component__location')
                    location = location_elem.text.strip() if location_elem and location_elem.text else 'N/A'

                    # Convertir datos para ordenamiento/filtrado
                    price_num = int(price.replace('US$', '').replace('.', '').strip()) if price != 'N/A' else 0
                    year_num = int(year) if year != 'N/A' else 0
                    km_num = int(km.replace('Km', '').replace('.', '').strip()) if km != 'N/A' else 0

                    all_items.append({
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
                    print(f"Added item: {title[:50]}...")
                except Exception as e:
                    print(f"Error processing item: {e}")
                    continue

            page += 1
            time.sleep(2)

        except Exception as e:
            print(f"Error scraping page {page}: {e}")
            break

    df = pd.DataFrame(all_items)

    # Guardar resultados en MongoDB con timestamp, término de búsqueda y control por día
    if not df.empty:
        records = df.to_dict(orient='records')
        timestamp = datetime.utcnow()
        today_str = timestamp.strftime('%Y-%m-%d')
        for rec in records:
            rec['timestamp'] = timestamp
            rec['search_term'] = search_term
            rec['date_str'] = today_str  # campo auxiliar para control diario
            filter_query = {
                'search_term': search_term,
                'description': rec['description'],
                'date_str': today_str
            }
            cars_collection.replace_one(filter_query, rec, upsert=True)
    return df


@app.route('/', methods=['GET', 'POST'])
def index():
    sort = request.args.get('sort', '')
    order = request.args.get('order', 'asc')

    if request.method == 'POST':
        search_term = request.form['search_term']
        web_logger.logs = []  # Clear previous logs
        df = scrape_mercado_libre(search_term)
        # Añadir columna de variación de precio
        variation_list = []
        for _, row in df.iterrows():
            # Buscar último precio anterior (día < hoy)
            prev_doc = cars_collection.find_one({
                'search_term': row['search_term'],
                'description': row['description'],
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

        csv_filename = f"mercado_libre_{search_term.replace(' ', '_')}.csv"
        df.to_csv(csv_filename, index=False)

        # Histórico de precios promedio inicial (opcional: mostrar de primero)
        history_points = []

        # Apply sorting if requested
        if sort and sort in df.columns:
            df = df.sort_values(by=sort, ascending=(order == 'asc'))

        return render_template_string('''
            <!DOCTYPE html>
            <html>
            <head>
                <title>Mercado Libre Scraper</title>
                <style>
                    th a { text-decoration: none; color: inherit; }
                    th a:hover { text-decoration: underline; }
                </style>
                <style>
                    body { font-family: Arial, sans-serif; margin: 20px; }
                    .logs { background: #f5f5f5; padding: 10px; border-radius: 5px; height: 300px; overflow-y: scroll; }
                    table { width: 100%; border-collapse: collapse; margin-top: 20px; }
                    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
                    th { background-color: #f2f2f2; }
                </style>
            </head>
            <body>
                <h1>Mercado Libre Scraper</h1>
                <form method="POST">
                    <input type="text" name="search_term" placeholder="Enter search term" required>
                    <button type="submit">Scrape</button>
                </form>
                <h2>Logs</h2>
                <div class="logs">
                    {% for log in logs %}
                        <div>{{ log }}</div>
                    {% endfor %}
                </div>
                <h2>Resultados ({{ df|length }} ítems)</h2>
                <p>Columnas: {{ df.columns.tolist() }}</p>
                <div class="table-container">
                    <table id="resultsTable" class="display">
                        <thead>
                            <tr>
                                {% for column in df.columns %}
                                <th>{{ column }}</th>
                                {% endfor %}
                                <th>Enlace</th>
                                <th>Evolución</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for _, row in df.iterrows() %}
                            <tr>
                                {% for value in row %}
                                <td>{{ value if value is not none else '' }}</td>
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
                                        data-description="{{ row.description }}"
                                        data-searchterm="{{ row.search_term }}">
                                        Ver evolución
                                    </button>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                    <!-- DataTables JS -->
                    <script type="text/javascript" src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
                    <script type="text/javascript" src="https://cdn.datatables.net/1.11.5/js/jquery.dataTables.js"></script>
                    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
                    <script>
                        $(document).ready(function() {
                            $('#resultsTable').DataTable({
                                "paging": false,
                                "info": false,
                                "language": {
                                    "search": "Buscar:",
                                    "zeroRecords": "No se encontraron registros"
                                }
                            });
                        });

                        let chart = null;
                        function updateChart(history) {
                            const labels = history.map(point => point.date);
                            const data = history.map(point => point.avg_price);
                            if (!chart) {
                                chart = new Chart(document.getElementById('priceChart').getContext('2d'), {
                                    type: 'line',
                                    data: {
                                        labels: labels,
                                        datasets: [{
                                            label: 'Precio promedio (USD)',
                                            data: data,
                                            fill: false,
                                            borderColor: 'rgb(75, 192, 192)',
                                            tension: 0.1
                                        }]
                                    },
                                    options: {
                                        scales: {
                                            x: { display: true, title: { display: true, text: 'Fecha' }},
                                            y: { display: true, title: { display: true, text: 'Precio USD'}}
                                        }
                                    }
                                });
                            } else {
                                chart.data.labels = labels;
                                chart.data.datasets[0].data = data;
                                chart.update();
                            }
                        }
                        $(document).on('click', '.show-history', function() {
                            const description = $(this).data('description');
                            const search_term = $(this).data('searchterm');
                            fetch('/history', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({description, search_term})
                            })
                            .then(response => response.json())
                            .then(result => {
                                updateChart(result.history);
                            });
                        });
                    </script>
                </div>
                <p><a href="/download/{{ csv_filename }}">Descargar CSV</a></p>
                <h2>Evolución histórica del precio promedio</h2>
                <canvas id="priceChart"></canvas>
            </body>
            </html>
        ''', logs=web_logger.logs, df=df, csv_filename=csv_filename)

    return render_template_string('''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Mercado Libre Scraper</title>
            <link rel="stylesheet" type="text/css" href="https://cdn.datatables.net/1.11.5/css/jquery.dataTables.min.css">
            <link rel="stylesheet" type="text/css" href="https://cdn.datatables.net/buttons/2.2.2/css/buttons.dataTables.min.css">
<style>
    :root {
        --bg-dark: #1a1a1a;
        --text-light: #e0e0e0;
        --primary-accent: #4a6fa5;
        --secondary-accent: #6d8bc7;
        --table-border: #3a3a3a;
    }

    body {
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        margin: 20px;
        background-color: var(--bg-dark);
        color: var(--text-light);
    }

    .logs {
        background: #2a2a2a;
        color: var(--text-light);
        padding: 15px;
        border-radius: 8px;
        height: 300px;
        overflow-y: scroll;
        border: 1px solid var(--table-border);
    }

    table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 20px;
        background-color: #2a2a2a;
    }

    th, td {
        border: 1px solid var(--table-border);
        padding: 12px;
        text-align: left;
    }

    th {
        background-color: #333;
        color: var(--text-light);
        cursor: pointer;
    }

    tr:nth-child(even) {
        background-color: #2f2f2f;
    }

    tr:hover {
        background-color: #3a3a3a;
    }

    a {
        color: var(--secondary-accent);
        text-decoration: none;
    }

    a:hover {
        color: var(--primary-accent);
        text-decoration: underline;
    }

    .no-link {
        color: #888;
    }

    h1, h2, h3 {
        color: var(--primary-accent);
    }
</style>
        </head>
        <body>
            <h1>Mercado Libre Scraper</h1>
            <form method="POST">
                <input type="text" name="search_term" placeholder="Enter search term" required>
                <button type="submit">Scrape</button>
            </form>
            <h2>Logs</h2>
            <div class="logs">
                {% for log in logs %}
                    <div>{{ log }}</div>
                {% endfor %}
            </div>
        </body>
        </html>
    ''', logs=web_logger.logs)


@app.route('/history', methods=['POST'])
def history():
    data = request.json
    description = data['description']
    search_term = data['search_term']
    docs = cars_collection.find({
        'search_term': search_term,
        'description': description
    })
    history_points = {}
    for doc in docs:
        date = doc['timestamp'].strftime('%Y-%m-%d') if hasattr(doc['timestamp'], 'strftime') else str(doc['timestamp'])
        price = doc.get('price_num', 0)
        if price:
            history_points.setdefault(date, []).append(price)
    history_list = [
        {'date': date, 'avg_price': sum(prices) // len(prices)}
        for date, prices in sorted(history_points.items())
    ]
    return jsonify({'history': history_list})


@app.route('/download/<filename>')
def download(filename):
    return send_file(filename, as_attachment=True)


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=52021, debug=False, use_reloader=False)

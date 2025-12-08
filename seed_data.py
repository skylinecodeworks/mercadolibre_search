from datetime import datetime

SAMPLE_CARS = [
    {
        'unique_id': 'MLA123456',
        'image': 'https://http2.mlstatic.com/D_NQ_NP_888888-MLA88888888888_122022-W.jpg',
        'description': 'Toyota Corolla 1.8 Xei Pack Cvt',
        'price': 'US$22.000',
        'price_num': 22000,
        'year': '2019',
        'year_num': 2019,
        'kilometers': '45.000 Km',
        'kilometers_num': 45000,
        'location': 'Palermo, Capital Federal',
        'link': 'https://auto.mercadolibre.com.ar/MLA-123456-toyota-corolla',
        'search_term': 'Toyota Corolla',
    },
    {
        'unique_id': 'MLA654321',
        'image': 'https://http2.mlstatic.com/D_NQ_NP_999999-MLA99999999999_122022-W.jpg',
        'description': 'Ford Ranger 3.2 Limited 4x4 At',
        'price': 'US$35.000',
        'price_num': 35000,
        'year': '2021',
        'year_num': 2021,
        'kilometers': '20.000 Km',
        'kilometers_num': 20000,
        'location': 'Córdoba, Córdoba',
        'link': 'https://auto.mercadolibre.com.ar/MLA-654321-ford-ranger',
        'search_term': 'Ford Ranger',
    },
    {
        'unique_id': 'MLA789012',
        'image': 'https://http2.mlstatic.com/D_NQ_NP_777777-MLA77777777777_122022-W.jpg',
        'description': 'Volkswagen Golf 1.4 Tsi Highline',
        'price': 'US$24.500',
        'price_num': 24500,
        'year': '2018',
        'year_num': 2018,
        'kilometers': '55.000 Km',
        'kilometers_num': 55000,
        'location': 'San Isidro, Gba Norte',
        'link': 'https://auto.mercadolibre.com.ar/MLA-789012-volkswagen-golf',
        'search_term': 'Volkswagen Golf',
    }
]

def recreate_database(collection):
    """
    Borra todos los documentos de la colección e inserta datos de ejemplo.
    """
    # Borrar todos los documentos
    collection.delete_many({})

    # Preparar datos con timestamp actual
    timestamp = datetime.utcnow()
    today_str = timestamp.strftime('%Y-%m-%d')

    records_to_insert = []
    for car in SAMPLE_CARS:
        record = car.copy()
        record['timestamp'] = timestamp
        record['date_str'] = today_str
        records_to_insert.append(record)

    # Insertar nuevos documentos
    if records_to_insert:
        collection.insert_many(records_to_insert)

    return len(records_to_insert)

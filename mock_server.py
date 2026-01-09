import sys
from unittest.mock import MagicMock
import pandas as pd
import os

# Mock pymongo
mock_mongo = MagicMock()
sys.modules['pymongo'] = mock_mongo
mock_client = MagicMock()
mock_mongo.MongoClient.return_value = mock_client
mock_db = MagicMock()
mock_client.__getitem__.return_value = mock_db
mock_collection = MagicMock()
mock_db.__getitem__.return_value = mock_collection

# Mock find to return dummy search terms
mock_collection.find.return_value = [{'search_term': 'BMW X3'}, {'search_term': 'Audi A4'}]
mock_collection.find_one.return_value = None # For variation logic
mock_collection.aggregate.return_value = []
mock_collection.replace_one.return_value = None

# Import main
import main

# Mock scrape_mercado_libre
# We need to ensure we use web_logger.write here too if we want to emulate the new behavior,
# although main.web_logger is available.
def mock_scrape(term):
    main.web_logger.write(f"DEBUG: Mock scraping started for {term}")
    main.web_logger.write("DEBUG: Status Code: 200, Content Length: 1234")
    main.web_logger.write("DEBUG: Found 1 items with class 'ui-search-result__wrapper'")
    main.web_logger.write("DEBUG: Added item: Test Car... (ID: 123)")
    return pd.DataFrame([
        {
            'unique_id': '123',
            'image': '',
            'description': 'Test Car',
            'price': 'US$ 10,000',
            'price_num': 10000,
            'currency': 'USD',
            'year': '2020',
            'year_num': 2020,
            'kilometers': '1000 Km',
            'kilometers_num': 1000,
            'location': 'Test Location',
            'link': '#',
            'search_term': term,
            'date_str': '2023-01-01'
        }
    ])

main.scrape_mercado_libre = mock_scrape

# Run app
if __name__ == '__main__':
    print("Starting mocked server on 52021...")
    main.app.run(host='0.0.0.0', port=52021)

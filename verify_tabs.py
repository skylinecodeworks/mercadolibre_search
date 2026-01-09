import time
import sys
from playwright.sync_api import sync_playwright, expect
import re

def verify_tabs():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Navigate to the app
        print("Navigating to app...")
        page.goto("http://localhost:52021")

        # Check title
        expect(page).to_have_title("Mercado Libre Scraper")
        print("Title verified.")

        # Trigger a search to get to the POST view (Results View)
        # The tabs are ONLY in the Results View
        print("Triggering search...")
        page.fill("input[name='search_term']", "Test Car")
        page.click("button[value='scrape']")

        # Wait for navigation / load
        page.wait_for_load_state('networkidle')

        # Now Check Tabs exist
        results_tab = page.get_by_role("tab", name="Resultados")
        logs_tab = page.get_by_role("tab", name="Logs del Scraper")

        expect(results_tab).to_be_visible()
        expect(logs_tab).to_be_visible()
        print("Tabs found.")

        # Check Results Tab is active by default
        # Using regex to check for 'active' class
        expect(results_tab).to_have_class(re.compile(r"active"))
        print("Results tab is active.")

        # Take screenshot of Results View
        page.screenshot(path="/home/jules/verification/results_view.png")
        print("Screenshot results_view.png taken.")

        # Click Logs Tab
        logs_tab.click()

        # Check Logs Tab is active
        expect(logs_tab).to_have_class(re.compile(r"active"))
        print("Logs tab is active after click.")

        # Check Log Content Visibility
        # We look for "Logs Técnicos" header in the logs pane
        logs_header = page.get_by_text("Logs Técnicos")
        expect(logs_header).to_be_visible()

        # Verify specific logs exist
        logs_content = page.locator("#logs-tab-pane pre")
        expect(logs_content).to_contain_text("DEBUG: Mock scraping started")
        expect(logs_content).to_contain_text("DEBUG: Found 1 items")
        print("Log content verified.")

        # Take screenshot of Logs View
        page.screenshot(path="/home/jules/verification/logs_view.png")
        print("Screenshot logs_view.png taken.")

        browser.close()

if __name__ == "__main__":
    # Wait for server to start
    time.sleep(5)
    try:
        verify_tabs()
    except Exception as e:
        print(f"Verification failed: {e}")
        sys.exit(1)

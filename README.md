# Kleinanzeigen Order Tool

Kleines internes Flask-Tool für ChiefCards.

Zweck:
- Kundendaten aus Kleinanzeigen eintragen
- Shopify-Produkte/Varianten suchen
- Artikel und Versandkosten auswählen
- Shopify Draft Order erstellen

Wichtige Scopes:
- read_products
- read_inventory
- read_locations
- read_customers
- write_customers
- read_draft_orders
- write_draft_orders

Start lokal:
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
./venv/bin/python app.py

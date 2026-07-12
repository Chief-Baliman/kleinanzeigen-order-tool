# Kleinanzeigen Order Tool v1.1

Erstellt Shopify Draft Orders aus reinkopierten Kleinanzeigen-Daten.

## Shopify Zugang

Das Tool unterstützt zwei Varianten:

1. `SHOPIFY_TOKEN`, falls Shopify dir einen klassischen Admin API Access Token zeigt.
2. `SHOPIFY_CLIENT_ID` + `SHOPIFY_CLIENT_SECRET`, wenn die App im neuen Shopify Dev Dashboard erstellt wurde. Dann holt das Tool den Admin API Access Token automatisch per Client-Credentials-Flow.

Benötigte Scopes:

`read_products,read_inventory,read_locations,read_customers,write_customers,read_draft_orders,write_draft_orders`

## Server

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
./venv/bin/python app.py
```

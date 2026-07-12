# Kleinanzeigen Order Tool v1.2

Kleines Server-Tool, um aus Kleinanzeigen-Kontaktdaten Shopify Draft Orders zu erstellen.

## v1.2

Fix: Der Status-Check für Shopify-Scopes nutzt jetzt den richtigen Endpoint `/admin/oauth/access_scopes.json` statt `/admin/api/<version>/oauth/access_scopes.json`.

## Env

```env
SHOPIFY_SHOP=20980d-fd.myshopify.com
SHOPIFY_TOKEN=
SHOPIFY_CLIENT_ID=DEINE_CLIENT_ID
SHOPIFY_CLIENT_SECRET=DEIN_SCHLUESSEL
SHOPIFY_API_VERSION=2026-04
DASH_USER=admin
DASH_PASS=KleinTool!2026
SECRET_KEY=BitteAendern
DEFAULT_SHIPPING=5.99
PORT=8789
```

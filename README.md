# Shopify Kleinanzeigen Tool

Interne Web-App zum Anlegen von Shopify-Bestellentwürfen aus Kleinanzeigen-Verkäufen.

## Aufbau

Die App läuft auf einem Ubuntu-Server. GitHub ist optional und wird nur zur Versionsverwaltung benötigt. Zugangsdaten liegen ausschließlich in der `.env` auf dem Server.

## Shopify vorbereiten, Stand Juli 2026

1. Shopify Admin öffnen: Einstellungen > Apps > Apps entwickeln > Apps im Dev Dashboard erstellen.
2. Eine App mit dem Namen `Kleinanzeigen Bestelltool` erstellen.
3. Eine Version anlegen. Als App-URL kann bei einer nicht eingebetteten App zunächst `https://shopify.dev/apps/default-app-home` verwendet werden.
4. Diese Admin-API-Berechtigungen eintragen:
   - `read_products`
   - `write_draft_orders`
5. Version veröffentlichen und die App im eigenen Shop installieren.
6. Im Dev Dashboard unter Einstellungen die Client-ID und das Client-Secret kopieren.

Das Tool erzeugt mit diesen Daten automatisch einen Shopify-Zugriffstoken und erneuert ihn nach Ablauf.

## Installation auf Ubuntu

ZIP nach `/opt` auf den Server laden und dann ausführen:

```bash
cd /opt
sudo unzip shopify-kleinanzeigen-tool.zip
cd shopify-kleinanzeigen-tool
sudo cp .env.example .env
sudo nano .env
npm install --omit=dev
```

In `.env` eintragen:

```env
PORT=8790
APP_PASSWORD=EIN_EIGENES_SICHERES_PASSWORT
SHOPIFY_STORE=dein-shop.myshopify.com
SHOPIFY_CLIENT_ID=DEINE_CLIENT_ID
SHOPIFY_CLIENT_SECRET=DEIN_CLIENT_SECRET
SHOPIFY_API_VERSION=2026-07
```

## systemd

Datei `/etc/systemd/system/shopify-kleinanzeigen.service` erstellen:

```ini
[Unit]
Description=Shopify Kleinanzeigen Tool
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/shopify-kleinanzeigen-tool
ExecStart=/usr/bin/npm start
Restart=always
RestartSec=5
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
```

Aktivieren:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now shopify-kleinanzeigen.service
sudo systemctl status shopify-kleinanzeigen.service
```

Lokaler Test:

```bash
curl http://127.0.0.1:8790
```

Die App sollte anschließend zunächst unter `http://SERVER-IP:8790` erreichbar sein. Für den dauerhaften Einsatz Nginx mit HTTPS und einer Subdomain verwenden. Port 8790 muss dann nicht öffentlich geöffnet werden.

## Adressen

Deutschland ist standardmäßig ausgewählt. Das Tool übergibt den ISO-Ländercode ausdrücklich an Shopify und setzt Rechnungs- und Lieferadresse identisch. Für andere Länder kann der Ländercode geändert werden.

# Kleinanzeigen Order Tool v2.0

Interne Flask-Anwendung zum Erstellen von Shopify Draft Orders aus Kleinanzeigen-Verkäufen.

## Änderungen in v2.0

- Draft Orders werden über die Shopify GraphQL Admin API erstellt.
- Liefer- und Rechnungsadresse erhalten immer einen ISO-Ländercode. Standard ist `DE`.
- Vor dem Erstellen führt Shopify eine echte Draft-Order-Berechnung durch.
- Deutsche Bestellungen mit `0,00 EUR` Steuer werden standardmäßig blockiert, damit keine fehlerhaften Bestellungen an Lexware weitergegeben werden.
- Benutzerdefinierte Artikel sind ausdrücklich steuerpflichtig.
- Bei Shopify-Varianten wird geprüft, ob die Variante steuerpflichtig ist.
- Der im Tool angezeigte Verkaufspreis wird als `priceOverride` übernommen.
- Steuerbetrag, Tax Lines und Adresscodes werden im lokalen Audit-Payload gespeichert.
- `.env`, Datenbank, virtuelle Umgebung und Cache sind über `.gitignore` geschützt.

## Wichtige Voraussetzung in Shopify

Shopify berechnet die Steuer. Das Tool erfindet keine Steuerzeilen. Daher müssen in Shopify:

1. die deutsche Steuerregistrierung aktiv sein,
2. die verwendeten Produktvarianten als steuerpflichtig markiert sein,
3. die Preise entsprechend den Shop-Einstellungen als Bruttopreise oder Nettopreise gepflegt sein.

`REQUIRE_TAXES=true` verhindert bei deutschen Bestellungen das Erstellen einer Draft Order, wenn Shopify keine Steuer berechnet.

## Benötigte Scopes

- `read_products`
- `write_draft_orders`
- empfohlen: `read_draft_orders`

## Sicheres Deployment

Auf dem Server:

```bash
ssh root@217.154.249.255
cd /opt/kleinanzeigen-order-tool
bash scripts/safe_deploy.sh
```

Das Skript erstellt vor dem Update ein Code-Backup, lädt `main` nur als Fast-Forward, installiert Abhängigkeiten, prüft die Python-Syntax und startet den Dienst. Wenn der Dienst nicht startet, wird automatisch auf den vorherigen Git-Commit zurückgesetzt.

## Manueller Test nach dem Deployment

1. Einen steuerpflichtigen Shopify-Artikel auswählen.
2. Eine deutsche Testadresse eintragen.
3. Draft Order erstellen.
4. In Shopify kontrollieren:
   - Lieferadresse Deutschland
   - Rechnungsadresse Deutschland
   - enthaltener Steuerbetrag größer als 0
   - Steuerzeile am Artikel
   - Steuerzeile am Versand, sofern Shopify den Versand nach seiner Steuerkonfiguration besteuert
5. Erst danach den Lexware-Import testen.

## Lokale Tests

```bash
python -m unittest discover -s tests -v
```

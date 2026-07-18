# Changelog

## 2.0.1

- Behebt ungültige Felder in der Antwortabfrage von `draftOrderCalculate`.
- Landprüfung verwendet jetzt die bereits validierte Eingabeadresse.
- Steuerzeilen und Versandsteuer werden bereits in der Vorberechnung abgefragt.


## 2.0.0

- Migration der Draft-Order-Erstellung von REST zu GraphQL
- ISO-Ländercodes für Liefer- und Rechnungsadresse
- Deutschland als Standardland
- Steuer-Vorprüfung über `draftOrderCalculate`
- Schutz vor deutschen Bestellungen ohne berechnete Steuer
- Prüfung steuerpflichtiger Shopify-Varianten
- Steuerpflichtige benutzerdefinierte Artikel
- Preisüberschreibung für vereinbarte Kleinanzeigen-Preise
- Audit-Daten mit Steuerberechnung und Tax Lines
- Sicheres Deployment-Skript mit Backup und Rollback

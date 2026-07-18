import os
import re
import json
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from functools import wraps

import requests
from dotenv import load_dotenv
from flask import Flask, request, jsonify, session, redirect, Response

load_dotenv()

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, 'orders.db')

SHOPIFY_SHOP = os.getenv('SHOPIFY_SHOP', '').strip()
SHOPIFY_TOKEN = os.getenv('SHOPIFY_TOKEN', '').strip()
SHOPIFY_CLIENT_ID = os.getenv('SHOPIFY_CLIENT_ID', '').strip()
SHOPIFY_CLIENT_SECRET = os.getenv('SHOPIFY_CLIENT_SECRET', '').strip()
_token_cache = {'value': None, 'expires_at': 0}
SHOPIFY_API_VERSION = os.getenv('SHOPIFY_API_VERSION', '2026-04').strip()
DASH_USER = os.getenv('DASH_USER', 'admin')
DASH_PASS = os.getenv('DASH_PASS', '')
DEFAULT_SHIPPING = os.getenv('DEFAULT_SHIPPING', '4.29')
SHOP_CURRENCY = os.getenv('SHOP_CURRENCY', 'EUR').strip().upper() or 'EUR'
DEFAULT_COUNTRY_CODE = os.getenv('DEFAULT_COUNTRY_CODE', 'DE').strip().upper() or 'DE'
REQUIRE_TAXES = os.getenv('REQUIRE_TAXES', 'true').strip().lower() in ('1', 'true', 'yes', 'on')

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'change-me-now')


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS draft_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            shopify_id TEXT,
            shopify_name TEXT,
            admin_url TEXT,
            invoice_url TEXT,
            customer_name TEXT,
            email TEXT,
            phone TEXT,
            total_price TEXT,
            payload TEXT
        )
    ''')
    conn.commit()
    conn.close()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get('logged_in'):
            return fn(*args, **kwargs)
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Nicht eingeloggt'}), 401
        return redirect('/login')
    return wrapper


def get_shopify_token():
    if not SHOPIFY_SHOP:
        raise RuntimeError('SHOPIFY_SHOP fehlt in .env')

    # Klassische Custom-App: fester Admin API Access Token, z. B. shpat_...
    if SHOPIFY_TOKEN:
        return SHOPIFY_TOKEN

    # Neues Shopify Dev Dashboard: Client-Credentials-Flow.
    # Der Access Token ist nur ca. 24h gültig und wird deshalb automatisch erneuert.
    if not SHOPIFY_CLIENT_ID or not SHOPIFY_CLIENT_SECRET:
        raise RuntimeError('Entweder SHOPIFY_TOKEN oder SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET müssen in .env gesetzt sein')

    now = int(time.time())
    if _token_cache.get('value') and _token_cache.get('expires_at', 0) > now + 120:
        return _token_cache['value']

    url = f'https://{SHOPIFY_SHOP}/admin/oauth/access_token'
    res = requests.post(url, data={
        'grant_type': 'client_credentials',
        'client_id': SHOPIFY_CLIENT_ID,
        'client_secret': SHOPIFY_CLIENT_SECRET,
    }, timeout=45)
    if res.status_code >= 400:
        raise RuntimeError(f'Shopify Token-Fehler {res.status_code}: {res.text[:1000]}')
    data = res.json()
    token = data.get('access_token')
    if not token:
        raise RuntimeError(f'Keine access_token Antwort von Shopify: {json.dumps(data, ensure_ascii=False)[:600]}')
    _token_cache['value'] = token
    _token_cache['expires_at'] = now + int(data.get('expires_in') or 86399)
    return token


def shopify_headers():
    return {
        'X-Shopify-Access-Token': get_shopify_token(),
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }


def shopify_rest(path, method='GET', payload=None):
    url = f'https://{SHOPIFY_SHOP}/admin/api/{SHOPIFY_API_VERSION}{path}'
    res = requests.request(method, url, headers=shopify_headers(), json=payload, timeout=45)
    if res.status_code >= 400:
        try:
            detail = res.json()
        except Exception:
            detail = res.text[:1000]
        raise RuntimeError(f'Shopify REST Fehler {res.status_code}: {detail}')
    return res.json()


def shopify_access_scopes():
    # Dieser Endpoint ist bei Shopify nicht unter /admin/api/<version>/ erreichbar.
    # Der alte Status-Check lief deshalb auf /admin/api/<version>/oauth/access_scopes.json
    # und bekam korrekt 404 Not Found.
    url = f'https://{SHOPIFY_SHOP}/admin/oauth/access_scopes.json'
    res = requests.get(url, headers=shopify_headers(), timeout=45)
    if res.status_code >= 400:
        try:
            detail = res.json()
        except Exception:
            detail = res.text[:1000]
        raise RuntimeError(f'Shopify Scope-Check Fehler {res.status_code}: {detail}')
    return res.json()


def shopify_graphql(query, variables=None):
    url = f'https://{SHOPIFY_SHOP}/admin/api/{SHOPIFY_API_VERSION}/graphql.json'
    res = requests.post(url, headers=shopify_headers(), json={'query': query, 'variables': variables or {}}, timeout=45)
    if res.status_code >= 400:
        raise RuntimeError(f'Shopify GraphQL HTTP {res.status_code}: {res.text[:1000]}')
    data = res.json()
    if data.get('errors'):
        raise RuntimeError(f'Shopify GraphQL Fehler: {data["errors"]}')
    return data.get('data') or {}


def gid_to_numeric(value):
    if value is None:
        return None
    text = str(value)
    m = re.search(r'(\d+)$', text)
    return int(m.group(1)) if m else None


def safe_money(value):
    if value is None or str(value).strip() == '':
        return '0.00'
    value = str(value).replace(',', '.').strip()
    try:
        return f'{float(value):.2f}'
    except Exception:
        raise ValueError('Ungültiger Betrag')


def clean_phone(phone):
    return (phone or '').strip()




def parse_contact_text(raw):
    """Parse copied Kleinanzeigen/customer contact text into Shopify customer fields.

    The parser is deliberately heuristic and conservative: it returns parsed fields,
    confidence hints and the raw lines that were used, without inventing missing data.
    """
    text = (raw or '').replace('\r', '\n').replace('\t', ' ')
    text = re.sub(r'\u00a0', ' ', text)
    lines = [re.sub(r'\s+', ' ', l).strip(' ,;') for l in text.split('\n')]
    lines = [l for l in lines if l]
    flat = '\n'.join(lines)

    result = {
        'firstName': '',
        'lastName': '',
        'email': '',
        'phone': '',
        'address1': '',
        'address2': '',
        'zip': '',
        'city': '',
        'country': 'Deutschland',
    }
    evidence = []

    def set_field(key, value, reason):
        value = (value or '').strip(' ,;')
        if value and not result.get(key):
            result[key] = value
            evidence.append({'field': key, 'value': value, 'reason': reason})

    # Labelled values can occur as "Name: Max", "Adresse - ...", etc.
    label_patterns = [
        (r'^(?:vorname|first\s*name)\s*[:=\-]\s*(.+)$', 'firstName'),
        (r'^(?:nachname|last\s*name|familienname)\s*[:=\-]\s*(.+)$', 'lastName'),
        (r'^(?:name|vollständiger\s*name|vollstaendiger\s*name)\s*[:=\-]\s*(.+)$', 'fullName'),
        (r'^(?:e\s*-?\s*mail|mail|email)\s*[:=\-]\s*(.+)$', 'email'),
        (r'^(?:telefon|tel\.?|handy|mobil|phone)\s*[:=\-]\s*(.+)$', 'phone'),
        (r'^(?:straße|strasse|anschrift|adresse|address)\s*[:=\-]\s*(.+)$', 'addressCombined'),
        (r'^(?:adresszusatz|zusatz|address2)\s*[:=\-]\s*(.+)$', 'address2'),
        (r'^(?:plz|postleitzahl)\s*[:=\-]\s*(\d{5}).*$', 'zip'),
        (r'^(?:ort|stadt|city)\s*[:=\-]\s*(.+)$', 'city'),
        (r'^(?:land|country)\s*[:=\-]\s*(.+)$', 'country'),
    ]
    for line in lines:
        for pat, key in label_patterns:
            m = re.match(pat, line, flags=re.I)
            if not m:
                continue
            val = m.group(1).strip()
            if key == 'fullName':
                parts = [x for x in val.split() if x]
                if parts:
                    set_field('firstName', parts[0], 'gelabelter Name')
                    if len(parts) > 1:
                        set_field('lastName', ' '.join(parts[1:]), 'gelabelter Name')
            elif key == 'addressCombined':
                # e.g. Adresse: Musterstraße 1, 41539 Dormagen
                chunks = [c.strip() for c in re.split(r'[,;|]', val) if c.strip()]
                if chunks:
                    set_field('address1', chunks[0], 'gelabelte Adresse')
                    rest = ' '.join(chunks[1:])
                    mzc = re.search(r'\b(\d{5})\s+([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß .\-]{1,70})', rest)
                    if mzc:
                        set_field('zip', mzc.group(1), 'gelabelte Adresse')
                        set_field('city', mzc.group(2).strip(), 'gelabelte Adresse')
            else:
                set_field(key, val, 'gelabelte Angabe')

    # E-mail.
    m = re.search(r'[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}', flat, flags=re.I)
    if m:
        set_field('email', m.group(0), 'E-Mail-Muster')

    # Phone. Prefer a labelled phone line, otherwise any German-looking phone number.
    phone_candidates = []
    for line in lines:
        if re.search(r'(telefon|tel\.?|handy|mobil|phone)', line, re.I):
            phone_candidates.append(line)
    phone_candidates.append(flat)
    for source in phone_candidates:
        m = re.search(r'(?:\+49|0049|0)\s*[1-9][0-9\s()/.\-]{6,}', source)
        if m:
            phone = re.sub(r'\s{2,}', ' ', m.group(0)).strip(' .,/;-')
            set_field('phone', phone, 'Telefonnummer-Muster')
            break

    # Zip and city. Supports "41539 Dormagen" on same line, or zip line followed by city line.
    for i, line in enumerate(lines):
        m = re.search(r'\b(\d{5})\b\s*([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß .\-]{1,70})?$', line)
        if m:
            set_field('zip', m.group(1), 'PLZ-Muster')
            if m.group(2):
                set_field('city', m.group(2).strip(), 'PLZ-Ort-Muster')
            elif i + 1 < len(lines) and re.match(r'^[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß .\-]{1,70}$', lines[i + 1]):
                set_field('city', lines[i + 1], 'Ort nach PLZ')
            break

    # Street line. Common German street suffixes plus house number.
    street_rx = re.compile(r'\b([A-Za-zÄÖÜäöüß0-9 .\-]+?(?:straße|strasse|str\.?|weg|allee|platz|gasse|ring|damm|ufer|chaussee|markt|hof|steig|pfad|kamp|wall|promenade)\s+\d+[A-Za-z]?(?:\s*[-/]\s*\d+[A-Za-z]?)?)\b', re.I)
    for line in lines:
        m = street_rx.search(line)
        if m:
            set_field('address1', m.group(1).strip(), 'Straße-Hausnummer-Muster')
            break

    # Some users write street without typical suffix but with label handled above. Add a conservative fallback.
    if not result['address1']:
        for line in lines:
            if re.search(r'\d+[a-zA-Z]?$', line) and not re.search(r'@|\b\d{5}\b|(?:\+49|0049|0)\s*[1-9][0-9\s()/.\-]{6,}', line):
                words = line.split()
                if len(words) >= 2 and len(line) <= 80:
                    set_field('address1', line, 'Adresszeile mit Hausnummer')
                    break

    # Country.
    if re.search(r'\b(deutschland|germany|de)\b', flat, flags=re.I):
        set_field('country', 'Deutschland', 'Land erkannt')

    # Name. Exclude lines that are clearly not names.
    def looks_like_name(line):
        if len(line) > 60 or len(line) < 3:
            return False
        if re.search(r'@|\b\d{5}\b|\d+[a-zA-Z]?$|(?:\+49|0049|0)\s*[1-9][0-9\s()/.\-]{6,}|:', line):
            return False
        if re.search(r'(hallo|hi|moin|guten|adresse|versand|paket|paypal|danke|gruß|gruss|lg|preis|€|eur|karten|pokemon|pokémon|booster)', line, re.I):
            return False
        words = [w for w in line.split() if w]
        return 2 <= len(words) <= 4 and all(re.match(r'^[A-Za-zÄÖÜäöüß\-]+$', w) for w in words)

    if not result['firstName'] or not result['lastName']:
        for line in lines:
            if looks_like_name(line):
                parts = line.split()
                set_field('firstName', parts[0], 'Namenszeile')
                set_field('lastName', ' '.join(parts[1:]), 'Namenszeile')
                break

    # If labelled first/last name landed in one field accidentally.
    if result['firstName'] and not result['lastName'] and len(result['firstName'].split()) > 1:
        parts = result['firstName'].split()
        result['firstName'] = parts[0]
        result['lastName'] = ' '.join(parts[1:])
        evidence.append({'field': 'lastName', 'value': result['lastName'], 'reason': 'Vorname-Feld aufgeteilt'})

    filled = [k for k, v in result.items() if v]
    return {
        'ok': True,
        'fields': result,
        'filled': filled,
        'evidence': evidence,
        'lineCount': len(lines),
    }

COUNTRY_CODE_ALIASES = {
    'DE': 'DE', 'DEU': 'DE', 'DEUTSCHLAND': 'DE', 'GERMANY': 'DE',
    'AT': 'AT', 'AUT': 'AT', 'ÖSTERREICH': 'AT', 'OESTERREICH': 'AT', 'AUSTRIA': 'AT',
    'CH': 'CH', 'CHE': 'CH', 'SCHWEIZ': 'CH', 'SWITZERLAND': 'CH',
    'NL': 'NL', 'NLD': 'NL', 'NIEDERLANDE': 'NL', 'NETHERLANDS': 'NL',
    'BE': 'BE', 'BEL': 'BE', 'BELGIEN': 'BE', 'BELGIUM': 'BE',
    'FR': 'FR', 'FRA': 'FR', 'FRANKREICH': 'FR', 'FRANCE': 'FR',
    'LU': 'LU', 'LUX': 'LU', 'LUXEMBURG': 'LU', 'LUXEMBOURG': 'LU',
    'IT': 'IT', 'ITA': 'IT', 'ITALIEN': 'IT', 'ITALY': 'IT',
    'ES': 'ES', 'ESP': 'ES', 'SPANIEN': 'ES', 'SPAIN': 'ES',
    'PL': 'PL', 'POL': 'PL', 'POLEN': 'PL', 'POLAND': 'PL',
    'CZ': 'CZ', 'CZE': 'CZ', 'TSCHECHIEN': 'CZ', 'CZECHIA': 'CZ',
    'DK': 'DK', 'DNK': 'DK', 'DÄNEMARK': 'DK', 'DAENEMARK': 'DK', 'DENMARK': 'DK',
}


def normalize_country_code(value):
    raw = (value or DEFAULT_COUNTRY_CODE).strip().upper()
    return COUNTRY_CODE_ALIASES.get(raw, DEFAULT_COUNTRY_CODE)


def build_address(data):
    """Build a GraphQL MailingAddressInput with an explicit ISO country code."""
    address = {
        'firstName': (data.get('firstName') or '').strip(),
        'lastName': (data.get('lastName') or '').strip(),
        'address1': (data.get('address1') or '').strip(),
        'address2': (data.get('address2') or '').strip(),
        'zip': (data.get('zip') or '').strip(),
        'city': (data.get('city') or '').strip(),
        'countryCode': normalize_country_code(data.get('country')),
        'phone': clean_phone(data.get('phone')),
    }
    return {key: value for key, value in address.items() if value not in ('', None)}


def money_input(value):
    return {'amount': safe_money(value), 'currencyCode': SHOP_CURRENCY}


def graphql_user_errors(payload, operation):
    errors = payload.get('userErrors') or []
    if errors:
        detail = '; '.join(
            f"{'.'.join(str(x) for x in (e.get('field') or []))}: {e.get('message')}".strip(': ')
            for e in errors
        )
        raise RuntimeError(f'Shopify {operation}: {detail}')


def build_draft_order_input(data):
    customer = data.get('customer') or {}
    items = data.get('items') or []
    shipping = data.get('shipping') or {}
    if not items:
        raise ValueError('Keine Produkte ausgewählt.')

    line_items = []
    for item in items:
        quantity = int(item.get('quantity') or 1)
        if quantity < 1:
            raise ValueError('Ungültige Menge.')

        if item.get('custom'):
            title = (item.get('title') or '').strip()
            if not title:
                raise ValueError('Benutzerdefinierter Artikel braucht einen Titel.')
            line_items.append({
                'title': title,
                'originalUnitPriceWithCurrency': money_input(item.get('price')),
                'quantity': quantity,
                'requiresShipping': True,
                'taxable': True,
            })
            continue

        variant_gid = (item.get('variantId') or '').strip()
        if not variant_gid:
            numeric_id = item.get('variantNumericId')
            if numeric_id:
                variant_gid = f'gid://shopify/ProductVariant/{numeric_id}'
        if not variant_gid:
            raise ValueError('Ungültiger Shopify-Artikel.')
        if item.get('taxable') is False:
            raise ValueError(
                f"Die Shopify-Variante „{item.get('productTitle') or item.get('variantTitle') or variant_gid}“ "
                'ist in Shopify nicht als steuerpflichtig markiert.'
            )

        line_item = {'variantId': variant_gid, 'quantity': quantity}
        if item.get('price') not in (None, ''):
            line_item['priceOverride'] = money_input(item.get('price'))
        line_items.append(line_item)

    email = (customer.get('email') or '').strip()
    phone = clean_phone(customer.get('phone'))
    note = (data.get('note') or '').strip()
    tags = [x for x in ['Kleinanzeigen', 'Manuell', (data.get('extraTag') or '').strip()] if x]
    address = build_address(customer)

    return {
        'lineItems': line_items,
        'email': email or None,
        'phone': phone or None,
        'shippingAddress': address,
        'billingAddress': dict(address),
        'note': note or None,
        'tags': tags,
        'taxExempt': False,
        'presentmentCurrencyCode': SHOP_CURRENCY,
        'shippingLine': {
            'title': (shipping.get('title') or 'Versand').strip(),
            'priceWithCurrency': money_input(
                shipping.get('price') if shipping.get('price') is not None else DEFAULT_SHIPPING
            ),
        },
    }


def calculate_draft_order(draft_input):
    query = '''
    mutation CalculateDraftOrder($input: DraftOrderInput!) {
      draftOrderCalculate(input: $input) {
        calculatedDraftOrder {
          totalPriceSet { presentmentMoney { amount currencyCode } }
          totalTaxSet { presentmentMoney { amount currencyCode } }
          lineItems { title quantity taxable }
          taxesIncluded
          taxLines { title rate ratePercentage priceSet { presentmentMoney { amount currencyCode } } }
          shippingLine {
            title
            taxLines { title rate ratePercentage priceSet { presentmentMoney { amount currencyCode } } }
          }
        }
        userErrors { field message }
      }
    }
    '''
    data = shopify_graphql(query, {'input': draft_input})
    payload = data.get('draftOrderCalculate') or {}
    graphql_user_errors(payload, 'Steuerberechnung')
    calculated = payload.get('calculatedDraftOrder') or {}
    if not calculated:
        raise RuntimeError('Shopify hat keine Steuerberechnung zurückgegeben.')
    return calculated


def create_draft_order_graphql(draft_input):
    query = '''
    mutation CreateDraftOrder($input: DraftOrderInput!) {
      draftOrderCreate(input: $input) {
        draftOrder {
          id
          name
          invoiceUrl
          presentmentCurrencyCode
          taxesIncluded
          taxExempt
          totalPriceSet { presentmentMoney { amount currencyCode } }
          totalTaxSet { presentmentMoney { amount currencyCode } }
          taxLines { title rate ratePercentage priceSet { presentmentMoney { amount currencyCode } } }
          shippingAddress { countryCode }
          billingAddress { countryCode }
          lineItems(first: 100) {
            nodes {
              title
              quantity
              taxable
              taxLines { title rate ratePercentage priceSet { presentmentMoney { amount currencyCode } } }
            }
          }
          shippingLine {
            title
            taxLines { title rate ratePercentage priceSet { presentmentMoney { amount currencyCode } } }
          }
        }
        userErrors { field message }
      }
    }
    '''
    data = shopify_graphql(query, {'input': draft_input})
    payload = data.get('draftOrderCreate') or {}
    graphql_user_errors(payload, 'Draft-Order-Erstellung')
    created = payload.get('draftOrder') or {}
    if not created.get('id'):
        raise RuntimeError('Shopify hat keine Draft Order zurückgegeben.')
    return created


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.form if request.form else request.json or {}
        if data.get('user') == DASH_USER and data.get('password') == DASH_PASS and DASH_PASS:
            session['logged_in'] = True
            return redirect('/')
        return Response(LOGIN_HTML.replace('{{ERROR}}', '<div class="error">Login falsch.</div>'), mimetype='text/html')
    return Response(LOGIN_HTML.replace('{{ERROR}}', ''), mimetype='text/html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


@app.route('/')
@require_login
def index():
    return Response(INDEX_HTML, mimetype='text/html')


@app.route('/api/health')
@require_login
def api_health():
    try:
        scopes_data = shopify_access_scopes()
        scopes = [s.get('handle') for s in scopes_data.get('access_scopes', [])]
        return jsonify({
            'ok': True,
            'shop': SHOPIFY_SHOP,
            'apiVersion': SHOPIFY_API_VERSION,
            'defaultShipping': DEFAULT_SHIPPING,
            'shopCurrency': SHOP_CURRENCY,
            'defaultCountryCode': DEFAULT_COUNTRY_CODE,
            'requireTaxes': REQUIRE_TAXES,
            'scopes': scopes,
            'missingRecommendedScopes': [s for s in [
                'read_products','read_inventory','read_locations','read_customers','write_customers','read_draft_orders','write_draft_orders'
            ] if s not in scopes]
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/parse-contact', methods=['POST'])
@require_login
def api_parse_contact():
    data = request.json or {}
    raw = data.get('text') or ''
    if not raw.strip():
        return jsonify({'error': 'Kein Text zum Parsen übergeben.'}), 400
    return jsonify(parse_contact_text(raw))


@app.route('/api/products')
@require_login
def api_products():
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return jsonify([])
    search = q.replace('"', '')
    query = '''
    query SearchProducts($query: String!) {
      products(first: 20, query: $query) {
        edges {
          node {
            id
            title
            handle
            status
            variants(first: 30) {
              edges {
                node {
                  id
                  title
                  sku
                  price
                  inventoryQuantity
                  taxable
                }
              }
            }
          }
        }
      }
    }
    '''
    data = shopify_graphql(query, {'query': search})
    out = []
    for edge in data.get('products', {}).get('edges', []):
        p = edge['node']
        variants = []
        for ve in p.get('variants', {}).get('edges', []):
            v = ve['node']
            variants.append({
                'id': v.get('id'),
                'numericId': gid_to_numeric(v.get('id')),
                'title': v.get('title'),
                'sku': v.get('sku'),
                'price': v.get('price'),
                'inventoryQuantity': v.get('inventoryQuantity'),
                'taxable': v.get('taxable')
            })
        out.append({
            'id': p.get('id'),
            'title': p.get('title'),
            'handle': p.get('handle'),
            'status': p.get('status'),
            'variants': variants
        })
    return jsonify(out)


@app.route('/api/draft-orders', methods=['POST'])
@require_login
def api_create_draft_order():
    data = request.json or {}
    try:
        draft_input = build_draft_order_input(data)
        calculated = calculate_draft_order(draft_input)

        total_tax = safe_money(
            (((calculated.get('totalTaxSet') or {}).get('presentmentMoney') or {}).get('amount')) or '0'
        )
        country_code = ((draft_input.get('shippingAddress') or {}).get('countryCode') or DEFAULT_COUNTRY_CODE)
        if REQUIRE_TAXES and country_code == 'DE' and float(total_tax) <= 0:
            raise ValueError(
                'Shopify berechnet für diese deutsche Bestellung 0,00 € Umsatzsteuer. '
                'Die Draft Order wurde nicht angelegt, damit Lexware keine Bestellung mit ungültigem Steuersatz erhält. '
                'Prüfe in Shopify die deutsche Steuerregistrierung und bei allen verwendeten Produktvarianten '
                '„Steuer auf dieses Produkt erheben“.'
            )

        created = create_draft_order_graphql(draft_input)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        return jsonify({'error': str(exc)}), 502

    draft_gid = created.get('id') or ''
    draft_id = gid_to_numeric(draft_gid)
    admin_url = f'https://{SHOPIFY_SHOP}/admin/draft_orders/{draft_id}' if draft_id else ''
    address = draft_input.get('shippingAddress') or {}
    customer_name = ' '.join([address.get('firstName', ''), address.get('lastName', '')]).strip()
    total_money = (created.get('totalPriceSet') or {}).get('presentmentMoney') or {}
    tax_money = (created.get('totalTaxSet') or {}).get('presentmentMoney') or {}

    conn = db()
    conn.execute('''
        INSERT INTO draft_orders (created_at, shopify_id, shopify_name, admin_url, invoice_url, customer_name, email, phone, total_price, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        datetime.now(timezone.utc).isoformat(),
        str(draft_id or draft_gid),
        created.get('name') or '',
        admin_url,
        created.get('invoiceUrl') or '',
        customer_name,
        draft_input.get('email') or '',
        draft_input.get('phone') or '',
        str(total_money.get('amount') or ''),
        json.dumps({'request': data, 'calculated': calculated, 'shopify': created}, ensure_ascii=False)
    ))
    conn.commit()
    conn.close()

    return jsonify({
        'ok': True,
        'draftOrder': {
            'id': draft_id or draft_gid,
            'name': created.get('name'),
            'adminUrl': admin_url,
            'invoiceUrl': created.get('invoiceUrl'),
            'totalPrice': total_money.get('amount'),
            'totalTax': tax_money.get('amount'),
            'taxesIncluded': created.get('taxesIncluded'),
            'currency': total_money.get('currencyCode') or created.get('presentmentCurrencyCode'),
        }
    })


@app.route('/api/recent')
@require_login
def api_recent():
    conn = db()
    rows = [dict(r) for r in conn.execute('SELECT * FROM draft_orders ORDER BY id DESC LIMIT 20').fetchall()]
    conn.close()
    return jsonify(rows)


LOGIN_HTML = '''<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kleinanzeigen Tool Login</title><style>
body{margin:0;background:#071014;color:#eef3f5;font-family:system-ui,-apple-system,Segoe UI,sans-serif;min-height:100vh;display:grid;place-items:center}.card{width:min(420px,92vw);background:#101b22;border:1px solid #29444f;border-radius:22px;padding:28px}h1{margin-top:0}input,button{width:100%;box-sizing:border-box;border-radius:14px;padding:13px;margin-top:10px;font-size:16px}input{border:1px solid #29444f;background:#071014;color:#fff}button{border:0;background:#f2ad3d;color:#111;font-weight:900}.error{background:#3a1111;border:1px solid #863333;padding:10px;border-radius:12px;margin:12px 0}</style></head><body><form class="card" method="post"><h1>Kleinanzeigen Tool</h1>{{ERROR}}<input name="user" placeholder="Benutzer"><input name="password" type="password" placeholder="Passwort"><button>Einloggen</button></form></body></html>'''


INDEX_HTML = '''<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kleinanzeigen Order Tool</title><style>
:root{--bg:#071014;--card:#101b22;--line:#29444f;--text:#eef3f5;--muted:#9fb0bb;--gold:#f2ad3d;--good:#4fe38a;--bad:#ff6b6b}body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,sans-serif}header{position:sticky;top:0;background:rgba(7,16,20,.95);border-bottom:1px solid var(--line);padding:16px 20px;z-index:2}.wrap{max-width:1220px;margin:0 auto;padding:20px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}@media(max-width:900px){.grid{grid-template-columns:1fr}}.card{background:var(--card);border:1px solid var(--line);border-radius:22px;padding:18px;margin-bottom:18px}input,textarea,select,button{box-sizing:border-box;border-radius:14px;padding:12px;font-size:15px}input,textarea,select{width:100%;border:1px solid var(--line);background:#071014;color:#fff}label{display:block;color:var(--muted);font-weight:700;margin:12px 0 6px}.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:650px){.row{grid-template-columns:1fr}}button{border:0;background:var(--gold);color:#111;font-weight:900;cursor:pointer}button.secondary{background:#223044;color:#fff}.pill{display:inline-block;background:#223044;border-radius:999px;padding:7px 10px;margin:4px 4px 0 0;color:#cbd5dc}.result,.selected{border:1px solid var(--line);border-radius:16px;padding:12px;margin-top:10px;background:#0b151c}.small{color:var(--muted);font-size:13px}.ok{color:var(--good)}.bad{color:var(--bad)}a{color:#9dc9ff}.actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.top{display:flex;justify-content:space-between;gap:12px;align-items:center}.msg{padding:12px;border-radius:14px;margin:10px 0}.msg.okmsg{background:#0c2b1c;border:1px solid #236b45}.msg.err{background:#351414;border:1px solid #7d3333}.mini{font-size:12px;color:var(--muted)}.custombox{border:1px dashed #48627a;border-radius:16px;padding:12px;margin-top:14px;background:#0a151b}</style></head><body><header><div class="top"><div><strong>Kleinanzeigen Order Tool</strong><div class="small">Kleinanzeigen-Nachricht rein, Produkt wählen, Draft Order erstellen.</div></div><a href="/logout">Logout</a></div></header><main class="wrap"><div id="message"></div><div class="card"><h2>Status</h2><div id="health" class="small">Lade Status...</div></div><div class="grid"><section class="card"><h2>Kunde</h2><label>Kleinanzeigen-Nachricht einfügen</label><textarea id="rawContact" rows="7" placeholder="Hier die Nachricht oder Kontaktdaten vom Kunden einfügen. Beispiel:\nMax Mustermann\nMusterstraße 12\n41539 Dormagen\nmax@example.de\n0176 12345678"></textarea><div class="actions" style="margin-top:10px"><button type="button" onclick="parseContact()">Kontaktdaten automatisch übernehmen</button><button class="secondary" type="button" onclick="clearCustomer()">Kunde leeren</button></div><div class="mini">Der Parser läuft jetzt serverseitig. Nach dem Klick siehst du genau, welche Felder erkannt wurden.</div><div id="parseResult" class="small" style="margin-top:10px"></div><div class="row"><div><label>Vorname</label><input id="firstName"></div><div><label>Nachname</label><input id="lastName"></div></div><div class="row"><div><label>E-Mail</label><input id="email" type="email"></div><div><label>Telefon</label><input id="phone"></div></div><label>Straße und Hausnummer</label><input id="address1"><label>Adresszusatz</label><input id="address2"><div class="row"><div><label>PLZ</label><input id="zip"></div><div><label>Ort</label><input id="city"></div></div><label>Land</label><input id="country" value="Deutschland"></section><section class="card"><h2>Produkt suchen</h2><label>Suchbegriff</label><div class="actions"><input id="search" value="100 gemischte Pokemon Karten Deutsch" placeholder="z. B. Pikachu, OP16, Booster" style="flex:1;min-width:220px"><button onclick="searchProducts()">Suchen</button></div><div id="results"></div><div class="custombox"><h3>Benutzerdefinierter Artikel</h3><label>Titel</label><input id="customTitle" placeholder="z. B. 100 gemischte Pokémon Karten Deutsch"><div class="row"><div><label>Wunschpreis</label><input id="customPrice" type="number" step="0.01" placeholder="z. B. 19.99"></div><div><label>Menge</label><input id="customQty" type="number" min="1" value="1"></div></div><button class="secondary" type="button" onclick="addCustomItem()">Benutzerdefinierten Artikel hinzufügen</button></div></section></div><section class="card"><h2>Ausgewählte Artikel</h2><div id="selected"></div></section><section class="card"><h2>Versand und Notiz</h2><div class="row"><div><label>Versandbezeichnung</label><input id="shippingTitle" value="Kleinpaket"></div><div><label>Versandkosten</label><input id="shippingPrice" type="number" step="0.01" value="4.29"></div></div><label>Notiz</label><textarea id="note" rows="4" placeholder="Quelle: Kleinanzeigen, vereinbarter Preis, Hinweise..."></textarea><br><br><button onclick="createDraftOrder()">Draft Order in Shopify erstellen</button></section><section class="card"><h2>Letzte Draft Orders</h2><div id="recent"></div></section></main><script>
let selected=[];const DEFAULT_QUERY='100 gemischte Pokemon Karten Deutsch';function msg(t,ok=true){document.getElementById('message').innerHTML=`<div class="msg ${ok?'okmsg':'err'}">${esc(t)}</div>`}function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}async function api(url,opt){const r=await fetch(url,opt);let d;try{d=await r.json()}catch(e){d={error:await r.text()}}if(!r.ok)throw new Error(d.error||'Fehler');return d}async function loadHealth(){try{const h=await api('/api/health');document.getElementById('shippingPrice').value=h.defaultShipping||'4.29';if(!document.getElementById('shippingTitle').value)document.getElementById('shippingTitle').value='Kleinpaket';const missing=h.missingRecommendedScopes||[];document.getElementById('health').innerHTML=`<div>Shop: <span class="ok">${esc(h.shop)}</span> · API: ${esc(h.apiVersion)}</div><div>Scopes: ${(h.scopes||[]).map(x=>`<span class="pill">${esc(x)}</span>`).join('')}</div>`+(missing.length?`<div class="bad">Fehlende empfohlene Scopes: ${missing.map(esc).join(', ')}</div>`:'<div class="ok">Alle empfohlenen Scopes vorhanden.</div>')}catch(e){document.getElementById('health').innerHTML=`<span class="bad">${esc(e.message)}</span>`}}async function searchProducts(){const q=document.getElementById('search').value.trim();if(q.length<2)return msg('Bitte mindestens 2 Zeichen suchen.',false);document.getElementById('results').innerHTML='Suche...';try{const data=await api('/api/products?q='+encodeURIComponent(q));document.getElementById('results').innerHTML=data.map(p=>`<div class="result"><strong>${esc(p.title)}</strong><div class="small">${esc(p.status)} · ${esc(p.handle)}</div>${p.variants.map(v=>`<div class="actions" style="margin-top:8px"><span class="pill">${esc(v.title)} · ${esc(v.price)} € · Bestand: ${esc(v.inventoryQuantity)} · ${v.taxable?'steuerpflichtig':'nicht steuerpflichtig'}</span><button class="secondary" onclick='addItem(${JSON.stringify({productTitle:p.title,variantTitle:v.title,variantId:v.id,variantNumericId:v.numericId,price:v.price,sku:v.sku,taxable:v.taxable}).replace(/'/g,"&#39;")})'>Hinzufügen</button></div>`).join('')}</div>`).join('')||'Keine Treffer.'}catch(e){document.getElementById('results').innerHTML='';msg(e.message,false)}}function addItem(item){item.quantity=1;item.custom=false;selected.push(item);renderSelected()}function addCustomItem(){const title=document.getElementById('customTitle').value.trim();const price=document.getElementById('customPrice').value.trim();const qty=Math.max(1,parseInt(document.getElementById('customQty').value||1));if(!title)return msg('Bitte Titel für den benutzerdefinierten Artikel eintragen.',false);if(!price)return msg('Bitte Wunschpreis eintragen.',false);selected.push({custom:true,title,price,quantity:qty});document.getElementById('customTitle').value='';document.getElementById('customPrice').value='';document.getElementById('customQty').value='1';renderSelected()}function removeItem(i){selected.splice(i,1);renderSelected()}function setQty(i,v){selected[i].quantity=Math.max(1,parseInt(v||1));renderSelected()}function renderSelected(){document.getElementById('selected').innerHTML=selected.map((it,i)=>{const title=it.custom?it.title:it.productTitle;const sub=it.custom?'Benutzerdefiniert':it.variantTitle;const price=it.custom?it.price:it.price;return `<div class="selected"><strong>${esc(title)}</strong><div>${esc(sub)} · ${esc(price)} €</div><div class="actions" style="margin-top:8px"><input type="number" value="${it.quantity}" min="1" style="width:100px" onchange="setQty(${i},this.value)"><button class="secondary" onclick="removeItem(${i})">Entfernen</button></div></div>`}).join('')||'<div class="small">Noch keine Artikel ausgewählt.</div>'}function val(id){return document.getElementById(id).value}function setVal(id,v){if(v!==undefined&&v!==null&&String(v).trim())document.getElementById(id).value=String(v).trim()}async function parseContact(){
  const raw=document.getElementById('rawContact').value||'';
  if(!raw.trim()) return msg('Bitte zuerst eine Nachricht einfügen.',false);
  const btns=[...document.querySelectorAll('button')].filter(b=>b.textContent.includes('Kontaktdaten'));
  btns.forEach(b=>{b.disabled=true;b.dataset.old=b.textContent;b.textContent='Übernehme...'});
  try{
    const data=await api('/api/parse-contact',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:raw})});
    const f=data.fields||{};
    const ids=['firstName','lastName','email','phone','address1','address2','zip','city','country'];
    let changed=[];
    for(const id of ids){
      if(f[id]!==undefined && f[id]!==null && String(f[id]).trim()){
        document.getElementById(id).value=String(f[id]).trim();
        changed.push(id);
      }
    }
    const labels={firstName:'Vorname',lastName:'Nachname',email:'E-Mail',phone:'Telefon',address1:'Straße',address2:'Adresszusatz',zip:'PLZ',city:'Ort',country:'Land'};
    const html=(data.evidence||[]).map(e=>`<span class="pill">${esc(labels[e.field]||e.field)}: ${esc(e.value)} <span class="mini">${esc(e.reason)}</span></span>`).join('');
    document.getElementById('parseResult').innerHTML = html || '<span class="bad">Es wurden keine eindeutigen Kontaktdaten erkannt.</span>';
    if(changed.length) msg('Kontaktdaten übernommen. Bitte kurz prüfen.',true); else msg('Keine eindeutigen Kontaktdaten erkannt. Bitte manuell prüfen.',false);
  }catch(e){
    document.getElementById('parseResult').innerHTML='<span class="bad">Parser-Fehler: '+esc(e.message)+'</span>';
    msg(e.message,false);
  }finally{
    btns.forEach(b=>{b.disabled=false;b.textContent=b.dataset.old||'Kontaktdaten automatisch übernehmen'});
  }
}function clearCustomer(){['rawContact','firstName','lastName','email','phone','address1','address2','zip','city'].forEach(id=>document.getElementById(id).value='');document.getElementById('country').value='Deutschland'}async function createDraftOrder(){if(!selected.length)return msg('Bitte mindestens einen Artikel auswählen.',false);const payload={customer:{firstName:val('firstName'),lastName:val('lastName'),email:val('email'),phone:val('phone'),address1:val('address1'),address2:val('address2'),zip:val('zip'),city:val('city'),country:val('country')},items:selected,shipping:{title:val('shippingTitle'),price:val('shippingPrice')},note:val('note')};try{msg('Erstelle Draft Order...');const r=await api('/api/draft-orders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=r.draftOrder;msg(`Draft Order ${d.name||d.id} erstellt. Enthaltene Steuer: ${d.totalTax||'0.00'} ${d.currency||'EUR'}.`,true);selected=[];renderSelected();loadRecent();if(d.adminUrl)window.open(d.adminUrl,'_blank')}catch(e){msg(e.message,false)}}async function loadRecent(){try{const rows=await api('/api/recent');document.getElementById('recent').innerHTML=rows.map(r=>`<div class="result"><strong>${esc(r.shopify_name||r.shopify_id)}</strong><div>${esc(r.customer_name)} · ${esc(r.total_price)} €</div><div class="small">${esc(r.created_at)}</div><a href="${esc(r.admin_url)}" target="_blank">In Shopify öffnen</a></div>`).join('')||'<div class="small">Noch keine Draft Orders.</div>'}catch(e){document.getElementById('recent').textContent=e.message}}loadHealth();renderSelected();loadRecent();setTimeout(()=>{if(document.getElementById('search').value===DEFAULT_QUERY)searchProducts()},250);</script></body></html>'''

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '8789')))

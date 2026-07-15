import 'dotenv/config';
import express from 'express';
import crypto from 'crypto';

const app = express();
const port = Number(process.env.PORT || 8790);
const store = (process.env.SHOPIFY_STORE || '').replace(/^https?:\/\//, '').replace(/\/$/, '');
const clientId = process.env.SHOPIFY_CLIENT_ID || '';
const clientSecret = process.env.SHOPIFY_CLIENT_SECRET || '';
let cachedToken = '';
let tokenExpiresAt = 0;
const apiVersion = process.env.SHOPIFY_API_VERSION || '2026-07';
const appPassword = process.env.APP_PASSWORD || '';

app.use(express.json({ limit: '1mb' }));
app.use(express.static('public'));

function safeEqual(a, b) {
  const aa = Buffer.from(String(a));
  const bb = Buffer.from(String(b));
  return aa.length === bb.length && crypto.timingSafeEqual(aa, bb);
}

function auth(req, res, next) {
  if (!appPassword) return res.status(500).json({ error: 'APP_PASSWORD fehlt in der .env-Datei.' });
  const supplied = req.headers['x-app-password'] || '';
  if (!safeEqual(supplied, appPassword)) return res.status(401).json({ error: 'Falsches Passwort.' });
  next();
}

async function getShopifyToken() {
  if (!store || !clientId || !clientSecret) {
    throw new Error('SHOPIFY_STORE, SHOPIFY_CLIENT_ID oder SHOPIFY_CLIENT_SECRET fehlt.');
  }
  if (cachedToken && Date.now() < tokenExpiresAt - 60_000) return cachedToken;

  const response = await fetch(`https://${store}/admin/oauth/access_token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'client_credentials',
      client_id: clientId,
      client_secret: clientSecret
    })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.access_token) {
    throw new Error(data.error_description || data.error || `Shopify-Token konnte nicht erzeugt werden (HTTP ${response.status}).`);
  }
  cachedToken = data.access_token;
  tokenExpiresAt = Date.now() + Number(data.expires_in || 86399) * 1000;
  return cachedToken;
}

async function shopifyGraphQL(query, variables = {}) {
  const token = await getShopifyToken();
  const response = await fetch(`https://${store}/admin/api/${apiVersion}/graphql.json`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Shopify-Access-Token': token
    },
    body: JSON.stringify({ query, variables })
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data?.errors?.[0]?.message || `Shopify HTTP ${response.status}`);
  if (data.errors?.length) throw new Error(data.errors.map(e => e.message).join(' | '));
  return data.data;
}

app.get('/api/health', auth, async (req, res) => {
  try {
    const data = await shopifyGraphQL(`query { shop { name myshopifyDomain } }`);
    res.json({ ok: true, shop: data.shop });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.get('/api/products', auth, async (req, res) => {
  try {
    const search = String(req.query.q || '').trim();
    const queryString = search ? `title:*${search.replace(/["\\]/g, ' ')}*` : '';
    const data = await shopifyGraphQL(`
      query ProductSearch($query: String!) {
        productVariants(first: 30, query: $query, sortKey: RELEVANCE) {
          nodes {
            id title sku barcode price inventoryQuantity
            product { id title status featuredMedia { preview { image { url altText } } } }
          }
        }
      }
    `, { query: queryString });
    const products = data.productVariants.nodes
      .filter(v => v.product.status === 'ACTIVE')
      .map(v => ({
        id: v.id,
        productId: v.product.id,
        title: v.product.title,
        variantTitle: v.title,
        sku: v.sku,
        barcode: v.barcode,
        price: v.price,
        inventoryQuantity: v.inventoryQuantity,
        image: v.product.featuredMedia?.preview?.image?.url || ''
      }));
    res.json({ products });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.post('/api/orders', auth, async (req, res) => {
  try {
    const { customer, items, shipping, note, mode = 'draft' } = req.body;
    if (!customer?.firstName || !customer?.lastName || !customer?.address1 || !customer?.zip || !customer?.city) {
      return res.status(400).json({ error: 'Name und vollständige Versandadresse fehlen.' });
    }
    if (!Array.isArray(items) || items.length === 0) return res.status(400).json({ error: 'Mindestens ein Produkt fehlt.' });

    const lineItems = items.map(item => {
      const base = { variantId: item.variantId, quantity: Number(item.quantity || 1) };
      const original = Number(item.originalPrice);
      const custom = Number(item.price);
      if (Number.isFinite(custom) && Number.isFinite(original) && custom >= 0 && Math.abs(custom - original) > 0.001) {
        const totalOriginal = original * base.quantity;
        const totalCustom = custom * base.quantity;
        const discount = Math.max(0, totalOriginal - totalCustom);
        if (discount > 0) {
          base.appliedDiscount = {
            title: 'Kleinanzeigen-Preis',
            valueType: 'FIXED_AMOUNT',
            value: discount.toFixed(2),
            amount: discount.toFixed(2)
          };
        }
      }
      return base;
    });

    const requestedCountryCode = String(customer.countryCode || 'DE').trim().toUpperCase();
    const countryCode = /^[A-Z]{2}$/.test(requestedCountryCode) ? requestedCountryCode : 'DE';
    const address = {
      firstName: customer.firstName || undefined,
      lastName: customer.lastName || undefined,
      company: customer.company || undefined,
      address1: customer.address1 || undefined,
      address2: customer.address2 || undefined,
      zip: customer.zip || undefined,
      city: customer.city || undefined,
      countryCode,
      phone: customer.phone || undefined
    };

    const input = {
      email: customer.email || undefined,
      phone: customer.phone || undefined,
      note: note || 'Verkauf über Kleinanzeigen',
      tags: ['Kleinanzeigen'],
      lineItems,
      shippingAddress: { ...address },
      billingAddress: { ...address }
    };

    if (shipping && Number(shipping.price) >= 0) {
      input.shippingLine = {
        title: shipping.title || 'Versand',
        priceWithCurrency: { amount: Number(shipping.price).toFixed(2), currencyCode: 'EUR' }
      };
    }

    const created = await shopifyGraphQL(`
      mutation CreateDraft($input: DraftOrderInput!) {
        draftOrderCreate(input: $input) {
          draftOrder { id name invoiceUrl }
          userErrors { field message }
        }
      }
    `, { input });

    const errors = created.draftOrderCreate.userErrors;
    if (errors.length) throw new Error(errors.map(e => e.message).join(' | '));
    const draft = created.draftOrderCreate.draftOrder;

    if (mode === 'paid') {
      const completed = await shopifyGraphQL(`
        mutation CompleteDraft($id: ID!) {
          draftOrderComplete(id: $id, paymentPending: false) {
            draftOrder { id name order { id name } }
            userErrors { field message }
          }
        }
      `, { id: draft.id });
      const completeErrors = completed.draftOrderComplete.userErrors;
      if (completeErrors.length) throw new Error(completeErrors.map(e => e.message).join(' | '));
      const order = completed.draftOrderComplete.draftOrder.order;
      const numericId = order.id.split('/').pop();
      return res.json({ ok: true, type: 'order', name: order.name, adminUrl: `https://${store}/admin/orders/${numericId}` });
    }

    const numericId = draft.id.split('/').pop();
    res.json({ ok: true, type: 'draft', name: draft.name, invoiceUrl: draft.invoiceUrl, adminUrl: `https://${store}/admin/draft_orders/${numericId}` });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.listen(port, '0.0.0.0', () => console.log(`Shopify Kleinanzeigen Tool läuft auf Port ${port}`));

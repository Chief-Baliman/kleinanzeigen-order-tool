import unittest
from unittest.mock import patch

import app


class CoreTests(unittest.TestCase):
    def test_country_defaults_to_germany(self):
        self.assertEqual(app.normalize_country_code(''), 'DE')
        self.assertEqual(app.normalize_country_code('Deutschland'), 'DE')
        self.assertEqual(app.normalize_country_code('Germany'), 'DE')

    def test_graphql_address_uses_iso_code(self):
        address = app.build_address({
            'firstName': 'Max', 'lastName': 'Mustermann',
            'address1': 'Musterstraße 1', 'zip': '41539', 'city': 'Dormagen',
            'country': 'Deutschland',
        })
        self.assertEqual(address['countryCode'], 'DE')
        self.assertNotIn('country', address)

    def test_custom_item_is_taxable(self):
        payload = app.build_draft_order_input({
            'customer': {'country': 'Deutschland'},
            'items': [{'custom': True, 'title': 'Test', 'price': '11,90', 'quantity': 1}],
            'shipping': {'title': 'Versand', 'price': '4,29'},
        })
        item = payload['lineItems'][0]
        self.assertTrue(item['taxable'])
        self.assertEqual(item['originalUnitPriceWithCurrency']['amount'], '11.90')
        self.assertFalse(payload['taxExempt'])
        self.assertEqual(payload['shippingAddress']['countryCode'], 'DE')
        self.assertEqual(payload['billingAddress']['countryCode'], 'DE')

    def test_variant_price_override_and_taxable_guard(self):
        payload = app.build_draft_order_input({
            'customer': {'country': 'DE'},
            'items': [{
                'variantId': 'gid://shopify/ProductVariant/123',
                'price': '9.99', 'quantity': 2, 'taxable': True,
            }],
            'shipping': {'price': '0'},
        })
        item = payload['lineItems'][0]
        self.assertEqual(item['variantId'], 'gid://shopify/ProductVariant/123')
        self.assertEqual(item['priceOverride']['amount'], '9.99')

        with self.assertRaises(ValueError):
            app.build_draft_order_input({
                'customer': {'country': 'DE'},
                'items': [{
                    'variantId': 'gid://shopify/ProductVariant/123',
                    'price': '9.99', 'quantity': 1, 'taxable': False,
                }],
                'shipping': {'price': '0'},
            })

    @patch('app.shopify_graphql')
    def test_calculation_errors_are_propagated(self, gql):
        gql.return_value = {'draftOrderCalculate': {
            'calculatedDraftOrder': None,
            'userErrors': [{'field': ['input', 'lineItems'], 'message': 'Fehler'}],
        }}
        with self.assertRaises(RuntimeError):
            app.calculate_draft_order({'lineItems': []})


if __name__ == '__main__':
    unittest.main()

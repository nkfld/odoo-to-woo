#!/usr/bin/env python3
"""
Odoo to WooCommerce Stock Synchronization
Fetches stock levels from Odoo and updates them in WooCommerce.
"""

import os
import json
import xmlrpc.client
import requests
from datetime import datetime
import base64


class OdooWooCommerceStockSync:
    def __init__(self):
        # WooCommerce config (from ENV)
        self.wc_url = os.getenv('WC_URL')
        self.wc_consumer_key = os.getenv('WC_CONSUMER_KEY')
        self.wc_consumer_secret = os.getenv('WC_CONSUMER_SECRET')

        # Odoo config (from ENV)
        self.odoo_url = os.getenv('ODOO_URL')
        self.odoo_db = os.getenv('ODOO_DB')
        self.odoo_username = os.getenv('ODOO_USERNAME') or os.getenv('ODOO_USER')
        self.odoo_password = os.getenv('ODOO_PASSWORD')

        # Source location (warehouse)
        location_id_str = (os.getenv('ODOO_LOCATION_ID', '8') or '8').strip()
        try:
            self.odoo_location_id = int(location_id_str)
        except ValueError:
            print(f"WARNING: Invalid ODOO_LOCATION_ID value: '{location_id_str}' - using default 8")
            self.odoo_location_id = 8

        # Load product mapping
        # Format: {"odoo_barcode": "wc_id"}
        self.product_mapping = self.load_product_mapping()

        # Odoo connection
        self.odoo_uid = None
        self.odoo_models = None

        print("Odoo to WooCommerce Stock Sync started")
        print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Loaded mapping for {len(self.product_mapping)} products")

    # -------------------- MAPPING --------------------
    def load_product_mapping(self):
        """Load mapping from product_mapping.json file."""
        try:
            path = 'product_mapping.json'
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    mapping = json.load(f)
                print("Loaded mapping from product_mapping.json")
                return mapping
            else:
                print("WARNING: product_mapping.json not found - using empty mapping")
                return {}
        except Exception as e:
            print(f"ERROR: Failed to load mapping: {e}")
            return {}

    # -------------------- ODOO --------------------
    def connect_odoo(self):
        try:
            print("Connecting to Odoo...")
            print(f"URL: {self.odoo_url}")
            print(f"DB: {self.odoo_db}")
            print(f"User: {self.odoo_username}")

            missing = []
            if not self.odoo_url: missing.append('ODOO_URL')
            if not self.odoo_db: missing.append('ODOO_DB')
            if not self.odoo_username: missing.append('ODOO_USERNAME/ODOO_USER')
            if not self.odoo_password: missing.append('ODOO_PASSWORD')
            if missing:
                raise Exception(f"Missing Odoo variables: {missing}")

            common = xmlrpc.client.ServerProxy(f'{self.odoo_url}/xmlrpc/2/common', allow_none=True)
            version_info = common.version()
            print(f"Odoo version: {version_info.get('server_version', 'unknown')}")

            print("Attempting authentication...")
            uid = common.authenticate(self.odoo_db, self.odoo_username, self.odoo_password, {})
            print(f"Authentication result: {uid}")
            if not uid:
                raise Exception("Invalid Odoo credentials")

            self.odoo_uid = uid
            self.odoo_models = xmlrpc.client.ServerProxy(f'{self.odoo_url}/xmlrpc/2/object', allow_none=True)
            print(f"Connected to Odoo (User ID: {self.odoo_uid})")
            return True

        except Exception as e:
            print(f"ERROR: Odoo connection failed: {e}")
            return False

    def get_product_stock_by_barcode(self, barcode):
        """
        Fetch product stock level from Odoo by barcode.
        Returns dict with product info and stock level.
        """
        try:
            products = self.odoo_models.execute_kw(
                self.odoo_db, self.odoo_uid, self.odoo_password,
                'product.product', 'search_read',
                [[['barcode', '=', str(barcode)]]],
                {'fields': ['id', 'name', 'barcode', 'qty_available'], 'limit': 1}
            )

            if products:
                product = products[0]
                return {
                    'id': product['id'],
                    'name': product['name'],
                    'barcode': product['barcode'],
                    'qty_available': product.get('qty_available', 0)
                }
            else:
                print(f"WARNING: Product not found in Odoo with barcode: {barcode}")
                return None

        except Exception as e:
            print(f"ERROR: Failed to fetch product {barcode} from Odoo: {e}")
            return None

    def get_all_products_stock(self):
        """
        Fetch stock levels for all products from mapping.
        Returns dict: {barcode: {'name': ..., 'qty': ..., 'wc_id': ...}}
        """
        products_stock = {}

        print(f"\nFetching stock for {len(self.product_mapping)} products from Odoo...")

        for barcode, wc_id in self.product_mapping.items():
            product = self.get_product_stock_by_barcode(barcode)
            if product:
                products_stock[barcode] = {
                    'name': product['name'],
                    'qty': product['qty_available'],
                    'wc_id': int(wc_id)
                }
                print(f"  OK: {product['name']} ({barcode}): {product['qty_available']} units -> WC ID: {wc_id}")
            else:
                print(f"  SKIPPED: barcode {barcode}")

        return products_stock

    # -------------------- WOOCOMMERCE --------------------
    def update_woocommerce_stock(self, product_id, stock_quantity, product_name=""):
        """
        Update product stock level in WooCommerce.
        """
        try:
            url = f"{self.wc_url}/wp-json/wc/v3/products/{product_id}"
            auth = base64.b64encode(f"{self.wc_consumer_key}:{self.wc_consumer_secret}".encode()).decode()
            headers = {'Authorization': f'Basic {auth}', 'Content-Type': 'application/json'}

            payload = {
                'stock_quantity': int(stock_quantity),
                'manage_stock': True,
                'stock_status': 'instock' if stock_quantity > 0 else 'outofstock'
            }

            response = requests.put(url, headers=headers, json=payload, timeout=20)
            response.raise_for_status()

            print(f"    OK: WC #{product_id} ({product_name}): updated to {stock_quantity} units")
            return True

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                print(f"    WARNING: WC #{product_id}: product not found (404)")
            else:
                print(f"    ERROR: WC #{product_id}: HTTP {e.response.status_code} - {e}")
            return False
        except Exception as e:
            print(f"    ERROR: WC #{product_id}: update failed - {e}")
            return False

    # -------------------- SYNCHRONIZATION --------------------
    def sync_stock(self):
        """
        Main stock synchronization function: Odoo -> WooCommerce.
        """
        try:
            print("\nStarting stock synchronization: Odoo -> WooCommerce")

            # Fetch stock levels from Odoo
            products_stock = self.get_all_products_stock()

            if not products_stock:
                print("WARNING: No products to synchronize")
                return False

            print(f"\nUpdating stock in WooCommerce...")

            total_updated = 0
            total_errors = 0

            # Update each product in WooCommerce
            for barcode, data in products_stock.items():
                product_name = data['name']
                qty = data['qty']
                wc_id = data['wc_id']

                print(f"\n  Product: {product_name} ({barcode}): {qty} units -> WC #{wc_id}")

                if self.update_woocommerce_stock(wc_id, qty, product_name):
                    total_updated += 1
                else:
                    total_errors += 1

            print(f"\nSynchronization completed")
            print(f"Updated: {total_updated} products")
            if total_errors > 0:
                print(f"Errors: {total_errors}")

            return True

        except Exception as e:
            print(f"ERROR: Synchronization failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    # -------------------- RUN --------------------
    def run(self):
        """Main function to run synchronization."""
        try:
            if not self.connect_odoo():
                return False

            return self.sync_stock()

        except Exception as e:
            print(f"CRITICAL ERROR: {e}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == "__main__":
    sync = OdooWooCommerceStockSync()
    success = sync.run()
    exit(0 if success else 1)

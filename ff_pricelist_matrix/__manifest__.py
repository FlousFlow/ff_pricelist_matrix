# -*- coding: utf-8 -*-
# Part of ff_pricelist_matrix. See README.md for details.
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3).
{
    'name': 'Price Matrix — Category Discounts Manager',
    'version': '19.0.1.2.0',
    'category': 'Sales/Sales',
    'summary': 'Excel-like matrix to manage per-product-category discounts on '
               'pricelists, on top of the standard Odoo pricing engine.',
    'description': """
Price Matrix — Category Discounts Manager
=========================================
Manage "Pricelist x Product Category -> Discount %" matrices from a single
spreadsheet-like screen. Every edit is stored as a standard
``product.pricelist.item`` rule (Apply on: Product Category, Compute Price:
Discount, Based on: Sales Price), so Sale Orders, POS, Website and every
consumer of the standard pricing engine keep working untouched.

Highlights
----------
* OWL board: rows = product categories (hierarchy aware), columns = pricelists,
  cells = discount %, batch save (no N+1).
* Ownership: the module only touches rules it created itself
  (``managed_by_matrix``). Manual rules are never modified or deleted.
* Bulk fill (selected categories x one pricelist) and column copy.
* Excel import wizard with analysis & preview before applying:
  product matching by Barcode then Internal Reference, discount
  reverse-engineering per category, tolerance-based rounding normalization,
  inconsistent-category conflict resolution.
* Change audit trail (old/new discount, user, date).
* Multi-company aware. Security groups: User (view) / Administrator (edit,
  import, bulk).
* Uninstall-safe: managed rules remain as standard pricelist rules; the module
  never deletes pricing data it did not create.
""",
    'author': 'Flous Flow',
    'website': 'https://flousflow.com',
    'license': 'LGPL-3',
    'depends': [
        'sale_management',
        'web',
    ],
    'data': [
        'security/security_groups.xml',
        'security/ir.model.access.csv',
        'views/price_matrix_views.xml',
        'wizards/price_matrix_import_views.xml',
        'views/menus.xml',
    ],
    'images': [
        'static/description/thumbnail.png',
        'static/description/banner.png',
        'static/description/cover.png',
        'static/description/icon.png',
    ],
    'post_init_hook': 'post_init_hook',
    'application': False,
    'installable': True,
}

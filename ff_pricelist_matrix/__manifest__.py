# -*- coding: utf-8 -*-
# Part of ff_pricelist_matrix. See README.md for details.
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3).
{
    'name': 'Price Matrix - Category Discounts Manager',
    'version': '19.0.1.3.0',
    'category': 'Sales/Sales',
    'summary': 'Excel-like matrix to manage per-product-category discounts on '
               'pricelists, on top of the standard Odoo pricing engine.',
    'description': """
Price Matrix - Category Discounts Manager
=========================================
Manage "Pricelist x Product Category -> Discount %" matrices from a single
standard Odoo editable list. Every edit is stored as a standard
``product.pricelist.item`` rule (Apply on: Product Category, Compute Price:
Discount, Based on: Sales Price), so Sale Orders, POS, Website and every
consumer of the standard pricing engine keep working untouched.

Highlights
----------
* 100% standard Odoo editable list: one row per (Pricelist x Product
  Category), inline discount editing - no custom JS/CSS, native search,
  grouping, dark mode and RTL.
* Ownership: the module only touches rules it created itself
  (``managed_by_matrix``). Manual rules are never modified or deleted.
* One managed rule per (pricelist, category) - enforced at DB level.
* Deterministic priority: deeper category wins over parents, product
  exceptions win over categories, quantity breaks keep working (standard rule
  order, enforced and documented).
* Excel import wizard with analysis and preview before applying: product
  matching by Barcode then Internal Reference, discount reverse-engineering
  per category, tolerance-based rounding normalization (14.999999 -> 15),
  inconsistent-category conflict resolution.
* Change audit trail (old/new discount, user, date, source).
* Multi-company aware. Security groups: User (view) / Administrator (edit,
  import, history).
* Uninstall-safe: managed rules remain as standard pricelist rules; the module
  never deletes pricing data it did not create.
* Full Arabic translation (i18n/ar.po).
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

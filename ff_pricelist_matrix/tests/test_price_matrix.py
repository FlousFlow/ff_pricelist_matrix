# -*- coding: utf-8 -*-
import base64
import io

import openpyxl
from psycopg2 import errors as pg_errors

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools import float_compare, float_is_zero


@tagged('post_install', '-at_install')
class TestPriceMatrix(TransactionCase):
    """End-to-end coverage of the Price Matrix module.

    The pricing side is never asserted against module code: every price
    expectation goes through the STANDARD engine (product.pricelist._get_
    product_price / sale.order.line), which is the module's contract.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))

        # --- category hierarchy: Food > Biscuits > Mini Classic
        cls.categ_food = cls.env['product.category'].create({'name': 'Food'})
        cls.categ_biscuits = cls.env['product.category'].create({
            'name': 'Biscuits', 'parent_id': cls.categ_food.id})
        cls.categ_mini = cls.env['product.category'].create({
            'name': 'Mini Classic', 'parent_id': cls.categ_biscuits.id})

        # --- pricelists
        cls.pl_wholesale = cls.env['product.pricelist'].create({
            'name': 'Wholesale', 'company_id': cls.env.company.id})
        cls.pl_retail = cls.env['product.pricelist'].create({
            'name': 'Retail', 'company_id': cls.env.company.id})
        cls.company2 = cls.env['res.company'].create({'name': 'Other Co'})
        cls.pl_foreign = cls.env['product.pricelist'].create({
            'name': 'Foreign PL', 'company_id': cls.company2.id})

        # --- products
        def product(name, categ, price, barcode=None, code=None):
            return cls.env['product.product'].create({
                'name': name,
                'categ_id': categ.id,
                'list_price': price,
                'type': 'consu',
                'barcode': barcode,
                'default_code': code,
                'is_storable': False,
            })

        cls.p_cinnamon = product(
            'Mini Cinnamon 120g', cls.categ_mini, 20.0,
            barcode='BC-CIN', code='MC-120')
        cls.p_tea = product(
            'Mini Tea 100g', cls.categ_mini, 50.0,
            barcode='BC-TEA', code='MT-100')
        cls.p_choco = product(
            'Choco Bar', cls.categ_biscuits, 100.0,
            barcode='BC-CHO', code='CB-01')

        cls.board = cls.env['price.matrix.board']

        # --- a matrix manager user (su=False) for permission tests
        # Odoo 19: the res.users groups field is `group_ids`
        cls.manager = cls.env['res.users'].create({
            'name': 'Matrix Manager',
            'login': 'matrix_manager',
            'email': 'manager@example.com',
            'group_ids': [
                Command.link(
                    cls.env.ref(
                        'ff_pricelist_matrix.group_price_matrix_manager').id),
            ],
        })
        cls.basic_user = cls.env['res.users'].create({
            'name': 'Matrix User',
            'login': 'matrix_user',
            'email': 'user@example.com',
            'group_ids': [
                Command.link(
                    cls.env.ref(
                        'ff_pricelist_matrix.group_price_matrix_user').id),
            ],
        })

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def cells(self, pricelist, pairs):
        return [
            {'pricelist_id': pricelist.id, 'categ_id': categ.id,
             'discount': discount}
            for categ, discount in pairs
        ]

    def assertPrice(self, pricelist, product, expected, quantity=1.0):
        price = pricelist._get_product_price(product, quantity)
        self.assertTrue(
            float_compare(price, expected, precision_digits=2) == 0,
            f"{product.name} in {pricelist.name}: expected {expected}, "
            f"got {price}",
        )

    def board_as(self, user, companies=None):
        """The board API seen by a concrete (non-superuser) user."""
        context = dict(self.env.context)
        if companies:
            context['allowed_company_ids'] = companies
        return self.board.with_env(
            self.env(user=user.id, su=False, context=context))

    # ------------------------------------------------------------------
    # 1 & 2 — category discount applied through the standard engine
    # ------------------------------------------------------------------
    def test_01_category_discount_wholesale(self):
        """List 20, category discount 15% -> 17 (standard engine)."""
        result = self.board.write_cells(self.cells(
            self.pl_wholesale, [(self.categ_mini, 15.0)]))
        self.assertEqual(result['created'], 1)
        item = self.pl_wholesale.item_ids
        self.assertEqual(len(item), 1)
        self.assertTrue(item.managed_by_matrix)
        self.assertEqual(item.applied_on, '2_product_category')
        self.assertEqual(item.compute_price, 'percentage')
        self.assertEqual(item.base, 'list_price')
        self.assertFalse(item.min_quantity)
        self.assertPrice(self.pl_wholesale, self.p_cinnamon, 17.0)

    def test_02_second_pricelist_same_category(self):
        """Same category, Retail 5% -> 19."""
        self.board.write_cells([
            {'pricelist_id': self.pl_wholesale.id,
             'categ_id': self.categ_mini.id, 'discount': 15.0},
            {'pricelist_id': self.pl_retail.id,
             'categ_id': self.categ_mini.id, 'discount': 5.0},
        ])
        self.assertPrice(self.pl_wholesale, self.p_cinnamon, 17.0)
        self.assertPrice(self.pl_retail, self.p_cinnamon, 19.0)

    # ------------------------------------------------------------------
    # 3 — product exception wins (standard priority)
    # ------------------------------------------------------------------
    def test_03_product_exception_wins(self):
        self.board.write_cells(self.cells(
            self.pl_wholesale, [(self.categ_mini, 10.0)]))
        self.env['product.pricelist.item'].create({
            'pricelist_id': self.pl_wholesale.id,
            'applied_on': '1_product',
            'product_tmpl_id': self.p_cinnamon.product_tmpl_id.id,
            'compute_price': 'percentage',
            'base': 'list_price',
            'percent_price': 20.0,
        })
        # Exception (20%) beats category (10%) per standard rule order.
        self.assertPrice(self.pl_wholesale, self.p_cinnamon, 16.0)
        # Other product of the same category keeps the category discount.
        self.assertPrice(self.pl_wholesale, self.p_tea, 45.0)

    # ------------------------------------------------------------------
    # 4 — hierarchy: child rule wins over parent rule
    # ------------------------------------------------------------------
    def test_04_category_hierarchy(self):
        self.board.write_cells(self.cells(
            self.pl_wholesale, [(self.categ_food, 10.0)]))
        # Product sits in Food > Biscuits > Mini Classic -> parent applies.
        self.assertPrice(self.pl_wholesale, self.p_cinnamon, 18.0)

        self.board.write_cells(self.cells(
            self.pl_wholesale, [(self.categ_mini, 15.0)]))
        # Deeper (child) rule wins deterministically.
        self.assertPrice(self.pl_wholesale, self.p_cinnamon, 17.0)
        # Product in Biscuits (sibling branch) still uses the Food rule.
        self.assertPrice(self.pl_wholesale, self.p_choco, 90.0)

    # ------------------------------------------------------------------
    # 5 — Sale Order end-to-end (Ahmed Market scenario)
    # ------------------------------------------------------------------
    def test_05_sale_order_end_to_end(self):
        partner = self.env['res.partner'].create({
            'name': 'Ahmed Market',
            'property_product_pricelist': self.pl_wholesale.id,
        })
        self.board.write_cells(self.cells(
            self.pl_wholesale, [(self.categ_mini, 15.0)]))

        order = self.env['sale.order'].create({'partner_id': partner.id})
        line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.p_cinnamon.id,
            'product_uom_qty': 2.0,
        })
        self.assertEqual(order.pricelist_id, self.pl_wholesale)
        self.assertTrue(
            float_compare(line.price_unit, 17.0, precision_digits=2) == 0,
            f"Expected 17.0 on the sale order, got {line.price_unit}")

        # Change the matrix cell, then a NEW order must see the new price.
        self.board.write_cells(self.cells(
            self.pl_wholesale, [(self.categ_mini, 10.0)]))
        order2 = self.env['sale.order'].create({'partner_id': partner.id})
        line2 = self.env['sale.order.line'].create({
            'order_id': order2.id,
            'product_id': self.p_cinnamon.id,
            'product_uom_qty': 1.0,
        })
        self.assertTrue(
            float_compare(line2.price_unit, 18.0, precision_digits=2) == 0,
            f"Expected 18.0 after the matrix change, got {line2.price_unit}")
        # The first order keeps its price (standard behavior: no retroactive
        # recomputation).
        self.assertTrue(
            float_compare(line.price_unit, 17.0, precision_digits=2) == 0)

    # ------------------------------------------------------------------
    # 6 — cell edit updates the standard rule (update path + audit)
    # ------------------------------------------------------------------
    def test_06_update_and_audit(self):
        # Make the audit assertion order-independent: whatever previous
        # tests / prior runs left behind is cleared first, and the final
        # assertion is scoped to this exact (pricelist, category).
        self.env['price.matrix.change'].sudo().search([]).unlink()
        self.board.write_cells(self.cells(
            self.pl_wholesale, [(self.categ_mini, 15.0)]))
        item = self.pl_wholesale.item_ids
        self.board.write_cells(self.cells(
            self.pl_wholesale, [(self.categ_mini, 12.5)]))
        self.assertEqual(item.percent_price, 12.5)
        change = self.env['price.matrix.change'].search([
            ('operation', '=', 'update'),
            ('pricelist_id', '=', self.pl_wholesale.id),
            ('categ_id', '=', self.categ_mini.id),
        ])
        self.assertEqual(
            len(change), 1,
            f"Expected exactly 1 update log, got: "
            f"{change.read(['old_discount', 'new_discount', 'categ_id'])}")
        self.assertEqual(change.old_discount, 15.0)
        self.assertEqual(change.new_discount, 12.5)
        self.assertEqual(change.pricelist_id, self.pl_wholesale)
        self.assertEqual(change.categ_id, self.categ_mini)

    # ------------------------------------------------------------------
    # 7/8/9 — Excel import: consistent / inconsistent / tolerance
    # ------------------------------------------------------------------
    def _import_wizard(self, rows):
        wb = openpyxl.Workbook()
        ws = wb.active
        for row in rows:
            ws.append(row)
        buff = io.BytesIO()
        wb.save(buff)
        wizard = self.env['price.matrix.import'].create({
            'file': base64.b64encode(buff.getvalue()),
            'file_name': 'matrix.xlsx',
            'rounding_tolerance': 0.01,
        })
        return wizard

    def test_07_import_consistent_category(self):
        wizard = self._import_wizard([
            ['Barcode', 'Product Category', 'Base Price', 'Wholesale'],
            ['BC-CIN', 'Mini Classic', 20, 17.0],     # 15%
            ['BC-TEA', 'Mini Classic', 50, 42.5],     # 15%
        ])
        wizard.action_analyze()
        line = wizard.line_ids
        self.assertEqual(len(line), 1)
        self.assertTrue(line.is_consistent)
        self.assertTrue(float_compare(
            line.suggested_discount, 15.0, precision_digits=4) == 0)
        self.assertEqual(line.action, 'apply')
        self.assertEqual(wizard.products_matched, 2)
        wizard.action_apply()
        items = self.pl_wholesale.item_ids.filtered(
            lambda it: it.categ_id == self.categ_mini)
        self.assertEqual(len(items), 1)
        self.assertTrue(items.managed_by_matrix)
        self.assertTrue(float_compare(
            items.percent_price, 15.0, precision_digits=4) == 0)

    def test_08_import_inconsistent_category(self):
        wizard = self._import_wizard([
            ['Barcode', 'Product Category', 'Base Price', 'Wholesale'],
            ['BC-CIN', 'Mini Classic', 20, 17.0],     # 15%
            ['BC-TEA', 'Mini Classic', 50, 42.0],     # 16%
        ])
        wizard.action_analyze()
        line = wizard.line_ids
        self.assertFalse(line.is_consistent)
        self.assertEqual(line.action, 'skip')  # never guessed
        # Applying with nothing selected is refused...
        with self.assertRaises(UserError):
            wizard.action_apply()
        # ...and no rule is invented for the inconsistent category.
        self.assertFalse(self.pl_wholesale.item_ids.filtered(
            lambda it: it.categ_id == self.categ_mini))

    def test_09_import_rounding_tolerance(self):
        wizard = self._import_wizard([
            ['Barcode', 'Product Category', 'Base Price', 'Wholesale'],
            ['BC-CIN', 'Mini Classic', 20, 17.0001],  # 14.9995%
            ['BC-TEA', 'Mini Classic', 50, 42.4995],  # 15.001%
        ])
        wizard.action_analyze()
        line = wizard.line_ids
        self.assertTrue(line.is_consistent)
        self.assertTrue(float_is_zero(
            line.suggested_discount - 15.0, precision_digits=4))
        self.assertEqual(wizard.snapped_count, 1)

    # ------------------------------------------------------------------
    # 10 — manual rules are never touched
    # ------------------------------------------------------------------
    def test_10_manual_rule_protection(self):
        manual = self.env['product.pricelist.item'].create({
            'pricelist_id': self.pl_retail.id,
            'applied_on': '2_product_category',
            'categ_id': self.categ_biscuits.id,
            'compute_price': 'percentage',
            'base': 'list_price',
            'percent_price': 12.0,
        })
        # Editing an unrelated cell keeps the manual rule intact.
        self.board.write_cells(self.cells(
            self.pl_wholesale, [(self.categ_mini, 15.0)]))
        self.assertTrue(manual.exists())
        self.assertEqual(manual.percent_price, 12.0)

        # Clearing a managed cell deletes the managed rule only.
        self.board.write_cells(self.cells(
            self.pl_wholesale, [(self.categ_mini, None)]))
        self.assertTrue(manual.exists())
        self.assertFalse(self.pl_wholesale.item_ids)

        # Exact conflict: managed write is blocked without force...
        conflicting = self.cells(self.pl_retail, [(self.categ_biscuits, 8.0)])
        with self.assertRaises(UserError):
            self.board.write_cells(conflicting)
        self.assertEqual(manual.percent_price, 12.0)
        # ...and the manual rule still wins the tie-break without force
        # because it was NOT overwritten by the failed write.
        # With force both rules coexist (recent rule wins, standard order).
        self.board.write_cells(conflicting, force=True)
        self.assertTrue(manual.exists())

        # Product-level manual exception is untouched by bulk ops.
        exception = self.env['product.pricelist.item'].create({
            'pricelist_id': self.pl_wholesale.id,
            'applied_on': '1_product',
            'product_tmpl_id': self.p_choco.product_tmpl_id.id,
            'compute_price': 'fixed',
            'base': 'list_price',
            'fixed_price': 55.0,
        })
        self.board.bulk_fill(
            self.pl_wholesale.id,
            [self.categ_biscuits.id, self.categ_mini.id, self.categ_food.id],
            10.0)
        self.assertTrue(exception.exists())
        self.assertEqual(exception.fixed_price, 55.0)

    # ------------------------------------------------------------------
    # 11 — multi-company isolation
    # ------------------------------------------------------------------
    def test_11_multi_company(self):
        # A real (non-superuser) manager limited to its own companies:
        manager_board = self.board_as(self.manager)
        with self.assertRaises(AccessError):
            manager_board.write_cells(
                self.cells(self.pl_foreign, [(self.categ_mini, 15.0)]))

        # The board data never leaks another company's pricelist.
        manager_board = self.board_as(self.manager)
        data = manager_board.load_data()
        self.assertNotIn(self.pl_foreign.id,
                         [pl['id'] for pl in data['pricelists']])

        # A manager user can write on its own company's pricelist.
        manager_board.write_cells(
            self.cells(self.pl_wholesale, [(self.categ_mini, 15.0)]))
        self.assertEqual(len(self.pl_wholesale.item_ids), 1)

    # ------------------------------------------------------------------
    # 12 — bulk fill + copy column
    # ------------------------------------------------------------------
    def test_12_bulk_and_copy(self):
        result = self.board.bulk_fill(
            self.pl_wholesale.id,
            [self.categ_mini.id, self.categ_biscuits.id, self.categ_food.id],
            10.0)
        self.assertEqual(result['created'], 3)
        self.assertEqual(len(self.pl_wholesale.item_ids), 3)
        self.assertPrice(self.pl_wholesale, self.p_cinnamon, 18.0)

        # copy wholesale -> retail, only empty cells
        result = self.board.copy_column(
            self.pl_wholesale.id, self.pl_retail.id)
        self.assertEqual(result['copied'], 3)
        self.assertEqual(len(self.pl_retail.item_ids), 3)
        self.assertPrice(self.pl_retail, self.p_cinnamon, 18.0)

        # copying again overwrites nothing by default
        result = self.board.copy_column(
            self.pl_wholesale.id, self.pl_retail.id)
        self.assertEqual(result['copied'], 0)

    # ------------------------------------------------------------------
    # Extra guards
    # ------------------------------------------------------------------
    def test_13_permissions(self):
        user_board = self.board_as(self.basic_user)
        # a reader can load
        user_board.load_data()
        # ...but not write
        with self.assertRaises(AccessError):
            user_board.write_cells(
                self.cells(self.pl_wholesale, [(self.categ_mini, 5.0)]))
        with self.assertRaises(AccessError):
            user_board.bulk_fill(
                self.pl_wholesale.id, [self.categ_mini.id], 5.0)

    def test_14_duplicate_managed_rule_forbidden(self):
        self.board.write_cells(self.cells(
            self.pl_wholesale, [(self.categ_mini, 15.0)]))
        # The partial unique index fires at the SQL layer before the Python
        # constraint can (known Odoo pattern) — the raw IntegrityError is
        # expected for direct creates outside the board API.
        with self.assertRaises(pg_errors.UniqueViolation):
            self.env['product.pricelist.item'].create({
                'pricelist_id': self.pl_wholesale.id,
                'categ_id': self.categ_mini.id,
                'applied_on': '2_product_category',
                'compute_price': 'percentage',
                'base': 'list_price',
                'percent_price': 10.0,
                'managed_by_matrix': True,
            })

    def test_15_discount_bounds(self):
        with self.assertRaises(UserError):
            self.board.write_cells(self.cells(
                self.pl_wholesale, [(self.categ_mini, 120.0)]))
        with self.assertRaises(UserError):
            self.board.write_cells(self.cells(
                self.pl_wholesale, [(self.categ_mini, -5.0)]))

    def test_16_uninstall_leaves_pricing_data(self):
        """Managed rules survive as standard rules when the flag is gone."""
        self.board.write_cells(self.cells(
            self.pl_wholesale, [(self.categ_mini, 15.0)]))
        # Simulate the ORM uninstall: the ownership column is dropped, the
        # rules (pure standard percentage rules) must still price products.
        self.pl_wholesale.item_ids.write({'managed_by_matrix': False})
        self.assertPrice(self.pl_wholesale, self.p_cinnamon, 17.0)

    # ------------------------------------------------------------------
    # 17 — quantity tiers are standard category rules
    # ------------------------------------------------------------------
    def test_17_category_quantity_tiers(self):
        """Higher standard min_quantity wins; no custom pricing engine."""
        base = self.env['product.pricelist.item'].create({
            'pricelist_id': self.pl_wholesale.id,
            'categ_id': self.categ_mini.id,
            'applied_on': '2_product_category',
            'compute_price': 'percentage',
            'base': 'list_price',
            'percent_price': 5.0,
            'min_quantity': 0,
            'managed_by_matrix': True,
        })
        tier_10 = self.env['product.pricelist.item'].create({
            'pricelist_id': self.pl_wholesale.id,
            'categ_id': self.categ_mini.id,
            'applied_on': '2_product_category',
            'compute_price': 'percentage',
            'base': 'list_price',
            'percent_price': 10.0,
            'min_quantity': 10,
            'managed_by_matrix': True,
        })
        tier_50 = self.env['product.pricelist.item'].create({
            'pricelist_id': self.pl_wholesale.id,
            'categ_id': self.categ_mini.id,
            'applied_on': '2_product_category',
            'compute_price': 'percentage',
            'base': 'list_price',
            'percent_price': 15.0,
            'min_quantity': 50,
            'managed_by_matrix': True,
        })
        self.assertPrice(self.pl_wholesale, self.p_cinnamon, 19.0, 1)
        self.assertPrice(self.pl_wholesale, self.p_cinnamon, 18.0, 10)
        self.assertPrice(self.pl_wholesale, self.p_cinnamon, 17.0, 50)
        self.assertTrue(base.exists() and tier_10.exists() and tier_50.exists())

        with self.assertRaises(pg_errors.UniqueViolation):
            self.env['product.pricelist.item'].create({
                'pricelist_id': self.pl_wholesale.id,
                'categ_id': self.categ_mini.id,
                'applied_on': '2_product_category',
                'compute_price': 'percentage',
                'base': 'list_price',
                'percent_price': 20.0,
                'min_quantity': 10,
                'managed_by_matrix': True,
            })

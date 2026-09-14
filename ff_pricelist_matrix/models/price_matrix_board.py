# -*- coding: utf-8 -*-
import logging

from odoo import _, api, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)

MANAGED_APPLIED_ON = '2_product_category'
MANAGED_COMPUTE_PRICE = 'percentage'
MANAGED_BASE = 'list_price'

# cell dict keys coming from the client
CELL_KEYS = ('pricelist_id', 'categ_id', 'discount')


class PriceMatrixBoard(models.Model):
    """JSON API consumed by the OWL Price Matrix board.

    This model never stores records: it is the batch read/write surface over
    standard ``product.pricelist.item`` rules. The pricelist item remains the
    single source of truth for pricing; the board only upserts/removes rules
    it owns (``managed_by_matrix = True``).
    """

    _name = 'price.matrix.board'
    _description = 'Price Matrix Board (API)'

    # ------------------------------------------------------------------
    # Access helpers
    # ------------------------------------------------------------------
    def _ensure_user(self):
        if not self.env.su and not self.env.user.has_group(
                'ff_pricelist_matrix.group_price_matrix_user'):
            raise AccessError(_(
                "You need the \"Price Matrix / User\" access rights to view "
                "the Price Matrix board."))

    def _ensure_manager(self):
        self._ensure_user()
        if not self.env.su and not self.env.user.has_group(
                'ff_pricelist_matrix.group_price_matrix_manager'):
            raise AccessError(_(
                "Only Price Matrix Administrators can edit the matrix, run "
                "bulk operations or import Excel files."))

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    @api.model
    def load_data(self):
        """Return everything the board needs in one round trip.

        All queries are batched; no per-cell search is ever performed.
        """
        self._ensure_user()
        Item = self.env['product.pricelist.item'].sudo()
        companies = self.env.companies

        pricelists = self.env['product.pricelist'].sudo().search(
            [('company_id', 'in', companies.ids + [False])],
            order='sequence, id',
        )
        categories = self.env['product.category'].sudo().search(
            [], order='complete_name')

        items = Item.search([
            ('pricelist_id', 'in', pricelists.ids),
            ('applied_on', '=', MANAGED_APPLIED_ON),
        ])

        categ_parent_path = {c.id: c.parent_path for c in categories}

        def depth_of(categ):
            return max(len(categ.parent_path.rstrip('/').split('/')) - 1, 0)

        cells, manual_cells = [], []
        managed_item_ids = set()
        for item in items:
            entry = {
                'item_id': item.id,
                'pricelist_id': item.pricelist_id.id,
                'categ_id': item.categ_id.id,
                'discount': item.percent_price,
                'min_quantity': item.min_quantity,
                'label': item.name,
            }
            if item.managed_by_matrix and not item.min_quantity:
                cells.append(entry)
                managed_item_ids.add(item.id)
            else:
                entry['kind'] = 'qty_break' if item.min_quantity else 'manual'
                manual_cells.append(entry)

        # Ancestor coverage: a rule on an ancestor category also applies to
        # its descendants (standard parent_path matching). Surface those
        # ancestors so the UI can show where an inherited discount comes from.
        managed_ancestors = []
        everything = cells + manual_cells
        for cell in cells:
            path = categ_parent_path.get(cell['categ_id'], '')
            for other in everything:
                if (other is cell
                        or other['pricelist_id'] != cell['pricelist_id']):
                    continue
                other_path = categ_parent_path.get(other['categ_id'], '')
                if (other_path and other_path != path
                        and path.startswith(other_path)):
                    managed_ancestors.append({
                        'pricelist_id': cell['pricelist_id'],
                        'categ_id': cell['categ_id'],
                        'ancestor_categ_id': other['categ_id'],
                        'discount': other['discount'],
                        'managed': other['item_id'] in managed_item_ids,
                    })

        global_counts = {}
        if pricelists:
            global_counts = {
                row[0][0]: row[1]
                for row in Item._read_group(
                    domain=[
                        ('pricelist_id', 'in', pricelists.ids),
                        ('applied_on', '=', '3_global'),
                    ],
                    groupby=['pricelist_id'],
                    aggregates=['__count'],
                )
            }

        feature_enabled = self.env.user.has_group(
            'product.group_product_pricelist')

        return {
            'feature_enabled': feature_enabled,
            'pricelists': [{
                'id': pl.id,
                'name': pl.name,
                'currency_symbol': pl.currency_id.symbol or '',
                'company_id': pl.company_id.id,
            } for pl in pricelists],
            'categories': [{
                'id': c.id,
                'name': c.name,
                'complete_name': c.complete_name,
                'parent_id': c.parent_id.id,
                'depth': depth_of(c),
            } for c in categories],
            'cells': cells,
            'manual_cells': manual_cells,
            'managed_ancestors': managed_ancestors,
            'global_counts': global_counts,
        }

    # ------------------------------------------------------------------
    # Write — batch upsert
    # ------------------------------------------------------------------
    @api.model
    def write_cells(self, cells, force=False, source='board'):
        """Upsert/delete a batch of matrix cells.

        :param cells: list of {'pricelist_id': int, 'categ_id': int,
            'discount': float | None} — ``None`` removes the managed rule.
        :param force: allow writing cells where a manual rule targets the
            exact same (pricelist, category) with no quantity break.
        :param source: audit source tag ('board' | 'import' | 'bulk').
        """
        self._ensure_manager()
        if not isinstance(cells, list) or not cells:
            return {'created': 0, 'updated': 0, 'deleted': 0}

        Item = self.env['product.pricelist.item'].sudo().with_context(
            ff_pm_source=source)
        companies = self.env.companies

        pricelist_ids, categ_ids = set(), set()
        normalized = []
        for cell in cells:
            if not all(k in cell for k in CELL_KEYS):
                raise UserError(_("Malformed cell payload: %s", cell))
            pl_id = int(cell['pricelist_id'])
            categ_id = int(cell['categ_id'])
            discount = cell['discount']
            if discount is not None:
                try:
                    discount = float(discount)
                except (TypeError, ValueError):
                    raise UserError(_(
                        "Invalid discount value for cell: %s", cell)) from None
                if discount < 0.0 or discount > 100.0:
                    raise UserError(_(
                        "Discount must be between 0 and 100 (got %s).",
                        discount))
            pricelist_ids.add(pl_id)
            categ_ids.add(categ_id)
            normalized.append((pl_id, categ_id, discount))

        pricelists = self.env['product.pricelist'].sudo().browse(
            pricelist_ids).exists()
        for pricelist in pricelists:
            if pricelist.company_id and pricelist.company_id not in companies:
                raise AccessError(_(
                    "Pricelist \"%s\" belongs to another company.",
                    pricelist.display_name))
        categories = self.env['product.category'].sudo().browse(
            categ_ids).exists()
        if len(categories) < len(categ_ids):
            raise UserError(_("Some product categories no longer exist."))
        if len(pricelists) < len(pricelist_ids):
            raise UserError(_("Some pricelists no longer exist."))

        existing = Item.search([
            ('managed_by_matrix', '=', True),
            ('pricelist_id', 'in', list(pricelist_ids)),
            ('categ_id', 'in', list(categ_ids)),
        ])
        existing_map = {
            (it.pricelist_id.id, it.categ_id.id): it for it in existing
        }

        to_create, to_write, to_unlink = [], [], []
        for pl_id, categ_id, discount in normalized:
            item = existing_map.get((pl_id, categ_id))
            if item is None and discount is not None:
                to_create.append((pl_id, categ_id, discount))
            elif item is not None and discount is None:
                to_unlink.append(item)
            elif (item is not None and discount is not None
                    and float_compare(item.percent_price, discount,
                                      precision_digits=4) != 0):
                to_write.append((item, discount))

        # Deterministic-behavior guard: a manual rule on the exact same
        # (pricelist, category) without quantity break makes the winning rule
        # depend on creation order (standard tie-break is `id desc`).
        # Block unless the user explicitly forces the write.
        if not force and (to_create or to_write):
            target_pairs = {(pl, cat) for pl, cat, _d in to_create}
            target_pairs |= {(it.pricelist_id.id, it.categ_id.id)
                             for it, _d in to_write}
            blocking = Item.search([
                ('managed_by_matrix', '=', False),
                ('applied_on', '=', MANAGED_APPLIED_ON),
                ('min_quantity', '=', 0),
                ('pricelist_id', 'in', [pl for pl, _c in target_pairs]),
                ('categ_id', 'in', [cat for _pl, cat in target_pairs]),
            ])
            blocking = [
                it for it in blocking
                if (it.pricelist_id.id, it.categ_id.id) in target_pairs
            ]
            if blocking:
                raise UserError(_(
                    "Manual rule(s) already target the same pricelist and "
                    "category:\n%s\n"
                    "Remove or convert them first, or save again with "
                    "\"Force\" to keep both (the most recently modified rule "
                    "would then win).",
                    "\n".join(f"• {it.display_name}"
                              for it in blocking[:10]),
                ))

        if to_create:
            # NOTE: `name` is a non-stored computed field on
            # product.pricelist.item in Odoo 19 — never pass it in vals.
            Item.create([{
                'pricelist_id': pl_id,
                'categ_id': categ_id,
                'applied_on': MANAGED_APPLIED_ON,
                'compute_price': MANAGED_COMPUTE_PRICE,
                'base': MANAGED_BASE,
                'percent_price': discount,
                'min_quantity': 0,
                'managed_by_matrix': True,
            } for pl_id, categ_id, discount in to_create])

        for item, discount in to_write:
            item.write({'percent_price': discount})

        if to_unlink:
            Item.browse([it.id for it in to_unlink]).unlink()

        return {
            'created': len(to_create),
            'updated': len(to_write),
            'deleted': len(to_unlink),
        }

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------
    @api.model
    def bulk_fill(self, pricelist_id, categ_ids, discount,
                  force=False, overwrite_existing=True):
        """Set one discount on (one pricelist x N categories)."""
        self._ensure_manager()
        pricelist = self.env['product.pricelist'].sudo().browse(pricelist_id)
        if not pricelist.exists():
            raise UserError(_("Pricelist not found."))
        categs = self.env['product.category'].sudo().browse(
            categ_ids).exists()
        if not categs:
            raise UserError(_("Select at least one category."))
        if not overwrite_existing:
            existing = self.env['product.pricelist.item'].sudo().search([
                ('managed_by_matrix', '=', True),
                ('pricelist_id', '=', pricelist.id),
                ('categ_id', 'in', categs.ids),
            ])
            skip = {(it.pricelist_id.id, it.categ_id.id) for it in existing}
        else:
            skip = set()
        cells = [
            {'pricelist_id': pricelist.id, 'categ_id': categ.id,
             'discount': discount}
            for categ in categs if (pricelist.id, categ.id) not in skip
        ]
        result = self.write_cells(cells, force=force, source='bulk')
        result['requested'] = len(categs)
        return result

    @api.model
    def copy_column(self, source_pricelist_id, target_pricelist_id,
                    overwrite_existing=False, force=False):
        """Copy the discounts of one pricelist column into another one.

        By default only empty target cells are filled, so target-specific
        discounts are preserved and the user then edits the differences.
        """
        self._ensure_manager()
        Pricelist = self.env['product.pricelist'].sudo()
        source = Pricelist.browse(source_pricelist_id)
        target = Pricelist.browse(target_pricelist_id)
        if not source.exists() or not target.exists():
            raise UserError(_("Source or target pricelist not found."))
        if source.id == target.id:
            raise UserError(_("Source and target pricelists must differ."))
        for pricelist in (source, target):
            if (pricelist.company_id
                    and pricelist.company_id not in self.env.companies):
                raise AccessError(_(
                    "Pricelist \"%s\" belongs to another company.",
                    pricelist.display_name))

        source_items = self.env['product.pricelist.item'].sudo().search([
            ('managed_by_matrix', '=', True),
            ('pricelist_id', '=', source.id),
        ])
        cells = [
            {'pricelist_id': target.id, 'categ_id': it.categ_id.id,
             'discount': it.percent_price}
            for it in source_items
        ]
        if not overwrite_existing and cells:
            existing = self.env['product.pricelist.item'].sudo().search([
                ('managed_by_matrix', '=', True),
                ('pricelist_id', '=', target.id),
                ('categ_id', 'in', [c['categ_id'] for c in cells]),
            ])
            skip = {it.categ_id.id for it in existing}
            cells = [c for c in cells if c['categ_id'] not in skip]
        result = self.write_cells(cells, force=force, source='bulk')
        # report actual changes, not inspected cells
        result['copied'] = result['created'] + result['updated']
        return result

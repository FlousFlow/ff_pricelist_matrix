# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare

MANAGED_APPLIED_ON = '2_product_category'
MANAGED_COMPUTE_PRICE = 'percentage'
MANAGED_BASE = 'list_price'


class ProductPricelistItem(models.Model):
    _inherit = 'product.pricelist.item'

    # ------------------------------------------------------------------
    # Ownership marker
    # ------------------------------------------------------------------
    managed_by_matrix = fields.Boolean(
        string="Managed by Price Matrix",
        default=False,
        index=True,
        copy=False,
        help="Technical flag: this rule was created/updated by the Price "
             "Matrix board. The board only ever touches rules carrying this "
             "flag; manual rules are never modified or deleted.",
    )

    # ------------------------------------------------------------------
    # Audit: managed rules log every change, whatever the UI used
    # (standard matrix list, import wizard or API). The source recorded
    # comes from the `ff_pm_source` context key ('board' by default).
    # ------------------------------------------------------------------
    def _matrix_audit(self, operation, old, new):
        self.ensure_one()
        self.env['price.matrix.change'].sudo().create({
            'pricelist_id': self.pricelist_id.id,
            'categ_id': self.categ_id.id,
            'operation': operation,
            'old_discount': old,
            'new_discount': new,
            'source': self.env.context.get('ff_pm_source', 'board'),
            'user_id': self.env.user.id,
        })

    def write(self, vals):
        audit = []
        if 'percent_price' in vals:
            audit = [
                (item.id, item.percent_price)
                for item in self.filtered('managed_by_matrix')
                if float_compare(item.percent_price, vals['percent_price'],
                                 precision_digits=4) != 0
            ]
        res = super().write(vals)
        Item = self.env['product.pricelist.item']
        for item_id, old in audit:
            Item.browse(item_id)._matrix_audit(
                'update', old, vals['percent_price'])
        return res

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records.filtered('managed_by_matrix'):
            record._matrix_audit('create', 0.0, record.percent_price)
        return records

    def unlink(self):
        for item in self.filtered('managed_by_matrix'):
            item._matrix_audit('delete', item.percent_price, 0.0)
        return super().unlink()

    # ------------------------------------------------------------------
    # Managed scope invariants
    # ------------------------------------------------------------------
    @api.constrains('managed_by_matrix', 'applied_on', 'compute_price',
                    'base', 'min_quantity', 'date_start', 'date_end', 'categ_id')
    def _check_matrix_managed_scope(self):
        """A managed rule is, by design, a pure category discount:

            applied_on = '2_product_category' + compute_price = 'percentage'
            + base = 'list_price' + min_quantity = 0 + no date range.

        Keeping the scope narrow is what makes matrix behavior deterministic
        (standard first-match order: applied_on, min_quantity desc, categ_id
        desc, id desc) and prevents the board from shadowing or being
        shadowed by quantity breaks / date windows.
        """
        for item in self:
            if not item.managed_by_matrix:
                continue
            if item.applied_on != MANAGED_APPLIED_ON:
                raise ValidationError(_(
                    "Price Matrix rules must apply on a Product Category."))
            if item.compute_price != MANAGED_COMPUTE_PRICE:
                raise ValidationError(_(
                    "Price Matrix rules must compute the price as a "
                    "percentage discount."))
            if item.base != MANAGED_BASE:
                raise ValidationError(_(
                    "Price Matrix rules must be based on the Sales Price."))
            if not item.categ_id:
                raise ValidationError(_(
                    "Price Matrix rules require a product category."))
            if item.min_quantity:
                raise ValidationError(_(
                    "Price Matrix rules cannot define a minimum quantity."))
            if item.date_start or item.date_end:
                raise ValidationError(_(
                    "Price Matrix rules cannot define a date range."))

    @api.constrains('managed_by_matrix', 'pricelist_id', 'categ_id')
    def _check_matrix_managed_uniq(self):
        """Prevent two managed rules for the same (pricelist, category).

        Standard Odoo tolerates duplicate rules on purpose (date ranges,
        quantity breaks...), so the restriction applies to the managed scope
        only. A partial unique index created in post_init_hook is the
        concurrency safety net; this constraint gives a friendly message.
        """
        managed = self.filtered('managed_by_matrix')
        if not managed:
            return
        groups = self.sudo()._read_group(
            domain=[
                ('managed_by_matrix', '=', True),
                ('pricelist_id', 'in', managed.pricelist_id.ids),
                ('categ_id', 'in', managed.categ_id.ids),
            ],
            groupby=['pricelist_id', 'categ_id'],
            aggregates=['__count'],
            having=[('__count', '>', 1)],
        )
        if groups:
            raise ValidationError(_(
                "Only one Price Matrix rule is allowed per pricelist and "
                "product category.\nDuplicate(s): %s",
                ", ".join(
                    f"{pricelist.display_name} / {categ.display_name}"
                    for pricelist, categ, _count in groups
                ),
            ))

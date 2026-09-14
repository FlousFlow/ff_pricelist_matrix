# -*- coding: utf-8 -*-
from odoo import fields, models


class PriceMatrixChange(models.Model):
    _name = 'price.matrix.change'
    _description = 'Price Matrix Change Log'
    _order = 'id desc'

    item_id = fields.Many2one(
        comodel_name='product.pricelist.item',
        string="Pricelist Rule",
        ondelete='set null',
        index='btree_not_null',
    )
    pricelist_id = fields.Many2one(
        comodel_name='product.pricelist',
        string="Pricelist",
        ondelete='cascade',
        index=True,
    )
    categ_id = fields.Many2one(
        comodel_name='product.category',
        string="Product Category",
        ondelete='cascade',
        index=True,
    )
    company_id = fields.Many2one(related='pricelist_id.company_id', store=True)
    operation = fields.Selection(
        selection=[
            ('create', "Created"),
            ('update', "Updated"),
            ('delete', "Removed"),
        ],
        required=True,
    )
    old_discount = fields.Float(string="Old Discount %", digits='Discount')
    new_discount = fields.Float(string="New Discount %", digits='Discount')
    user_id = fields.Many2one(
        comodel_name='res.users',
        string="Changed By",
        default=lambda self: self.env.user,
        index=True,
    )
    source = fields.Selection(
        selection=[
            ('board', "Matrix Board"),
            ('import', "Excel Import"),
            ('bulk', "Bulk Operation"),
        ],
        default='board',
        required=True,
    )

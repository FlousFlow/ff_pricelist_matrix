# -*- coding: utf-8 -*-
from odoo import Command


def post_init_hook(env):
    """Post-installation setup.

    1. Enable the "Pricelists" feature for existing internal users. In Odoo 19
       the pricelist engine is gated behind ``product.group_product_pricelist``
       (Settings > Sales > Pricing > Pricelists). A module whose whole purpose
       is pricelist management must have that feature on; adding the group to
       internal users is exactly what toggling the setting ON does.
       (Toggling it off later remains possible and simply archives pricelists.)

    2. Create a partial unique index protecting the managed scope from
       duplicates (same pricelist + category), even under concurrent edits.
       It is a raw index (not models.Constraint) because the uniqueness only
       applies to matrix-managed rules; standard Odoo allows duplicate rules
       for date ranges / quantity breaks and we must not restrict that.
       The index is dropped automatically when the column is removed on
       uninstall.
    """
    group_user = env.ref('base.group_user')
    pricelist_group = env.ref('product.group_product_pricelist')
    # Odoo 19: the res.users groups field is `group_ids`
    users = env['res.users'].search([('group_ids', 'in', group_user.ids)])
    if pricelist_group not in users.group_ids:
        users.write({'group_ids': [Command.link(pricelist_group.id)]})

    env.cr.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ff_price_matrix_managed_uniq_idx
        ON product_pricelist_item (pricelist_id, categ_id)
        WHERE managed_by_matrix AND applied_on = '2_product_category'
        """
    )
    env.cr.commit()

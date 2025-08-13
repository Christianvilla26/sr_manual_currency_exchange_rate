# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) Sitaram Solutions (<https://sitaramsolutions.in/>).
#
#    For Module Support : info@sitaramsolutions.in  or Skype : contact.hiren1188
#
##############################################################################

from odoo import models, fields, api, _
from odoo.tools.float_utils import float_compare, float_is_zero, float_round
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT, format_amount, format_date, formatLang, get_lang, groupby


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    apply_manual_currency_exchange = fields.Boolean(string='Apply Manual Currency Exchange')
    manual_currency_exchange_rate = fields.Float(string='Manual Currency Exchange Rate',digits='Manual Currency')
    active_manual_currency_rate = fields.Boolean('active Manual Currency', default=False)

    @api.depends('date_order', 'currency_id', 'company_id', 'company_id.currency_id')
    def _compute_currency_rate(self):
        for order in self:
            if order.apply_manual_currency_exchange:
                order.currency_rate = order.manual_currency_exchange_rate
            else:
                order.currency_rate = self.env['res.currency']._get_conversion_rate(order.company_id.currency_id,
                                                                                    order.currency_id, order.company_id,
                                                                                    order.date_order)

    @api.onchange('company_id','currency_id')
    def onchange_currency_id(self):
        if self.company_id or self.currency_id:
            if self.company_id.currency_id != self.currency_id:
                self.active_manual_currency_rate = True
            else:
                self.active_manual_currency_rate = False
        else:
            self.active_manual_currency_rate = False

    def _prepare_invoice(self):
        res = super(PurchaseOrder, self)._prepare_invoice()
        res.update({
            'apply_manual_currency_exchange':self.apply_manual_currency_exchange,
            'manual_currency_exchange_rate':self.manual_currency_exchange_rate,
            'active_manual_currency_rate':self.active_manual_currency_rate
            })
        return res


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    def _convert_to_tax_base_line_dict(self):
        """ Convert the current record to a dictionary in order to use the generic taxes computation method
        defined on account.tax.

        :return: A python dictionary.
        """
        self.ensure_one()
        if self.order_id.active_manual_currency_rate:
            self = self.with_context(
                manual_rate=self.order_id.manual_currency_exchange_rate,
                active_manutal_currency=self.order_id.apply_manual_currency_exchange,
            )

        return self.env['account.tax']._convert_to_tax_base_line_dict(
            self,
            partner=self.order_id.partner_id,
            currency=self.order_id.currency_id,
            product=self.product_id,
            taxes=self.taxes_id,
            price_unit=self.price_unit,
            quantity=self.product_qty,
            discount=self.discount,
            price_subtotal=self.price_subtotal,
        )
        # return self.env['account.tax']._convert_to_tax_base_line_dict(
        #     self,
        #     partner=self.order_id.partner_id,
        #     currency=self.order_id.currency_id,
        #     product=self.product_id,
        #     taxes=self.taxes_id,
        #     price_unit=self.price_unit,
        #     quantity=self.product_qty,
        #     price_subtotal=self.price_subtotal,
        # )

    @api.onchange('product_id')
    def onchange_product_id(self):
        if self.order_id.active_manual_currency_rate:
            self = self.with_context(
                manual_rate=self.order_id.manual_currency_exchange_rate,
                active_manutal_currency=self.order_id.apply_manual_currency_exchange,
            )
        return super(PurchaseOrderLine, self).onchange_product_id()

    @api.depends('product_qty', 'product_uom', 'company_id')
    def _compute_price_unit_and_date_planned_and_name(self):
        for line in self:
            if not line.product_id or line.invoice_lines or not line.company_id:
                continue
            params = line._get_select_sellers_params()
            seller = line.product_id._select_seller(
                partner_id=line.partner_id,
                quantity=line.product_qty,
                date=line.order_id.date_order and line.order_id.date_order.date() or fields.Date.context_today(line),
                uom_id=line.product_uom,
                params=params)

            if seller or not line.date_planned:
                line.date_planned = line._get_date_planned(seller).strftime(DEFAULT_SERVER_DATETIME_FORMAT)

            # If not seller, use the standard price. It needs a proper currency conversion.
            if not seller:
                line.discount = 0
                unavailable_seller = line.product_id.seller_ids.filtered(
                    lambda s: s.partner_id == line.order_id.partner_id)
                if not unavailable_seller and line.price_unit and line.product_uom == line._origin.product_uom:
                    # Avoid to modify the price unit if there is no price list for this partner and
                    # the line has already one to avoid to override unit price set manually.
                    continue
                po_line_uom = line.product_uom or line.product_id.uom_po_id
                price_unit = line.env['account.tax']._fix_tax_included_price_company(
                    line.product_id.uom_id._compute_price(line.product_id.standard_price, po_line_uom),
                    line.product_id.supplier_taxes_id,
                    line.taxes_id,
                    line.company_id,
                )
                if line.order_id.apply_manual_currency_exchange:
                    price_unit = line.product_id.standard_price * line.order_id.manual_currency_exchange_rate
                else:
                    price_unit = line.product_id.cost_currency_id._convert(
                        price_unit,
                        line.currency_id,
                        line.company_id,
                        line.date_order or fields.Date.context_today(line),
                        False
                    )
                line.price_unit = float_round(price_unit, precision_digits=max(line.currency_id.decimal_places,
                                                                               self.env[
                                                                                   'decimal.precision'].precision_get(
                                                                                   'Product Price')))

            elif seller:
                price_unit = line.env['account.tax']._fix_tax_included_price_company(seller.price,
                                                                                     line.product_id.supplier_taxes_id,
                                                                                     line.taxes_id,
                                                                                     line.company_id) if seller else 0.0
                if line.order_id.apply_manual_currency_exchange:
                    price_unit = price_unit * line.order_id.manual_currency_exchange_rate
                else:
                    price_unit = seller.currency_id._convert(price_unit, line.currency_id, line.company_id,
                                                             line.date_order or fields.Date.context_today(line), False)
                price_unit = float_round(price_unit, precision_digits=max(line.currency_id.decimal_places,
                                                                          self.env['decimal.precision'].precision_get(
                                                                              'Product Price')))
                line.price_unit = seller.product_uom._compute_price(price_unit, line.product_uom)
                line.discount = seller.discount or 0.0

            # record product names to avoid resetting custom descriptions
            default_names = []
            vendors = line.product_id._prepare_sellers({})
            product_ctx = {'seller_id': None, 'partner_id': None, 'lang': get_lang(line.env, line.partner_id.lang).code}
            default_names.append(line._get_product_purchase_description(line.product_id.with_context(product_ctx)))
            for vendor in vendors:
                product_ctx = {'seller_id': vendor.id, 'lang': get_lang(line.env, line.partner_id.lang).code}
                default_names.append(line._get_product_purchase_description(line.product_id.with_context(product_ctx)))
            if not line.name or line.name in default_names:
                product_ctx = {'seller_id': seller.id, 'lang': get_lang(line.env, line.partner_id.lang).code}
                line.name = line._get_product_purchase_description(line.product_id.with_context(product_ctx))

    # @api.depends('product_qty', 'product_uom', 'company_id')
    # def _compute_price_unit_and_date_planned_and_name(self):
    #     for line in self:
    #         if not line.product_id or line.invoice_lines or not line.company_id:
    #             continue
    #         params = {'order_id': line.order_id}
    #         seller = line.product_id._select_seller(
    #             partner_id=line.partner_id,
    #             quantity=line.product_qty,
    #             date=line.order_id.date_order and line.order_id.date_order.date() or fields.Date.context_today(line),
    #             uom_id=line.product_uom,
    #             params=params)
    #
    #         if seller or not line.date_planned:
    #             line.date_planned = line._get_date_planned(seller).strftime(DEFAULT_SERVER_DATETIME_FORMAT)
    #
    #         # If not seller, use the standard price. It needs a proper currency conversion.
    #         if not seller:
    #             unavailable_seller = line.product_id.seller_ids.filtered(
    #                 lambda s: s.partner_id == line.order_id.partner_id)
    #             if not unavailable_seller and line.price_unit and line.product_uom == line._origin.product_uom:
    #                 # Avoid to modify the price unit if there is no price list for this partner and
    #                 # the line has already one to avoid to override unit price set manually.
    #                 continue
    #             po_line_uom = line.product_uom or line.product_id.uom_po_id
    #             if line.order_id.apply_manual_currency_exchange:
    #                 price_unit = line.product_id.standard_price * line.order_id.manual_currency_exchange_rate
    #             else:
    #                 price_unit = line.env['account.tax']._fix_tax_included_price_company(
    #                     line.product_id.uom_id._compute_price(line.product_id.standard_price, po_line_uom),
    #                     line.product_id.supplier_taxes_id,
    #                     line.taxes_id,
    #                     line.company_id,
    #                 )
    #                 price_unit = line.product_id.cost_currency_id._convert(
    #                     price_unit,
    #                     line.currency_id,
    #                     line.company_id,
    #                     line.date_order or fields.Date.context_today(line),
    #                     False
    #                 )
    #             line.price_unit = float_round(price_unit, precision_digits=max(line.currency_id.decimal_places,
    #                                                                            self.env[
    #                                                                                'decimal.precision'].precision_get(
    #                                                                                'Product Price')))
    #             continue
    #
    #         price_unit = line.env['account.tax']._fix_tax_included_price_company(seller.price,
    #                                                                              line.product_id.supplier_taxes_id,
    #                                                                              line.taxes_id,
    #                                                                              line.company_id) if seller else 0.0
    #         if line.order_id.apply_manual_currency_exchange:
    #             price_unit = price_unit * line.order_id.manual_currency_exchange_rate
    #         else:
    #             price_unit = seller.currency_id._convert(price_unit, line.currency_id, line.company_id,
    #                                                  line.date_order or fields.Date.context_today(line), False)
    #         price_unit = float_round(price_unit, precision_digits=max(line.currency_id.decimal_places,
    #                                                                   self.env['decimal.precision'].precision_get(
    #                                                                       'Product Price')))
    #         line.price_unit = seller.product_uom._compute_price(price_unit, line.product_uom)
    #
    #         # record product names to avoid resetting custom descriptions
    #         default_names = []
    #         vendors = line.product_id._prepare_sellers({})
    #         for vendor in vendors:
    #             product_ctx = {'seller_id': vendor.id, 'lang': get_lang(line.env, line.partner_id.lang).code}
    #             default_names.append(line._get_product_purchase_description(line.product_id.with_context(product_ctx)))
    #         if not line.name or line.name in default_names:
    #             product_ctx = {'seller_id': seller.id, 'lang': get_lang(line.env, line.partner_id.lang).code}
    #             line.name = line._get_product_purchase_description(line.product_id.with_context(product_ctx))

    def _prepare_stock_move_vals(self, picking, price_unit, product_uom_qty, product_uom):
        if self.order_id.apply_manual_currency_exchange and self.order_id.manual_currency_exchange_rate > 0 and self.price_unit > 0:
            price_unit = self.price_unit * self.order_id.manual_currency_exchange_rate
        return super(PurchaseOrderLine,self)._prepare_stock_move_vals(picking,price_unit,product_uom_qty,product_uom)

class StockMove(models.Model):
    _inherit = 'stock.move'

    def _get_price_unit(self):
        price = super(StockMove, self)._get_price_unit()
        if self.picking_id.purchase_id and self.picking_id.purchase_id.apply_manual_currency_exchange:
            price = self.price_unit
        return price

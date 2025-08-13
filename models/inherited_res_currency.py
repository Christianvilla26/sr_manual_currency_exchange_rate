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


class ResCurrency(models.Model):
    _inherit = 'res.currency'

    # @api.model
    # def _get_conversion_rate(self, from_currency, to_currency, company, date):
    #     currency_rates = (from_currency + to_currency)._get_rates(company, date)
    #     if self._context.get('active_manutal_currency'):
    #         res = self._context.get('manual_rate')
    #     else:
    #         res = currency_rates.get(to_currency.id) / currency_rates.get(from_currency.id)
    #     return res

    @api.model
    def _get_conversion_rate(self, from_currency, to_currency, company=None, date=None):
        if from_currency == to_currency:
            return 1
        company = company or self.env.company
        date = date or fields.Date.context_today(self)

        if self._context.get('active_manutal_currency'):
            res = self._context.get('manual_rate')
        else:
            res = from_currency.with_company(company).with_context(to_currency=to_currency.id, date=str(date)).inverse_rate
        return res

    # def _convert(self, from_amount, to_currency, company, date, round=True):
    def _convert(self, from_amount, to_currency, company=None, date=None, round=True):
        """Returns the converted amount of ``from_amount``` from the currency
           ``self`` to the currency ``to_currency`` for the given ``date`` and
           company.

           :param company: The company from which we retrieve the convertion rate
           :param date: The nearest date from which we retriev the conversion rate.
           :param round: Round the result or not
        """
        self, to_currency = self or to_currency, to_currency or self
        assert self, "convert amount from unknown currency"
        assert to_currency, "convert amount to unknown currency"
        # apply conversion rate
        if from_amount:
            if self._context.get('cus_active_manutal_currency'):
                to_amount = from_amount * self._context.get('cus_manual_rate')
            elif self._context.get('diff_active_manutal_currency'):
                to_amount = from_amount * self._context.get('diff_manual_rate')
            else:
                to_amount = from_amount * self._get_conversion_rate(self, to_currency, company, date)
        else:
            return 0.0

        # apply rounding
        return to_currency.round(to_amount) if round else to_amount

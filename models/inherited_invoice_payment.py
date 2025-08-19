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
from odoo.exceptions import ValidationError


class AccountPayments(models.Model):
    _inherit = 'account.payment'
    # rhodetech custom fields
    journal_current_balance = fields.Monetary(
        string="Saldo Actual del Diario",
        compute='_compute_journal_current_balance',
        help="Muestra el saldo actual de la cuenta de liquidez asociada a este diario."
    )
    
    can_confirm_payment = fields.Boolean(
        string="Can Confirm Payment",
        compute='_compute_can_confirm_payment',
        help="Indicates if the payment can be confirmed based on journal balance"
    )
    
    payment_button_state = fields.Selection([
        ('normal', 'Normal'),
        ('disabled', 'Disabled'),
        ('warning', 'Warning')
    ], string="Payment Button State", compute='_compute_payment_button_state', default='normal')

    # rhodetech custom fields
    apply_manual_currency_exchange = fields.Boolean(
        string='Apply Manual Currency Exchange')
    manual_currency_exchange_rate = fields.Float(
        string='Manual Currency Exchange Rate',
        digits=(16, 6))
    active_manual_currency_rate = fields.Boolean(
        'active Manual Currency', default=False)

    @api.depends('journal_id', 'date')
    def _compute_journal_current_balance(self):
        """
        Calcula el saldo del diario al momento de la fecha del pago,
        obteniendo el saldo directamente desde la cuenta contable asociada.
        """
        for payment in self:
            payment.journal_current_balance = 0.0
            
            # Continuamos solo si es un diario de banco o efectivo
            if payment.journal_id and payment.journal_id.type in ('bank', 'cash'):
                # Obtenemos la cuenta de liquidez por defecto del diario.
                # 'default_account_id' es el nombre estándar para este campo.
                account = payment.journal_id.default_account_id
                
                # Si la cuenta está configurada, calculamos el saldo hasta la fecha del pago
                if account and payment.date:
                    # Calculamos el saldo hasta la fecha del pago
                    # Sumamos todos los débitos y créditos hasta esa fecha
                    domain = [
                        ('account_id', '=', account.id),
                        ('date', '<=', payment.date),
                        ('move_id.state', '=', 'posted')
                    ]
                    
                    # Calculamos el saldo: débitos - créditos
                    debit_sum = sum(self.env['account.move.line'].search(domain).mapped('debit'))
                    credit_sum = sum(self.env['account.move.line'].search(domain).mapped('credit'))
                    
                    payment.journal_current_balance = debit_sum - credit_sum

    @api.depends('journal_id', 'amount', 'journal_current_balance')
    def _compute_can_confirm_payment(self):
        """
        Determina si el pago puede ser confirmado basándose en el saldo del diario
        """
        for payment in self:
            payment.can_confirm_payment = True
            
            # Solo validamos para pagos de salida (outbound) y diarios de banco/efectivo
            if (payment.payment_type == 'outbound' and 
                payment.journal_id and 
                payment.journal_id.type in ('bank', 'cash') and
                payment.amount and payment.journal_current_balance and 
                payment.state == 'draft'):
                
                # Si el monto del pago es mayor que el saldo disponible, no se puede confirmar
                if payment.amount > payment.journal_current_balance:
                    payment.can_confirm_payment = False

    @api.depends('can_confirm_payment', 'payment_type', 'journal_id', 'amount')
    def _compute_payment_button_state(self):
        """
        Calcula el estado del botón de confirmación del pago
        """
        for payment in self:
            if not payment.can_confirm_payment and payment.payment_type == 'outbound':
                payment.payment_button_state = 'disabled'
            elif not payment.can_confirm_payment:
                payment.payment_button_state = 'warning'
            else:
                payment.payment_button_state = 'normal'

    def _validate_journal_balance(self):
        """
        Valida que el diario tenga saldo suficiente para realizar el pago
        """
        self.ensure_one()
        
        # Solo validamos para pagos de salida (outbound) y diarios de banco/efectivo
        if (self.payment_type == 'outbound' and 
            self.journal_id and 
            self.journal_id.type in ('bank', 'cash') and
            self.amount and self.journal_current_balance):
            
            # Si el monto del pago es mayor que el saldo disponible, lanzamos error
            if self.amount > self.journal_current_balance:
                raise ValidationError(_(
                    "No se puede confirmar el pago. El monto del pago (%.2f) "
                    "excede el saldo disponible en el diario (%.2f)."
                ) % (self.amount, self.journal_current_balance))

    def action_post(self):
        # Validate journal balance before posting payment
        for payment in self:
            payment._validate_journal_balance()
        
        return super(AccountPayments, self).action_post()

    def _get_confirm_button_attrs(self):
        """
        Retorna los atributos del botón de confirmación basándose en la validación
        """
        self.ensure_one()
        attrs = {}
        
        if not self.can_confirm_payment:
            attrs['invisible'] = True
            attrs['readonly'] = True
        
        return attrs

    @api.onchange('currency_id')
    def onchange_currency_id(self):
        if self.currency_id:
            if self.company_id.currency_id != self.currency_id:
                self.active_manual_currency_rate = True
            else:
                self.active_manual_currency_rate = False
        else:
            self.active_manual_currency_rate = False

    def _prepare_move_line_default_vals(self, write_off_line_vals=None, force_balance=None):
        ''' Prepare the dictionary to create the default account.move.lines for the current payment.
        :param write_off_line_vals: Optional dictionary to create a write-off account.move.line easily containing:
            * amount:       The amount to be added to the counterpart amount.
            * name:         The label to set on the line.
            * account_id:   The account on which create the write-off.
        :return: A list of python dictionary to be passed to the account.move.line's 'create' method.
        '''
        self.ensure_one()
        write_off_line_vals = write_off_line_vals or {}

        if not self.outstanding_account_id:
            raise ValidationError(_(
                "You can't create a new payment without an outstanding payments/receipts account set either on the company or the %s payment method in the %s journal.",
                self.payment_method_line_id.name, self.journal_id.display_name))

        # old Code
        # # Compute amounts.
        # write_off_amount_currency = write_off_line_vals.get('amount', 0.0)

        # New code
        # Compute amounts.
        write_off_line_vals_list = write_off_line_vals or []
        write_off_amount_currency = sum(x['amount_currency'] for x in write_off_line_vals_list)
        write_off_balance = sum(x['balance'] for x in write_off_line_vals_list)

        if self.payment_type == 'inbound':
            # Receive money.
            liquidity_amount_currency = self.amount
        elif self.payment_type == 'outbound':
            # Send money.
            liquidity_amount_currency = -self.amount
            write_off_amount_currency *= -1
        else:
            liquidity_amount_currency = write_off_amount_currency = 0.0

        if self.active_manual_currency_rate:
            if self.apply_manual_currency_exchange and self.manual_currency_exchange_rate:
                liquidity_balance = liquidity_amount_currency * self.manual_currency_exchange_rate
                write_off_balance = write_off_amount_currency * self.manual_currency_exchange_rate
            else:
                write_off_balance = self.currency_id._convert(
                    write_off_amount_currency,
                    self.company_id.currency_id,
                    self.company_id,
                    self.date,
                )
                liquidity_balance = self.currency_id._convert(
                    liquidity_amount_currency,
                    self.company_id.currency_id,
                    self.company_id,
                    self.date,
                )
        else:
            write_off_balance = self.currency_id._convert(
                write_off_amount_currency,
                self.company_id.currency_id,
                self.company_id,
                self.date,
            )
            # Old Code
            # liquidity_balance = self.currency_id._convert(
            #     liquidity_amount_currency,
            #     self.company_id.currency_id,
            #     self.company_id,
            #     self.date,
            # )

            # New code
            if not write_off_line_vals and force_balance is not None:
                sign = 1 if liquidity_amount_currency > 0 else -1
                liquidity_balance = sign * abs(force_balance)
            else:
                liquidity_balance = self.currency_id._convert(
                    liquidity_amount_currency,
                    self.company_id.currency_id,
                    self.company_id,
                    self.date,
                )
        counterpart_amount_currency = -liquidity_amount_currency - write_off_amount_currency
        counterpart_balance = -liquidity_balance - write_off_balance
        currency_id = self.currency_id.id

        if self.is_internal_transfer:
            if self.payment_type == 'inbound':
                liquidity_line_name = _('Transfer to %s', self.journal_id.name)
            else:  # payment.payment_type == 'outbound':
                liquidity_line_name = _(
                    'Transfer from %s', self.journal_id.name)
        else:
            liquidity_line_name = self.payment_reference

        # Compute a default label to set on the journal items.

        payment_display_name = {
            'outbound-customer': _("Customer Reimbursement"),
            'inbound-customer': _("Customer Payment"),
            'outbound-supplier': _("Vendor Payment"),
            'inbound-supplier': _("Vendor Reimbursement"),
        }

        line_vals_list = [
            # Liquidity line.
            {
                'name': liquidity_line_name,
                'date_maturity': self.date,
                'amount_currency': liquidity_amount_currency,
                'currency_id': currency_id,
                'debit': liquidity_balance if liquidity_balance > 0.0 else 0.0,
                'credit': -liquidity_balance if liquidity_balance < 0.0 else 0.0,
                'partner_id': self.partner_id.id,
                'account_id': self.outstanding_account_id.id,
            },
            # Receivable / Payable.
            {
                'name': self.payment_reference,
                'date_maturity': self.date,
                'amount_currency': counterpart_amount_currency,
                'currency_id': currency_id,
                'debit': counterpart_balance if counterpart_balance > 0.0 else 0.0,
                'credit': -counterpart_balance if counterpart_balance < 0.0 else 0.0,
                'partner_id': self.partner_id.id,
                'account_id': self.destination_account_id.id,
            },
        ]

        if write_off_line_vals and isinstance(write_off_line_vals, list):
            write_off_line_vals = write_off_line_vals[0]  # Get the first dictionary if it's a list
            if not self.currency_id.is_zero(write_off_amount_currency):
                line_vals_list.append({
                    'name': write_off_line_vals.get('name') or False,
                    # 'amount_currency': write_off_line_vals.get('amount_currency'),
                    # 'currency_id': write_off_line_vals.get('currency_id'),
                    'amount_currency': write_off_amount_currency,
                    'currency_id': currency_id,
                    'debit': write_off_balance if write_off_balance > 0.0 else 0.0,
                    'credit': -write_off_balance if write_off_balance < 0.0 else 0.0,
                    'partner_id': self.partner_id.id,
                    'account_id': write_off_line_vals.get('account_id'),
                })
        return line_vals_list

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


class srAccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    apply_manual_currency_exchange = fields.Boolean(string='Apply Manual Currency Exchange')
    manual_currency_exchange_rate = fields.Float(string='Manual Currency Exchange Rate',digits=(16, 6))
    active_manual_currency_rate = fields.Boolean('active Manual Currency', default=False)
    journal_amount = fields.Float("Amount", readonly=True)
    
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

    @api.onchange('manual_currency_exchange_rate','amount')
    def onchange_manual_currency_exchange_rate(self):
        if self.apply_manual_currency_exchange:
            self.journal_amount = self.manual_currency_exchange_rate*self.amount

    @api.onchange('currency_id')
    def onchange_currency_id(self):
        if self.currency_id:
            if self.company_id.currency_id != self.currency_id:
                self.active_manual_currency_rate = True
            else:
                self.active_manual_currency_rate = False
        else:
            self.active_manual_currency_rate = False
            

    def _get_total_amount_in_wizard_currency_to_full_reconcile(self, batch_result, early_payment_discount=True):
        """ Compute the total amount needed in the currency of the wizard to fully reconcile the batch of journal
        items passed as parameter.

        :param batch_result:    A batch returned by '_get_batches'.
        :return:                An amount in the currency of the wizard.
        """
        self.ensure_one()
        comp_curr = self.company_id.currency_id
        if self.source_currency_id == self.currency_id:
            # Same currency (manage the early payment discount).
            return self._get_total_amount_using_same_currency(batch_result, early_payment_discount=early_payment_discount)
        elif self.source_currency_id != comp_curr and self.currency_id == comp_curr:
            # Foreign currency on source line but the company currency one on the opposite line.
            if self.apply_manual_currency_exchange and self.manual_currency_exchange_rate:
                # return self.source_currency_custom_rate(self.source_amount_currency)
                return self.source_currency_id.with_context(
                    cus_manual_rate=self.manual_currency_exchange_rate,
                    cus_active_manutal_currency=self.apply_manual_currency_exchange,
                )._convert(
                    self.source_amount_currency,
                    comp_curr,
                    self.company_id,
                    self.payment_date,
                ), False
            else:
                return self.source_currency_id._convert(
                    self.source_amount_currency,
                    comp_curr,
                    self.company_id,
                    self.payment_date,
                ), False
        elif self.source_currency_id == comp_curr and self.currency_id != comp_curr:
            # Company currency on source line but a foreign currency one on the opposite line.
            residual_amount = 0.0
            for aml in batch_result['lines']:
                if not aml.move_id.payment_id and not aml.move_id.statement_line_id:
                    conversion_date = self.payment_date
                else:
                    conversion_date = aml.date
                if self.apply_manual_currency_exchange and self.manual_currency_exchange_rate:
                    residual_amount += comp_curr.with_context(
                    diff_manual_rate=self.manual_currency_exchange_rate,
                    diff_active_manutal_currency=self.apply_manual_currency_exchange,
                )._convert(
                    aml.amount_residual,
                    self.currency_id,
                    self.company_id,
                    conversion_date,
                )
                else:
                    residual_amount += comp_curr._convert(
                        aml.amount_residual,
                        self.currency_id,
                        self.company_id,
                        conversion_date,
                    )
            return abs(residual_amount), False
        else:
            # Foreign currency on payment different than the one set on the journal entries.
            return comp_curr._convert(
                self.source_amount,
                self.currency_id,
                self.company_id,
                self.payment_date,
            ), False

    @api.depends('can_edit_wizard', 'source_amount', 'source_amount_currency', 'source_currency_id', 'company_id', 'currency_id', 'payment_date','apply_manual_currency_exchange','manual_currency_exchange_rate')
    def _compute_amount(self):
        return super(srAccountPaymentRegister, self)._compute_amount()

    @api.depends('journal_id', 'payment_date')
    def _compute_journal_current_balance(self):
        """
        Calcula el saldo del diario al momento de la fecha del pago,
        obteniendo el saldo directamente desde la cuenta contable asociada.
        """
        # Get all unique accounts and dates to optimize the query
        accounts_to_compute = {}
        for payment in self:
            if (payment.journal_id and 
                payment.journal_id.type in ('bank', 'cash') and 
                payment.journal_id.default_account_id and 
                payment.payment_date):
                account = payment.journal_id.default_account_id
                if account.id not in accounts_to_compute:
                    accounts_to_compute[account.id] = []
                accounts_to_compute[account.id].append(payment.payment_date)
        
        # Calculate balances for each account up to each date
        balances = {}
        for account_id, dates in accounts_to_compute.items():
            for date in set(dates):  # Use set to avoid duplicate queries for same date
                domain = [
                    ('account_id', '=', account_id),
                    ('date', '<=', date),
                    ('parent_state', '=', 'posted')
                ]
                
                # Use _read_group for efficient aggregation like Odoo core
                result = self.env['account.move.line']._read_group(
                    domain=domain,
                    groupby=['account_id'],
                    aggregates=['balance:sum'],
                )
                
                if result:
                    # _read_group returns a list of tuples, where the first element is the group key
                    # and subsequent elements are the aggregated values
                    balance = result[0][1]  # First result, second element (balance sum)
                    balances[(account_id, date)] = balance
                else:
                    balances[(account_id, date)] = 0.0
        
        # Assign balances to payments
        for payment in self:
            payment.journal_current_balance = 0.0
            
            if (payment.journal_id and 
                payment.journal_id.type in ('bank', 'cash') and 
                payment.journal_id.default_account_id and 
                payment.payment_date):
                
                account = payment.journal_id.default_account_id
                balance = balances.get((account.id, payment.payment_date), 0.0)
                
                # For foreign currency journals, we need to convert the balance
                if (payment.journal_id.currency_id and 
                    payment.journal_id.currency_id != payment.company_id.currency_id):
                    # The balance field is in company currency, so we need to get the amount_currency
                    domain = [
                        ('account_id', '=', account.id),
                        ('date', '<=', payment.payment_date),
                        ('parent_state', '=', 'posted')
                    ]
                    
                    # Use _read_group for amount_currency aggregation
                    result = self.env['account.move.line']._read_group(
                        domain=domain,
                        groupby=['account_id'],
                        aggregates=['amount_currency:sum'],
                    )
                    
                    if result:
                        # _read_group returns a list of tuples, where the first element is the group key
                        # and subsequent elements are the aggregated values
                        payment.journal_current_balance = result[0][1]  # First result, second element (amount_currency sum)
                    else:
                        payment.journal_current_balance = 0.0
                else:
                    payment.journal_current_balance = balance

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
                payment.amount and payment.journal_current_balance):
                
                # Si el monto del pago es mayor que el saldo disponible, no se puede confirmar
                if payment.amount > payment.journal_current_balance:
                    payment.can_confirm_payment = False

    @api.depends('can_confirm_payment')
    def _compute_payment_button_state(self):
        """
        Computes the state of the payment button based on the payment's confirmability.
        """
        for payment in self:
            if payment.can_confirm_payment:
                payment.payment_button_state = 'normal'
            else:
                payment.payment_button_state = 'disabled'

    @api.model
    def default_get(self, fields_list):
        # OVERRIDE
        result = super().default_get(fields_list)
        move_id = self.env['account.move'].browse(self._context.get('active_id'))
        if len(move_id) !=1:
            return result
        else:
            result.update({
                'apply_manual_currency_exchange': move_id.apply_manual_currency_exchange,
                'manual_currency_exchange_rate': move_id.manual_currency_exchange_rate,
            })
            return result

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

    def _create_payment_vals_from_wizard(self, batch_result):
        # Validate journal balance before creating payment
        self._validate_journal_balance()
        
        payment_vals = {
            'date': self.payment_date,
            'amount': self.amount,
            'payment_type': self.payment_type,
            'partner_type': self.partner_type,
            'ref': self.communication,
            'journal_id': self.journal_id.id,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'partner_id': self.partner_id.id,
            'partner_bank_id': self.partner_bank_id.id,
            'payment_method_line_id': self.payment_method_line_id.id,
            'destination_account_id': self.line_ids[0].account_id.id,
            'write_off_line_vals': [],
            'apply_manual_currency_exchange':self.apply_manual_currency_exchange,
            'manual_currency_exchange_rate':self.manual_currency_exchange_rate,
            'active_manual_currency_rate':self.active_manual_currency_rate
        }

        if self.payment_difference_handling == 'reconcile':
            if self.early_payment_discount_mode:
                epd_aml_values_list = []
                for aml in batch_result['lines']:
                    if aml.move_id._is_eligible_for_early_payment_discount(self.currency_id, self.payment_date):
                        epd_aml_values_list.append({
                            'aml': aml,
                            'amount_currency': -aml.amount_residual_currency,
                            'balance': aml.currency_id._convert(-aml.amount_residual_currency, aml.company_currency_id, date=self.payment_date),
                        })

                open_amount_currency = self.payment_difference * (-1 if self.payment_type == 'outbound' else 1)
                open_balance = self.currency_id._convert(open_amount_currency, self.company_id.currency_id, self.company_id, self.payment_date)
                early_payment_values = self.env['account.move']._get_invoice_counterpart_amls_for_early_payment_discount(epd_aml_values_list, open_balance)
                for aml_values_list in early_payment_values.values():
                    payment_vals['write_off_line_vals'] += aml_values_list

            elif not self.currency_id.is_zero(self.payment_difference):
                if self.payment_type == 'inbound':
                    # Receive money.
                    write_off_amount_currency = self.payment_difference
                else: # if self.payment_type == 'outbound':
                    # Send money.
                    write_off_amount_currency = -self.payment_difference

                payment_vals['write_off_line_vals'].append({
                    'name': self.writeoff_label,
                    'account_id': self.writeoff_account_id.id,
                    'partner_id': self.partner_id.id,
                    'currency_id': self.currency_id.id,
                    'amount_currency': write_off_amount_currency,
                    'balance': self.currency_id._convert(write_off_amount_currency, self.company_id.currency_id, self.company_id, self.payment_date),
                })
        return payment_vals


    def _init_payments(self, to_process, edit_mode=False):
        """ Create the payments.

        :param to_process:  A list of python dictionary, one for each payment to create, containing:
                            * create_vals:  The values used for the 'create' method.
                            * to_reconcile: The journal items to perform the reconciliation.
                            * batch:        A python dict containing everything you want about the source journal items
                                            to which a payment will be created (see '_get_batches').
        :param edit_mode:   Is the wizard in edition mode.
        """

        payments = self.env['account.payment']\
            .with_context(skip_invoice_sync=True)\
            .create([x['create_vals'] for x in to_process])

        for payment, vals in zip(payments, to_process):
            vals['payment'] = payment

            # If payments are made using a currency different than the source one, ensure the balance match exactly in
            # order to fully paid the source journal items.
            # For example, suppose a new currency B having a rate 100:1 regarding the company currency A.
            # If you try to pay 12.15A using 0.12B, the computed balance will be 12.00A for the payment instead of 12.15A.
            if edit_mode:
                lines = vals['to_reconcile']

                # Batches are made using the same currency so making 'lines.currency_id' is ok.
                if payment.currency_id != lines.currency_id:
                    liquidity_lines, counterpart_lines, writeoff_lines = payment._seek_for_lines()
                    source_balance = abs(sum(lines.mapped('amount_residual')))
                    if self.apply_manual_currency_exchange:
                        payment_rate = self.manual_currency_exchange_rate
                    else:
                        if liquidity_lines[0].balance:
                            payment_rate = liquidity_lines[0].amount_currency / liquidity_lines[0].balance
                        else:
                            payment_rate = 0.0
                    source_balance_converted = abs(source_balance) * payment_rate

                    # Translate the balance into the payment currency is order to be able to compare them.
                    # In case in both have the same value (12.15 * 0.01 ~= 0.12 in our example), it means the user
                    # attempt to fully paid the source lines and then, we need to manually fix them to get a perfect
                    # match.
                    payment_balance = abs(sum(counterpart_lines.mapped('balance')))
                    payment_amount_currency = abs(sum(counterpart_lines.mapped('amount_currency')))
                    if not payment.currency_id.is_zero(source_balance_converted - payment_amount_currency):
                        continue

                    delta_balance = source_balance - payment_balance

                    # Balance are already the same.
                    if self.company_currency_id.is_zero(delta_balance):
                        continue

                    # Fix the balance but make sure to peek the liquidity and counterpart lines first.
                    debit_lines = (liquidity_lines + counterpart_lines).filtered('debit')
                    credit_lines = (liquidity_lines + counterpart_lines).filtered('credit')

                    if debit_lines and credit_lines:
                        payment.move_id.write({'line_ids': [
                            (1, debit_lines[0].id, {'debit': debit_lines[0].debit + delta_balance}),
                            (1, credit_lines[0].id, {'credit': credit_lines[0].credit + delta_balance}),
                        ]})
        return payments

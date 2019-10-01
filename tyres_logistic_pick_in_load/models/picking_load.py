#!/usr/bin/python
# -*- coding: utf-8 -*-
###############################################################################
#
# ODOO (ex OpenERP) 
# Open Source Management Solution
# Copyright (C) 2001-2015 Micronaet S.r.l. (<https://micronaet.com>)
# Developer: Nicola Riolini @thebrush (<https://it.linkedin.com/in/thebrush>)
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. 
# See the GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
###############################################################################

import os
import sys
import logging
import shutil
from odoo import api, fields, models, tools, exceptions, SUPERUSER_ID
from odoo.addons import decimal_precision as dp
from odoo.tools.translate import _


_logger = logging.getLogger(__name__)

class SaleOrderLine(models.Model):
    """ Model name: Sale order line
    """    
    _inherit = 'sale.order.line'
    
    @api.model
    def check_ordered_ready_status(self, browse_list):
        ''' Check order ready from list browse obj passed
        '''
        # A. Mark Sale Order Line ready:
        _logger.info('Update sale order line as ready:')
        for line in self.browse([item.id for item in browse_list]):
            if line.logistic_state == 'ordered' and \
                    line.logistic_remain_qty <= 0: # (is sale order so remain)
                line.logistic_state = 'ready'

        # B. Check Sale Order with all line ready:
        _logger.info('Update sale order as ready:')
        self.logistic_check_ready_order(browse_list)

class StockPickingDelivery(models.Model):
    """ Model name: Stock picking import document
    """
    
    _name = 'stock.picking.delivery'
    _description = 'Generator of delivery'
    _rec_name = 'create_date'

    @api.multi
    def check_import_reply(self):
        ''' Check import reply for get confirmation EXTRA BF
        '''
        # TODO schedule action?
        # Pool used:
        quant_pool = self.env['stock.picking.delivery.quant']
        company_pool = self.env['res.company']

        # Parameter:
        company = company_pool.search([])[0]
        logistic_root_folder = os.path.expanduser(company.logistic_root_folder)
        reply_path = os.path.join(
            logistic_root_folder, 'delivery', 'reply')
        history_path = os.path.join(
            logistic_root_folder, 'delivery', 'history')

        for root, subfolders, files in os.walk(reply_path):
            for f in files:
                f_split = f[:-4].split('_')
                pick_id = int(f_split[-1]) # pick_in_ID.csv                
                if f_split[1] == 'in': 
                    quants = quant_pool.search([('order_id', '=', pick_id)])
                    quants.write({'account_sync': True, })
                # else: # 'undo' # not checked!

                # XXX Move when all is done after?
                shutil.move(
                    os.path.join(reply_path, f),
                    os.path.join(history_path, f),
                    )               
                _logger.info('Pick ID: %s correct!' % f)
            break # only first folder
        return True

    # -------------------------------------------------------------------------
    #                            WORKFLOW ACTION:
    # -------------------------------------------------------------------------
    @api.multi
    def confirm_stock_load(self):
        ''' Create new picking unloading the selected material
        '''
        # ---------------------------------------------------------------------
        # Pool used:
        # ---------------------------------------------------------------------
        # Stock:
        picking_pool = self.env['stock.picking']
        move_pool = self.env['stock.move']
        quant_pool = self.env['stock.picking.delivery.quant']

        # Sale order detail:
        sale_line_pool = self.env['sale.order.line']
       
        # Purchase order detail:
        purchase_pool = self.env['purchase.order']
        
        # Partner:
        company_pool = self.env['res.company']
        
        # ---------------------------------------------------------------------
        #                          Load parameters
        # ---------------------------------------------------------------------
        company = company_pool.search([])[0]
        logistic_pick_in_type = company.logistic_pick_in_type_id

        logistic_pick_in_type_id = logistic_pick_in_type.id
        location_from = logistic_pick_in_type.default_location_src_id.id
        location_to = logistic_pick_in_type.default_location_dest_id.id
        
        logistic_root_folder = os.path.expanduser(company.logistic_root_folder)

        # ---------------------------------------------------------------------
        # Create picking:
        # ---------------------------------------------------------------------
        partner = self.supplier_id
        scheduled_date = self.create_date
        name = self.name # mandatory Doc ID
        origin = _('%s [%s]') % (name, scheduled_date)

        picking = picking_pool.create({       
            'partner_id': partner.id,
            'scheduled_date': scheduled_date,
            'origin': origin,
            #'move_type': 'direct',
            'picking_type_id': logistic_pick_in_type_id,
            'group_id': False,
            'location_id': location_from,
            'location_dest_id': location_to,
            #'priority': 1,                
            'state': 'done', # immediately!
            })
        self.picking_id = picking.id

        # ---------------------------------------------------------------------
        # Append stock.move detail (or quants if in stock)
        # ---------------------------------------------------------------------           
        sale_line_check_ready = [] # ready line after assign load qty to purchase
        purchase_ids = [] # purchase order to chech state
        for line in self.move_line_ids: # Stock move to assign to picking                    
            # Extract purchase order (for final check closing state)
            purchase_id = line.logistic_purchase_id.order_id.id
            if purchase_id not in purchase_ids:
                purchase_ids.append(purchase_id)
                
            sale_line = line.logistic_load_id
            product = line.product_id
            product_qty = line.product_uom_qty # This move qty
    
            # -----------------------------------------------------------------
            # Order that load account stock status:            
            # -----------------------------------------------------------------
            # Duplicate row to load stock:
            import pdb; pdb.set_trace()
            if sale_line.order_id.logistic_source in (
                    'internal', 'workshop', 'resell'):
                quant_pool.create({
                    # date and uid are default
                    'order_id': self.id,
                    'product_id': product.id, 
                    'product_qty': product_qty,
                    'price': line.price_unit,                    
                    })

            # -----------------------------------------------------------------
            # Create movement (not load stock):
            # -----------------------------------------------------------------
            # Link stock movement to delivery:
            line.write({
                'picking_id': picking.id,
                'origin': origin,                
                })
            
            if sale_line not in sale_line_check_ready:
                sale_line_check_ready.append(sale_line)
                    
        # ---------------------------------------------------------------------
        #                         Manage extra delivery:
        # ---------------------------------------------------------------------
        quants = quant_pool.search([('order_id', '=', self.id)])
        path = os.path.join(logistic_root_folder, 'delivery')
        order_file = os.path.join(path, 'pick_in_%s.csv' % self.id)

        try:
            os.system('mkdir -p %s' % path)
            os.system('mkdir -p %s' % os.path.join(path, 'reply'))
            os.system('mkdir -p %s' % os.path.join(path, 'history'))
        except:
            _logger.error('Cannot create %s' % path)

        order_file = open(order_file, 'w')
        order_file.write('SKU|QTA|PREZZO|CODICE FORNITORE|RIF. DOC.|DATA\r\n')
        for quant in quants:
            order = quant.order_id
            order_file.write('%s|%s|%s|%s|%s|%s\r\n' % (
                quant.product_id.default_code,
                quant.product_qty,
                quant.price,
                order.supplier_id.sql_supplier_code or '',
                order.name,
                company_pool.formatLang(order.date, date=True),
                ))
        order_file.close()

        # Check if procedure if fast to confirm reply (instead scheduled!):
        self.check_import_reply()
        
        # ---------------------------------------------------------------------
        # Sale order: Update Logistic status:
        # ---------------------------------------------------------------------
        sale_line_pool.check_ordered_ready_status(sale_line_check_ready)

        # ---------------------------------------------------------------------
        # Check Purchase order ready
        # ---------------------------------------------------------------------
        if purchase_ids:
            _logger.info('Check purchase order closed:')
            return purchase_pool.check_order_confirmed_done(purchase_ids)

    # -------------------------------------------------------------------------
    # Button event:
    # -------------------------------------------------------------------------
    @api.multi
    def open_purchase_line(self):
        ''' Open current opened line
        '''
        tree_view_id = self.env.ref(
            'tyres_logistic_pick_in_load.view_delivery_selection_tree').id
        search_view_id = self.env.ref(
            'tyres_logistic_pick_in_load.view_delivery_selection_search').id
            
        # Select record to show 
        # TODO filter active!
        purchase_pool = self.env['purchase.order.line']
        purchases = purchase_pool.search([
            ('order_id.partner_id', '=', self.supplier_id.id),
            #('logistic_undelivered_qty', '>', 0.0), 
            # TODO change with logistic_status:
            # logistic_state = done!!!
            ])

        purchase_ids = []    
        for purchase in purchases:
            if purchase.logistic_undelivered_qty:
                purchase_ids.append(purchase.id)

        return {
            'type': 'ir.actions.act_window',
            'name': _('Purcase line:'),
            'view_type': 'form',
            'view_mode': 'tree',
            #'res_id': 1,
            'res_model': 'purchase.order.line',
            'view_id': tree_view_id,
            'search_view_id': search_view_id,
            'views': [(tree_view_id, 'tree')],
            'domain': [('id', 'in', purchase_ids)],
            'context': self.env.context,
            'target': 'current', # 'new'
            'nodestroy': False,
            }
    
    # -------------------------------------------------------------------------
    # Columns:
    # -------------------------------------------------------------------------
    name = fields.Char('Ref.', size=64)
    date = fields.Date(
        'Date', default=fields.Datetime.now())
    create_date = fields.Datetime(
        'Create date', required=True, default=fields.Datetime.now())
    create_uid = fields.Many2one(
        'res.users', 'Create user', required=True, 
        default=lambda s: s.env.user)
    supplier_id = fields.Many2one(
        'res.partner', 'Supplier', required=True, 
        domain='[("supplier", "=", True)]',
        )
    carrier_id = fields.Many2one('carrier.supplier', 'Carrier')
    picking_id = fields.Many2one('stock.picking', 'Picking')

class StockPickingDeliveryQuant(models.Model):
    """ Model name: Stock line that create real load of stock
    """
    
    _name = 'stock.picking.delivery.quant'
    _description = 'Extra purchase line'
    _rec_name = 'product_id'
    
    # -------------------------------------------------------------------------
    # Columns:
    # -------------------------------------------------------------------------
    order_id = fields.Many2one(
        'stock.picking.delivery', 'Order')
    create_date = fields.Datetime(
        'Create date', default=fields.Datetime.now())
    create_uid = fields.Many2one(
        'res.users', 'Create user', default=lambda s: s.env.user)
    product_id = fields.Many2one(
        'product.product', 'Product', required=True)
    product_qty = fields.Float('Q.', digits=(16, 2), required=True)
    price = fields.Float('Price', digits=(16, 2))
    account_sync = fields.Boolean('Account sync')

class StockMove(models.Model):
    """ Model name: Stock Move
    """
    _inherit = 'stock.move'

    # -------------------------------------------------------------------------
    #                                   COLUMNS:
    # -------------------------------------------------------------------------
    delivery_id = fields.Many2one('stock.picking.delivery', 'Delivery')

    # -------------------------------------------------------------------------
    #                                   Button event:
    # -------------------------------------------------------------------------
    @api.multi
    def generate_delivery_orders_from_line(self):
        ''' Delivery order creation:
            Create the list of all order received splitted for supplier        
        '''
        delivery_pool = self.env['stock.picking.delivery']

        # ---------------------------------------------------------------------
        # Extract supplier line list:
        # ---------------------------------------------------------------------
        suppliers = {} # TODO also with carrier?
        for line in self:
            sale_order = line.logistic_load_id.order_id
            purchase_order = line.logistic_purchase_id.order_id
            
            supplier = purchase_order.partner_id
            if supplier not in suppliers:
                suppliers[supplier] = []
            suppliers[supplier].append(line)
        
        # ---------------------------------------------------------------------
        # Create purchase order:
        # ---------------------------------------------------------------------
        delivery_ids = []
        for supplier in suppliers:
            # -----------------------------------------------------------------
            # Create header:
            # -----------------------------------------------------------------
            delivery_id = delivery_pool.create({
                'supplier_id': supplier.id,
                #'carrier_id': carrier.id,
                #'create_uid': self.env.uid,                
                }).id
            delivery_ids.append(delivery_id)
            for line in suppliers[supplier]:
                line.delivery_id = delivery_id # Link to delivery order
                
        # ---------------------------------------------------------------------
        # Return created order:
        # ---------------------------------------------------------------------
        tree_view_id = form_view_id = False
        if len(delivery_ids) == 1:
            res_id = delivery_ids[0]
            views = [(tree_view_id, 'form'), (tree_view_id, 'tree')]
        else:
            res_id = False    
            views = [(tree_view_id, 'tree'), (tree_view_id, 'form')]
            
        return {
            'type': 'ir.actions.act_window',
            'name': _('Delivery created:'),
            'view_type': 'form',
            'view_mode': 'tree,form',
            'res_id': res_id,
            'res_model': 'stock.picking.delivery',
            'view_id': tree_view_id,
            #'search_view_id': search_view_id,
            'views': views,
            'domain': [('id', 'in', delivery_ids)],
            'context': self.env.context,
            'target': 'current', # 'new'
            'nodestroy': False,
            }

class PurchaseOrderLine(models.Model):
    """ Model name: Purchase Order Line
    """
    _inherit = 'purchase.order.line'

    @api.multi
    def dummy(self):
        ''' Dummy button (do nothing)
        '''
        return True

    # Fast filter:
    @api.model
    def return_fast_filter_view(self, field_name, field_value, name):
        ''' Return view filtered for field
        '''
        # Readability:
        context = self.env.context
        uid = self.env.uid

        tree_id = self.env.ref(
            'tyres_logistic_pick_in_load.view_delivery_selection_tree').id
        search_id = self.env.ref(
            'tyres_logistic_pick_in_load.view_delivery_selection_search').id

        command_clean_before = context.get('command_clean_before', False)
        if not field_name or command_clean_before:
            # Clean previous context from search defaults:
            ctx = {}
            for key in context:           
                # Remove all previous search default:
                if key.startswith('search_default_'):
                    continue # Cannot remove!
                ctx[key] = context[key]    
            if command_clean_before: # clean yet now!
                ctx['command_next_clean'] = False # yet clean
            else:    
                ctx['command_next_clean'] = True # clean all next filter

        if field_name:
            ctx = context.copy()    
            ctx['search_default_%s' % field_name] = field_value
        _logger.info(ctx)

        return {
            'type': 'ir.actions.act_window',
            'name': _('Filter applied: %s' % name),
            'view_type': 'form',
            'view_mode': 'tree,form',
            'res_model': 'purchase.order.line',
            'view_id': tree_id,
            'search_view_id': search_id,
            'views': [
                (tree_id, 'tree'), 
                (False, 'form'), 
                (search_id, 'search'),
                ],
            'domain': [
                ('check_status', '!=', 'done'), 
                #('delivery_id', '=', False),  # TODO change or remove?!?
                ('order_id.logistic_state', '=', 'confirmed'), 
                '|', 
                ('user_select_id', '=', uid), 
                ('user_select_id', '=', False), 
                ('order_id.partner_id.internal_stock', '=', False),
                ],
            'context': ctx,
            'target': 'main',# 'target': 'current', # 'new'
            'nodestroy': False,
            }

    @api.multi
    def clean_fast_filter(self):
        ''' Remove fast filter:
        '''
        return self.return_fast_filter_view(False, False, False)
            
    @api.multi
    def fast_filter_product_id(self):
        ''' Filter this product_id
        '''
        return self.return_fast_filter_view(
            'product_id', 
            self.product_id.id, 
            self.product_id.default_code,
            )

    @api.multi
    def fast_filter_supplier(self):
        ''' Filter this supplier
        '''
        return self.return_fast_filter_view(
            'order_supplier_id', 
            self.order_supplier_id.id, 
            self.order_supplier_id.name,
            )

    @api.multi
    def fast_filter_larghezza(self):
        ''' Filter this larghezza
        '''
        return self.return_fast_filter_view(
            'larghezza', 
            self.larghezza, 
            self.larghezza,
            )

    @api.multi
    def fast_filter_spalla(self):
        ''' Filter this spalla
        '''
        return self.return_fast_filter_view(
            'spalla', 
            self.spalla, 
            self.spalla,
            )

    @api.multi
    def fast_filter_raggio(self):
        ''' Filter this raggio
        '''
        return self.return_fast_filter_view(
            'raggio', 
            self.raggio, 
            self.raggio,
            )

    # On change not used!
    @api.onchange('logistic_delivered_manual')
    def onchange_logistic_delivered_manual(self, ):
        ''' Write check state depend on partial or done
        '''
        if self.logistic_delivered_manual < self.logistic_undelivered_qty:
            self.check_status = 'partial'
        else:    
            self.check_status = 'total'

    @api.multi    
    def create_delivery_orders(self):
        ''' Create the list of all order received splitted for supplier        
        '''
        delivery_pool = self.env['stock.picking.delivery']
        sale_line_pool = self.env['sale.order.line']

        # ---------------------------------------------------------------------
        # Search selection line for this user:
        # ---------------------------------------------------------------------
        lines = self.search([
            ('user_select_id', '=', self.env.uid), # This user
            ('logistic_delivered_manual', '>', 0), # With quantity insert
            ('order_id.partner_id.internal_stock', '=', False), # No internal
            ('check_status', '!=', 'done'), # Market previously
            ])

        if not lines:
            raise exceptions.Warning('No selection for current user!') 

        # ---------------------------------------------------------------------
        # Pool used:
        # ---------------------------------------------------------------------
        # Stock:
        move_pool = self.env['stock.move']
        company_pool = self.env['res.company']
        
        # ---------------------------------------------------------------------
        #                          Load parameters
        # ---------------------------------------------------------------------
        company = company_pool.search([])[0]
        logistic_pick_in_type = company.logistic_pick_in_type_id
        location_from = logistic_pick_in_type.default_location_src_id.id
        location_to = logistic_pick_in_type.default_location_dest_id.id
        
        # ---------------------------------------------------------------------
        # Create picking:
        # ---------------------------------------------------------------------
        now = fields.Datetime.now()
        sale_line_check_ready = []
        for line in lines:
            purchase = line.order_id            
            partner = purchase.partner_id
            product = line.product_id

            product_qty = line.logistic_delivered_manual
            remain_qty = line.logistic_undelivered_qty
            logistic_sale = line.logistic_sale_id
            if logistic_sale not in sale_line_check_ready:
                sale_line_check_ready.append(logistic_sale)
            
            if product_qty > remain_qty:
                raise exceptions.Warning(_(
                    'Too much unload %s, max is %s (product %s)!') % (
                        product_qty,
                        remain_qty,
                        product.default_code,
                        ))
            
            # Create stock movement without picking:
            move_pool.create({
                'company_id': company.id,
                'partner_id': partner.id,
                'picking_id': False, # TODO join after
                'product_id': product.id, 
                'name': product.name or ' ',
                'date': now,
                'date_expected': now,
                'location_id': location_from,
                'location_dest_id': location_to,
                'product_uom_qty': product_qty,
                'product_uom': product.uom_id.id,
                'state': 'done',
                'origin': _('Temporary load'), # TODO replace
                'price_unit': line.price_unit, # Save purchase price
                
                # Sale order line link:
                'logistic_load_id': logistic_sale.id,
                
                # Purchase order line line: 
                'logistic_purchase_id': line.id,
                
                #'purchase_line_id': load_line.id, # XXX needed?
                #'logistic_quant_id': quant.id, # XXX no quants here

                # group_id
                # reference'
                # sale_line_id
                # procure_method,
                #'product_qty': select_qty,
                })
            
            # Partial not touched! # TODO correct?
            check_status = line.check_status
            if check_status == 'total':
                check_status = 'done'            
            line.write({
                'user_select_id': False,
                'logistic_delivered_manual': 0.0,
                'check_status': check_status,
                })

        # ---------------------------------------------------------------------
        # Sale order: Update Logistic status:
        # ---------------------------------------------------------------------
        sale_line_pool.check_ordered_ready_status(sale_line_check_ready)

        # TODO check ready order now:
        return self.clean_fast_filter()

    @api.multi
    def open_detail_delivery_in(self):
        ''' Return detail view for stock operator
        '''
        form_view_id = self.env.ref(
            'tyres_logistic_pick_in_load.view_delivery_selection_form').id
        return {
            'type': 'ir.actions.act_window',
            'name': _('Line detail'),
            'view_type': 'form',
            'view_mode': 'form',
            'res_id': self.id,
            'res_model': 'purchase.order.line',
            'view_id': form_view_id,
            'views': [(form_view_id, 'form')],
            'domain': [],
            'context': self.env.context,
            'target': 'new',
            'nodestroy': False,
            }
    
    @api.multi
    def delivery_0(self):
        ''' Add +1 to manual arrived qty
        '''
        self.write({
            'logistic_delivered_manual': 0,
            'user_select_id': False, # no need to save user
            })        
        return self.onchange_logistic_delivered_manual()

    @api.multi
    def delivery_more_1(self):
        ''' Add +1 to manual arrived qty
        '''
        logistic_undelivered_qty = self.logistic_undelivered_qty
        logistic_delivered_manual = self.logistic_delivered_manual
        
        if logistic_delivered_manual >= logistic_undelivered_qty:
            raise exceptions.Warning(
                _('All received: %s!') % logistic_undelivered_qty)
        
        self.write({
            'logistic_delivered_manual': logistic_delivered_manual + 1.0,
            'user_select_id': self.env.uid,
            })        
        return self.onchange_logistic_delivered_manual()

    @api.multi
    def delivery_less_1(self):
        ''' Add +1 to manual arrived qty
        '''
        logistic_delivered_manual = self.logistic_delivered_manual

        # TODO check also logistic_undeliveret_qty for remain?
        if logistic_delivered_manual < 1.0:
            raise exceptions.Warning('Nothing to remove!') 
            
        if logistic_delivered_manual <= 1.0:
            active_id = False # XXX need?
        self.write({
            'logistic_delivered_manual': logistic_delivered_manual - 1.0,
            'user_select_id': self.env.uid,
            })        
        return self.onchange_logistic_delivered_manual()

    @api.multi
    def delivery_all(self):
        ''' Add +1 to manual arrived qty
        '''
        logistic_undelivered_qty = self.logistic_undelivered_qty

        if logistic_undelivered_qty <= 0.0:
            raise exceptions.Warning('No more q. to deliver!') 
        
        self.write({
            'logistic_delivered_manual': logistic_undelivered_qty,
            'user_select_id': self.env.uid,
            })        
        return self.onchange_logistic_delivered_manual()

    @api.one
    def _get_name_extended_full(self):
        ''' Generate extended name
        '''
        try:
            product = self.product_id
            self.name_extended = product.description_sale or \
                product.titolocompleto or product.name or 'Non trovato' 
        except:
            self.name_extended = _('Error generating name')

    # -------------------------------------------------------------------------
    # Columns:
    # -------------------------------------------------------------------------
    name_extended = fields.Char(
        compute='_get_name_extended_full', string='Extended name')
    logistic_delivered_manual = fields.Float('Manual', digits=(16, 2))
    user_select_id = fields.Many2one('res.users', 'Selected user')

    # Related for filter
    raggio = fields.Char(
        'Ray', related='product_id.raggio', store=True)
    larghezza = fields.Char(
        'Width', related='product_id.larghezza', store=True)
    spalla = fields.Char(
        'Spalla', related='product_id.spalla', store=True)
        
    order_supplier_id = fields.Many2one(
        'res.partner', 'Supplier', domain="[('supplier', '=', True)]",
        related='order_id.partner_id', store=True)
        
    order_supplier_name = fields.Char(
        'Supplier name', related='order_supplier_id.name')
    
    product_name = fields.Char('Product name', related='product_id.name')
    
    check_status = fields.Selection([
        #('none', 'Not touched'), # Not selected
        ('done', 'Load in stock'), # Selected all remain to deliver
        
        ('total', 'Total received'), # Selected all to deliver
        ('partial', 'Partially received'), # Select partial to deliver
        ], 'Check status', default='partial')

    logistic_source = fields.Selection(
        'Logistic source', readonly=True,
        related='logistic_sale_id.order_id.logistic_source',
        )
        
    #ivel = fields.Char(
    #    'Indice di velocità', related='product_id.raggio', store=True)
    #icarico = fields.Char(
    #    'Indice di carico', related='product_id.raggio', store=True)
    #runflat = fields.Boolean(
    #    'Run flat', related='product_id.raggio', store=True)
    #brand = fields.Many2one('mmac_brand', 'Marca')
    #misuracompleta = fields.Char('Misura completa')
    #stagione = fields.Char('Stagione')
    #canale = fields.Char('Canale')
    #offset = fields.Char('Offset')
    #cb = fields.Char('CB')
    #numerofori = fields.Char('Numero fori')
    #interasse = fields.Char('Interasse')
    #bestpricecost = fields.Float('Costo bestprice')
    

class StockPickingDelivery(models.Model):
    """ Model name: Stock picking import document: add relations
    """
    
    # -------------------------------------------------------------------------
    # Columns:
    # -------------------------------------------------------------------------
    _inherit = 'stock.picking.delivery'

    move_line_ids = fields.One2many(
        'stock.move', 'delivery_id', 'Load move')
    quant_ids = fields.One2many(
        'stock.picking.delivery.quant', 'order_id', 'Stock quant:')

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

# -*- coding: utf-8 -*-
##############################################################################
#
# Copyright (c) 2015 Deltatech All Rights Reserved
#                    Dorin Hongu <dhongu(@)gmail(.)com       
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#
##############################################################################


from odoo import models, fields, api, _
from odoo.exceptions import except_orm, Warning, RedirectWarning
from odoo.tools import float_compare
import odoo.addons.decimal_precision as dp
import math
from datetime import datetime

import logging
_logger = logging.getLogger(__name__)

READING_TYPE_SELECTION = [
    ('inc', 'Increase'),
    ('dec', 'Decrease'),
    ('cng', 'Change'),
    ('src', 'Meter')
]

# de deinit o tabela de configurare pentru unitatile de masura utilizate pentru contorizare....


# face legatura dintre categorie si unitatea de masura
class service_meter_category(models.Model):
    _name = 'service.meter.category'
    _description = "Service Equipment Category"     
    
    name = fields.Char(string='Category') 
    uom_id = fields.Many2one('product.uom', string='Unit of Measure' , required=True ) 
    bill_uom_id = fields.Many2one('product.uom', string='Billing Unit of Measure' , required=True ) 
    type = fields.Selection([('counter','Counter'),('collector','Collector')], string='Type', default='counter')

class service_meter(models.Model):
    _name = 'service.meter'
    _description = "Meter"
    _rec_name = "display_name"

    name = fields.Char(string='Name')
    display_name = fields.Char(compute='_compute_display_name')
    
    type = fields.Selection([('counter','Counter'),('collector','Collector')], string='Type', default='counter', related="meter_categ_id.type", readonly=True)
    equipment_id = fields.Many2one('service.equipment', string='Equipment',required=True,  ondelete='cascade', index=True) 
    meter_reading_ids = fields.One2many('service.meter.reading', 'meter_id', string='Meter Reading')
   
    meter_ids = fields.Many2many('service.meter', 'service_meter_collector_meter', 'meter_collector_id', 'meter_id', string='Meter',  domain=[('type', '=', 'counter')])
 
    start_value = fields.Float(string='Start Value',digits= dp.get_precision('Meter Value'))
    last_meter_reading_id = fields.Many2one('service.meter.reading', string='Last Meter Reading',compute='_compute_last_meter_reading' )
    total_counter_value = fields.Float(string='Total Counter Value', digits= dp.get_precision('Meter Value'),  compute='_compute_last_meter_reading' )
    estimated_value= fields.Float(string='Estimated Value', digits= dp.get_precision('Meter Value'), compute='_compute_estimated_value' ) 
    
    meter_categ_id =  fields.Many2one('service.meter.category', string='Category' , required=True )
    uom_id = fields.Many2one('product.uom', string='Unit of Measure' ,required=True, index=True)

    value_a =  fields.Float()
    value_b =  fields.Float()
    

    _sql_constraints = [
        ('equipment_uom_uniq', 'unique(equipment_id,uom_id)', 'Two meter for one equipment with the same unit of measure? Impossible!')
    ]


    @api.model
    def create(self,   vals ):  
        if ('name' not in vals) or (vals.get('name') in ('/', False)):
            sequence = self.env.ref('deltatech_service_equipment.sequence_meter')
            if sequence:
                vals['name'] = sequence.next_by_id()
        return super(service_meter, self).create( vals )


    # rutina pentru actualizare date curente    
    def update_name(self):
        sequence = self.env.ref('deltatech_service_equipment.sequence_meter')
        if sequence:
            meters = self.env['service.meter'].search([('name','=',False)])
            for meter in meters:
                meter.name = sequence.next_by_id()

    # rutina pentru actualizare date curente 
    def update_categ(self):
        meters = self.env['service.meter'].search([('meter_categ_id','=',False)])
        for meter in meters:
            categ = self.env['service.meter.category'].search([('uom_id','=',meter.uom_id.id)])
            if categ:
                meter.meter_categ_id =  categ.id  
    
    @api.one
    @api.depends('name','uom_id.name')     
    def _compute_display_name(self):
        if self.name:
            self.display_name = "%s [%s]" % (self.name , self.uom_id.name)
        else:
            self.display_name = self.uom_id.name

   

    @api.onchange('meter_categ_id')
    def onchange_meter_categ_id(self):
        if self.meter_categ_id:
            self.uom_id = self.meter_categ_id.uom_id


    @api.one
    @api.depends('meter_reading_ids','meter_reading_ids.counter_value','meter_ids')
    def _compute_last_meter_reading(self ):
        total_counter_value = 0
        if self.type == 'counter':
            if self.meter_reading_ids:
                self.last_meter_reading_id =  self.meter_reading_ids[0]
                total_counter_value = self.last_meter_reading_id.counter_value                 
        else:
            for meter in self.meter_ids:
                total_counter_value += meter.meter_reading_ids[0].counter_value
        
                
        self.total_counter_value = total_counter_value

    @api.one
    def _compute_estimated_value(self):
        date = self.env.context.get('date', fields.Date.today())
        self.estimated_value = self.get_forcast(date)


    @api.multi
    def calc_forcast_coef(self):
        
        def linreg( X, Y):
            """
            return a,b in solution to y = ax + b such that root mean square distance between trend line and original points is minimized
            """
            N = len(X)
            Sx = Sy = Sxx = Syy = Sxy = 0.0
            for x, y in zip(X, Y):
                Sx = Sx + x
                Sy = Sy + y
                Sxx = Sxx + x*x
                Syy = Syy + y*y
                Sxy = Sxy + x*y
            det = Sxx * N - Sx * Sx
            if det:
                a, b = (Sxy * N - Sy * Sx)/det, (Sxx * Sy - Sx * Sxy)/det  
            else:
                a = b = 0.0
            return a, b   
        
        for meter in self:
            x = []
            y = []
            for reading in meter.meter_reading_ids:
                if not reading.estimated:
                    x  += [ fields.Date.from_string(reading.date).toordinal() ]
                    y  += [ reading.counter_value]
            
            a,b =  linreg(x,y) 
            meter.write({'value_a':a, 'value_b':b})
            #_logger.info("Value A: %s, Value B: %s" % (str(a), str(b) ))
       
 
    @api.model
    def get_forcast(self, date):
        "Calculeaza valoarea estimata in functie de data"  
        self.ensure_one()      
        x = fields.Date.from_string(date).toordinal()
        res = self.value_a * x + self.value_b
        if not res:
            res = self.total_counter_value
        return res

    @api.model
    def get_forcast_date(self, value):
        "Calculeaza data estimata in functie de valoare"
        self.ensure_one()
        if self.value_a:
            x = (value - self.value_b ) / self.value_a
            date = datetime.fromordinal(int(x))
            date = fields.Date.to_string(date)
        else:
            date =  False
        return  date 



    @api.multi
    def get_counter_value(self,begin_date,end_date):
        value = 0
        if self.type == 'counter':
             domain = [('date', '>=', begin_date ), ('date', '<', end_date), ('meter_id','=',self.id)]
             res = self.env['service.meter.reading'].read_group( domain, fields=['difference','meter_id'], groupby=['meter_id'])  
             if res:
                 value = res[0].get('difference',0)
        else:
            for meter in self.meter_ids:
                value += meter.get_counter_value(begin_date,end_date)
        return value


    @api.multi
    def recheck_value(self):
        for meter in self:
            readings = meter.meter_reading_ids.sorted(key=lambda r: r.date)
            previous_counter_value = meter.start_value
            for reading in readings:
                #reading.previous_counter_value = previous_counter_value
                #difference = 
                #reading._store_set_values
                reading.write({'previous_counter_value':previous_counter_value,
                               'difference' : reading.counter_value-previous_counter_value})
                previous_counter_value = reading.counter_value


class service_meter_reading(models.Model):
    _name = 'service.meter.reading'
    _description = "Meter Reading"
    _order = "meter_id, date desc, id desc"   
    _rec_name = "counter_value"
    
 
    
    meter_id = fields.Many2one('service.meter', string='Meter',required=True, ondelete='cascade',  domain=[('type', '=', 'counter')]) 
    
    equipment_history_id = fields.Many2one('service.equipment.history', string='Equipment history')   
    equipment_id = fields.Many2one('service.equipment', string='Equipment',required=True, ondelete='restrict' ) 

    
    date = fields.Date(string='Date', index=True, required=True, default = fields.Date.today()  ) 
    previous_counter_value = fields.Float(string='Previous Counter Value',readonly=True ,
                                           digits= dp.get_precision('Meter Value'),
                                           compute='_compute_previous_counter_value', store=True)
    counter_value = fields.Float(string='Counter Value',
                                 digits= dp.get_precision('Meter Value'),
                                  group_operator="max")
    estimated = fields.Boolean(string='Estimated')
    difference = fields.Float(string='Difference', readonly=True, 
                              digits= dp.get_precision('Meter Value'),
                               compute='_compute_difference', store=True)
    consumption_id = fields.Many2one('service.consumption', string='Consumption',readonly=True) 
    read_by = fields.Many2one('res.partner', string='Read by', domain=[('is_company','=',False)])
    note =  fields.Text(String='Notes')  

    #todo: de adaugat status: ciorna, valid, neplauzibil, facturat ?
    
    @api.one
    @api.depends('date','meter_id','equipment_id')
    def _compute_previous_counter_value(self ):
        self.previous_counter_value = self.meter_id.start_value
        if self.date and self.meter_id:
            previous = self.env['service.meter.reading'].search([('meter_id','=',self.meter_id.id),
                                                                 ('date','<',self.date)],limit=1, order='date desc, id desc')
            if previous:
                self.previous_counter_value = previous.counter_value
                self.difference = self.counter_value - self.previous_counter_value
                #self.invalidate_cache() # asta e solutia ?
            
 
            
    @api.one
    @api.depends('counter_value','previous_counter_value')
    def _compute_difference(self):
        self.difference = self.counter_value - self.previous_counter_value
        next = self.env['service.meter.reading'].search([('meter_id','=',self.meter_id.id),
                                                         ('date','>',self.date)],limit=1, order='date, id')
        if next and next.previous_counter_value <>  self.counter_value:
            next.write({'previous_counter_value': self.counter_value,
                        'difference' : (next.counter_value - self.counter_value)})
            #next._compute_difference()      

 
            
    @api.onchange('equipment_id','date')
    def onchange_equipment_id(self):
        if self.equipment_id:
            if len(self.equipment_id.meter_ids) == 1:
                self.meter_id = self.equipment_id.meter_ids[0]
            self.equipment_history_id = self.equipment_id.get_history_id(self.date) 
        else: 
            self.equipment_history_id = False           
            #self.previous_counter_value = self.meter_id.last_meter_reading_id.counter_value
            #self.counter_value =  self.meter_id.get_forcast(self.date)   #self.previous_counter_value 


    @api.onchange('meter_id')
    def onchange_meter_id(self):
        if self.meter_id:
            self.equipment_id = self.meter_id.equipment_id
             
            

    @api.model
    def create(self, vals):
        if not vals.get('equipment_history_id',False):
            equipment = self.env['service.equipment'].browse(vals['equipment_id'])
            history_id = equipment.get_history_id(vals['date']) 
            print history_id
            if history_id:
                vals['equipment_history_id'] =  history_id.id
                 
        res = super(service_meter_reading,self).create(vals)
        #self.meter_id.calc_forcast_coef()
        return res

    @api.multi
    def write(self, vals):
        res = super(service_meter_reading,self).write(vals)
        if  vals.get('date',False):
            for reading in self:
                reading.meter_id.recheck_value()
                reading.meter_id.calc_forcast_coef()
        return 


    @api.multi
    def unlink(self):
        nexts = self.env['service.meter.reading']
        meters = self.env['service.meter']
        for reading in self:
            if reading.consumption_id:
                raise Warning('Meter reading recorder in consumption prepared for billing.')
            meters |= reading.meter_id
            #next = self.env['service.meter.reading'].search([('meter_id','=',self.meter_id.id),
            #                                                      ('date','>',self.date)],limit=1, order='date, id')
            #nexts |= next
            
        res = super(service_meter_reading,self).unlink()
        
        #for next in nexts:
        #    next._compute_previous_counter_value()
        #    next._compute_difference()
        #  self.env.cr.commit()      este necesar ???
        meters.recheck_value() 
  
 
        return res
 



    
    
    
    
    
    
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

# -*- coding: utf-8 -*-
from odoo import models, fields
 
class VisitCancelWizard(models.TransientModel):
    _name = 'visit.cancel.wizard'
    _description = 'Cancel Appointment Wizard'
 
    reason = fields.Text(string="Reason for Cancellation", required=True)
 
    def action_confirm_cancel(self):
        appointment = self.env['visit.information'].browse(self.env.context.get('active_id'))
        appointment.cancellation_reason = self.reason
        appointment.status = "cancelled"
        
        # Send email
        template = self.env.ref("visitor_management.email_visit_cancelled")
        email_values = {'email_from': self.env.user.email}
        template.send_mail(appointment.id, force_send=True, email_values=email_values)
        
        
 
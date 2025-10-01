# -*- coding: utf-8 -*-
from dataclasses import fields
import json
import random
import requests
import logging
from odoo import http, fields as odoo_fields
from odoo.http import Response, request
from datetime import date, datetime, timedelta
from odoo import _
from werkzeug.exceptions import NotFound
from odoo.addons.bus.models.bus import dispatch


# ...

now = odoo_fields.Datetime.now()


_logger = logging.getLogger(__name__)


class VisitorSMS(http.Controller):

    @http.route('/visitor/send_sms', auth='public', type='json', methods=['POST'], csrf=False)
    def send_sms(self, **kw):
        """Send download link to visitor's phone"""
        phone = kw.get('phone')
        country_code = kw.get('country_code', '+91')  # default India
        visitor = request.env['visit.information'].sudo().search([('phone', '=', phone)], limit=1)

        if not visitor:
            return {"Status": 0, "Message": "Visitor not found"}

        if not visitor.attachment_id:
            return {"Status": 0, "Message": "No attachment found for this visitor"}
        
        
        # Base URL
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')

        # Generate download link
        download_link = f"{base_url}/web/content/{visitor.attachment_id.id}?download=True"
        sms_text = f"Hi {visitor.name}, your visit is approved. Download your pass here: {download_link}"

        # Send SMS
        success = SMSUtils.send_sms_route_mobile(request.env, phone, country_code, sms_text)

        if success:
            return {"Status": 1, "Message": "Download link sent via SMS"}
        else:
            return {"Status": 0, "Message": "Failed to send SMS"}
    
class VisitorQRController(http.Controller):

    @http.route(['/visitor/verify/<string:token>'], type='json', auth='public', methods=['POST'], csrf=False)
    def verify_qr(self, token, **kw):
        try:
            payload = request.httprequest.get_json(force=True, silent=True) or {}
            device = payload.get("device")

            _logger.info(f"Incoming Payload: {payload}")  # Debug
            
            data = {}

            # Device check
            if device != "1234":
                return {"Status": 0, "Message": f"Device id not matched. Got: {device}", "Data": data}
            
            # Verify QR & Visitor
            visitor = request.env['visit.information'].sudo().search([
                ('qr_token', '=', token),
                ('status', '=', "approved")
            ], limit=1)

            if not visitor:
                return {"Status": 0, "Message": "QR does not match any registered visitor.", "Data": data}

            if not visitor.visiting_date or visitor.visiting_date.date() != date.today():
                return {"Status": 0, "Message": "Visitor registered, but not scheduled for today.", "Data": data}

            # Return requirements only
            return {
                "Status": 1,
                "Message": "QR verified successfully.",
                "VisitorID": visitor.id,
            }

        except Exception as e:
            _logger.exception("Error in QR verification")
            return {"Status": -1, "Message": f"Internal Server Error: {str(e)}", "Data": {}}

        
class SendmeCommon:
    @staticmethod
    def _process_request_body(request_data, kw=None):
        request_data_str = request_data.decode('utf-8').strip()
        if request_data_str:
            try:
                json_data = json.loads(request_data_str)
                return json_data
            except json.JSONDecodeError:
                return {}
        return {}
    
class Otp(http.Controller):

    def _get_today_range(self):
        """Return datetime range for today."""
        today = datetime.today().date()
        tomorrow = today + timedelta(days=1)
        return today, tomorrow

    def _find_today_visitor(self, mobile):
        """Find visitor by mobile for today's date only."""
        today, tomorrow = self._get_today_range()
        return request.env['visit.information'].sudo().search([
            ("phone", "=", mobile),
            ("visiting_date", ">=", today),
            ("visiting_date", "<", tomorrow)
        ], limit=1)

    @http.route('/visitor/SendOTP', auth='public', methods=['POST'], csrf=False)
    def send_otp(self, **kw):
        data = request.httprequest.get_data()
        json_data = SendmeCommon._process_request_body(data, kw)

        mobile = json_data.get('mobileNumber') or kw.get('mobileNumber')
        country_code = "91"  # Hardcoded for now

        if not mobile:
            return request.make_json_response({
                "Status": 0,
                "Message": "Invalid request. Mobile number is required."
            })

        otp_code = str(random.randint(100000, 999999))
        sms_text = f"{otp_code} is your one time password for SendMe Technologies"
        now = datetime.now()
        
        visitor = self._find_today_visitor(mobile)
        if visitor:
            _logger.info(f"Writing OTP {otp_code} to visitor {visitor.id}")
            visitor.sudo().write({
                'otp_code': otp_code,
                'last_otp_time': now
            })
            _logger.info(f"After write, visitor otp_code={visitor.otp_code}")

        else:
            visitor = request.env["visit.information"].sudo().create({
                "phone": mobile,
                "otp_code": otp_code,
                "visiting_date": now
            })

        response = SMSUtils.send_sms_route_mobile(request.env, mobile, country_code, sms_text)

        return request.make_json_response({
            "Status": 1 if response else 0,
            "Message": "OTP sent successfully." if response else "Failed to send OTP. Please try again.",
            "Data": int(otp_code) if response else None
        })


    @http.route('/visitor/verifyOTP', type='json', auth='public', methods=['POST'], csrf=False)
    def verify_otp(self, **kw):
        try:
            payload = request.httprequest.get_json(force=True, silent=True) or {}
            mobile = payload.get("mobileNumber")
            otp = payload.get("accessToken")

            _logger.info(f"Incoming OTP Payload: {payload}")

            if not mobile or not otp:
                return {
                    "Status": 0,
                    "Message": "Invalid request. Mobile and OTP are required.",
                    "Data": {}
                }

            # search visitor for today
            today, tomorrow = self._get_today_range()
            visitor = request.env['visit.information'].sudo().search([
                ('phone', '=', mobile),
                ('visiting_date', '>=', today),
                ('visiting_date', '<', tomorrow)
            ], limit=1)
            
           
            if not otp.isdigit() or int(otp) != int(visitor.otp_code or 0):
                return {
                    "Status": 0,
                    "Message": "Invalid OTP!",
                    "Data": {}
                }

           
            if not visitor or not visitor.name:
                return {"Status": 1, "Message": "New user - please register", "Data": {}, "Newuser": 1}

            
            if visitor.status != "approved":
                return {
                    "Status": 0,
                    "Message": f"Visitor not approved yet (status={visitor.status}).",
                    "Data": {}
                }

            # 5️⃣ Success
            return {
                "Status": 1,
                "Message": "OTP verified successfully.",
                "VisitorID": visitor.id
            }

        except Exception as e:
            _logger.exception("Error in OTP verification")
            return {
                "Status": -1,
                "Message": f"Internal Server Error: {str(e)}",
                "Data": {}
            }



    @http.route('/visitor/checkin_out', type='json', auth='public', methods=['POST'], csrf=False)
    def visitor_attendance(self, **kw):
        try:
            payload = request.httprequest.get_json(force=True, silent=True) or {}
            visitor_id = payload.get("visitor_id")
            action = payload.get("action")  # "checkin" or "checkout"

            _logger.info(f"Incoming Attendance Payload: {payload}")

            if not visitor_id or not action:
                return {"Status": 0, "Message": "Invalid request. Visitor ID and action are required.", "Data": {}}

            visitor = request.env['visit.information'].sudo().browse(int(visitor_id))
            if not visitor.exists():
                return {"Status": 0, "Message": "Visitor not found.", "Data": {}}

            if visitor.status != "approved":
                return {"Status": 0, "Message": f"Visitor not approved yet (status={visitor.status}).", "Data": {}}

            # === Check-in ===
            if action == "checkin":
                if visitor.check_out:
                    return {"Status": 0, "Message": "Already checked out. Cannot check-in again.", "Data": {}}

                if not visitor.check_in:
                    check_in_time = datetime.now()
                    visitor.sudo().write({'check_in': check_in_time})

                    # Notify employee
                    if visitor.employee and visitor.employee.user_id:
                        message = {
                            'title': "Visitor Check-in",
                            'message': f"{visitor.name} has checked in at {check_in_time.strftime('%H:%M')}.",
                            'sticky': True,
                            'type': 'info'
                        }
                        request.env['bus.bus']._sendone(
                            visitor.employee.user_id.partner_id,
                            'simple_notification',
                            message
                        )

                    data = {
                        "name": visitor.name,
                        "check_in": check_in_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "instruction": visitor.instructions
                    }
                    return {"Status": 1, "Message": "Visitor check-in successful.", "Data": data}
                else:
                    return {"Status": 0, "Message": "Already checked in.", "Data": {}}

            # === Check-out ===
            elif action == "checkout":
                if visitor.check_in and not visitor.check_out:
                    check_out_time = datetime.now()
                    visitor.sudo().write({'check_out': check_out_time})

                    # Notify employee
                    if visitor.employee and visitor.employee.user_id:
                        message = {
                            'title': "Visitor Check-out",
                            'message': f"{visitor.name} has checked out at {check_out_time.strftime('%H:%M')}.",
                            'sticky': True,
                            'type': 'info'
                        }
                        request.env['bus.bus']._sendone(
                            visitor.employee.user_id.partner_id,
                            'simple_notification',
                            message
                        )

                    data = {"name": visitor.name, "check_out": check_out_time.strftime("%Y-%m-%d %H:%M:%S")}
                    return {"Status": 1, "Message": "Visitor check-out successful.", "Data": data}
                elif not visitor.check_in:
                    return {"Status": 0, "Message": "Cannot check-out before check-in.", "Data": {}}
                else:
                    return {"Status": 0, "Message": "Already checked out.", "Data": {}}

            else:
                return {"Status": 0, "Message": "Invalid action. Use 'checkin' or 'checkout'.", "Data": {}}

        except Exception as e:
            _logger.exception("Error in Attendance API")
            return {"Status": -1, "Message": f"Internal Server Error: {str(e)}", "Data": {}}




class SMSUtils:
    @staticmethod
    def send_sms_route_mobile(env, phone_number, country_code, sms_text):
        """Send SMS using Route Mobile (you can replace with Firebase later)"""
        config = env['ir.config_parameter'].sudo()
        user_name = config.get_param('visitor.sms.username')
        password = config.get_param('visitor.sms.password')
        source = config.get_param('visitor.sms.source')
        entity_id = config.get_param('visitor.sms.entity_id')
        temp_id = config.get_param('visitor.sms.temp_id')

        destination = f"{country_code}{phone_number}"

        url = (
            "https://sms6.rmlconnect.net:8443/bulksms/bulksms"
            f"?username={user_name}"
            f"&password={password}"
            f"&type=0"
            f"&dlr=1"
            f"&destination={destination}"
            f"&source={source}"
            f"&message={sms_text}"
            f"&entityid={entity_id}"
            f"&tempid={temp_id}"
        )

        try:
            response = requests.post(url, timeout=10)
            _logger.info(f"SMS Response: {response.text}")
            _logger.info(f"DESTINATION: {destination}")
            _logger.info(f"SMS TEXT: {sms_text}")
            _logger.info(f"SMS RESPONSE: {response.text}")
            if isinstance(response.text, str):
                split_response = response.text.split("|")
                return split_response[0] == "1701"
        except Exception:
            _logger.exception("Error sending SMS via RouteMobile")
        return False


class VisitorForm(http.Controller):

    def _get_today_range(self):
        from datetime import datetime, timedelta
        today = datetime.combine(datetime.today(), datetime.min.time())
        tomorrow = today + timedelta(days=1)
        return today, tomorrow

    def _find_today_visitor(self, phone):
        today, tomorrow = self._get_today_range()
        return request.env['visit.information'].sudo().search([
            ('phone', '=', phone),
            ('visiting_date', '>=', today),
            ('visiting_date', '<', tomorrow)
        ], limit=1)

    def _normalize_vals(self, model, data):
        """Keep only real model fields; coerce simple m2m lists to (6,0,ids)."""
        fields_map = model._fields
        vals = {}
        for k, v in data.items():
            if k == 'company_id':
                continue  # never trust from client
            if k not in fields_map:
                continue  # ignore unknown dynamic keys

            f = fields_map[k]
            # Many2one: allow {"id": 5} or 5 as value
            if f.type == 'many2one':
                if isinstance(v, dict) and 'id' in v:
                    vals[k] = int(v['id']) if v['id'] else False
                else:
                    vals[k] = int(v) if v else False

            # Many2many/One2many:
            elif f.type in ('many2many', 'one2many'):
                # Accept full Odoo command list if caller already sends it
                if isinstance(v, list) and v and isinstance(v[0], (list, tuple)) and len(v[0]) >= 2:
                    vals[k] = v
                # Accept simple list of IDs for M2M and turn into (6,0,ids)
                elif f.type == 'many2many' and isinstance(v, list) and all(isinstance(x, (int, str)) for x in v):
                    vals[k] = [(6, 0, [int(x) for x in v])]
                else:
                    # For O2M you typically need command tuples; ignore if not valid
                    pass

            # Datetime/Date: let Odoo coerce ISO strings; if you want to force "now", do it server-side
            else:
                vals[k] = v
        return vals


    @http.route('/visitor/submitForm', auth='public', type='json', methods=['POST'], csrf=False)
    def submit_form(self, **kw):
        try:
            data = request.get_json_data() or {}
            vals = {k: v for k, v in data.items() if k != 'company_id'}

            employee_id = vals.get('employee') or vals.get('employee_id')
            if employee_id:
                emp = request.env['hr.employee'].sudo().browse(int(employee_id))
                if emp.exists():
                    vals['company_id'] = emp.company_id.id

            phone = vals.get('phone') or data.get('mobileNumber')
            visitor = self._find_today_visitor(phone) if phone else None

            now = odoo_fields.Datetime.now()

            if visitor:
                if not visitor.visiting_date:
                    vals.setdefault('visiting_date', now)
                visitor.sudo().write(vals)
            else:
                vals.setdefault('visiting_date', now)
                visitor = request.env['visit.information'].sudo().create(vals)

            return {"Status": 1, "Message": "Form submitted successfully!", "VisitorID": visitor.id}
        except Exception as e:
            _logger.error(f"Error submitting form: {str(e)}")
            return {"Status": 0, "Message": f"Error: {str(e)}"}



    @http.route('/visitor/sendNotification', auth='public', type='http', methods=['POST'], csrf=False)
    def send_notification(self, **kw):
        try:
            # If JSON body is sent, parse it
            if request.httprequest.data:
                data = json.loads(request.httprequest.data.decode('utf-8'))
            else:
                data = kw  # fallback to form-data

            visitor_id = data.get("visitor_id")

            if not visitor_id:
                return request.make_response(
                    json.dumps({"Status": 0, "Message": "Visitor ID is required."}),
                    headers=[("Content-Type", "application/json")]
                )

            visitor = request.env["visit.information"].sudo().browse(int(visitor_id))
            if not visitor.exists():
                return request.make_response(
                    json.dumps({"Status": 0, "Message": "Visitor not found."}),
                    headers=[("Content-Type", "application/json")]
                )

            emp = visitor.employee
            if emp and emp.work_email:
                template = request.env.ref("visitor_management.email_visit_request")
                if template:
                    ctx = {
                        "default_email_from": emp.company_id.email or "no-reply@yourdomain.com",
                        "email_to": emp.work_email,
                        "lang": emp.user_id.lang or "en_US",
                    }
                    template.sudo().with_context(ctx).send_mail(visitor.id, force_send=True)
                    return request.make_response(
                        json.dumps({
                            "Status": 1,
                            "Message": "Notification sent to employee!",
                            "VisitorID": visitor.id
                        }),
                        headers=[("Content-Type", "application/json")]
                    )


        except Exception as e:
            _logger.error(f"Error sending notification: {str(e)}")
            return request.make_response(
                json.dumps({"Status": 0, "Message": f"Error: {str(e)}"}),
                headers=[("Content-Type", "application/json")]
            )


            
            
    @http.route('/visitor/requirements', type='json', auth='public', methods=['POST'], csrf=False)
    def visitor_requirements(self, **kw):
        try:
            payload = request.httprequest.get_json(force=True, silent=True) or {}
            visitor_id = payload.get("visitor_id")

            if not visitor_id:
                return {"Status": 0, "Message": "Visitor ID is required.", "Data": {}}

            visitor = request.env['visit.information'].sudo().browse(int(visitor_id))
            if not visitor.exists():
                return {"Status": 0, "Message": "Visitor not found.", "Data": {}}

            location = visitor.location_id

            return {
                "Status": 1,
                "Message": "Requirements",
                "VisitorID": visitor.id,
                "NDA": {
                    "Enabled": location.nda,
                    "Required": location.nda_required,
                },
                "Photo": {
                    "Enabled": location.photo,
                    "Required": location.photo_required,
                },
                "Questions": {
                    "Enabled": location.question,
                    "Required": location.question_required,
                },
            }

        except Exception as e:
            _logger.exception("Error in Visitor Requirements API")
            return {"Status": -1, "Message": f"Internal Server Error: {str(e)}", "Data": {}}


    @http.route('/visitor/nda_photo', auth='public', type='json', methods=['POST'], csrf=False)
    def nda_photo(self, **kw):
        try:
            data = request.get_json_data()

            visitor_id = data.get("visitor_id")
            nda_answer = data.get("nda_answer")   # Base64 image string
            photo_answer = data.get("photo_answer")  # Base64 image string

            if not visitor_id:
                return {"Status": 0, "Message": "Visitor ID is required"}

            visitor = request.env["visit.information"].sudo().browse(int(visitor_id))
            if not visitor:
                return {"Status": 0, "Message": "Visitor not found"}

            vals = {}
            if nda_answer:
                vals["nda_answer"] = nda_answer
            if photo_answer:
                vals["photo_answer"] = photo_answer  

            if vals:
                visitor.sudo().write(vals)

            # generate URLs for both NDA & Photo
            base_url = request.httprequest.host_url.rstrip('/')
            nda_url = f"{base_url}/web/image/visit.information/{visitor.id}/nda_answer" if visitor.nda_answer else ""
            photo_url = f"{base_url}/web/image/visit.information/{visitor.id}/photo_answer" if visitor.photo_answer else ""

            return {
                "Status": 1,
                "Message": "NDA/Photo updated successfully!",
                "VisitorID": visitor.id,
                "NDA_URL": nda_url,
                "PhotoURL": photo_url,
            }

        except Exception as e:
            return {"Status": 0, "Message": f"Error: {str(e)}"}



    @http.route('/company/getNDA', auth='public', type='http', methods=['GET'], csrf=False)
    def get_nda(self, **kw):
        try:
            location_id = kw.get("location_id")
            if not location_id:
                return request.make_response(
                    json.dumps({"Status": 0, "Message": "location_id is required"}),
                    headers=[("Content-Type", "application/json")]
                )

            location = request.env["company.location"].sudo().browse(int(location_id))
            if not location.exists():
                return request.make_response(
                    json.dumps({"Status": 0, "Message": "Location not found"}),
                    headers=[("Content-Type", "application/json")]
                )

            return request.make_response(
                json.dumps({
                    "Status": 1,
                    "Message": "NDA Content fetched successfully",
                    "Location": location.name,
                    "NDA": location.nda,
                    "NDARequired": location.nda_required,
                    "NDADetails": location.nda_details or ""
                }),
                headers=[("Content-Type", "application/json")]
            )

        except Exception as e:
            return request.make_response(
                json.dumps({"Status": 0, "Message": f"Error: {str(e)}"}),
                headers=[("Content-Type", "application/json")]
            )

class EmployeeAPI(http.Controller):

    @http.route('/visitor/employee', type='http', auth='public', methods=['GET'], csrf=False)
    def get_employees(self, **kwargs):
        try:
            # Limit the number of employees fetched
            employees = request.env['hr.employee'].sudo().search([], limit=100)

            # Use `read` to fetch only the necessary fields to reduce overhead
            employee_list = employees.read(['id', 'name', 'work_email', 'work_phone', 'job_title', 'department_id'])

            # Format the data for the response
            for emp in employee_list:
                # Adding the department name
                emp['department'] = emp['department_id'][1] if emp.get('department_id') else None
                del emp['department_id']  # Remove department_id from the response

            return request.make_json_response({
                "Status": 1 if employee_list else 0,
                "Message": "Employees fetched successfully." if employee_list else "No employees found.",
                "Data": employee_list
            })

        except Exception as e:
            # Log the exception for debugging
            _logger.exception("Error in fetching employees: %s", str(e))

            return request.make_json_response({
                "Status": 0,
                "Message": f"Error: {str(e)}",
                "Data": []
            }, status=500)

 
class VisitorFieldAPI(http.Controller):
    
    @http.route('/visitor/fields', type='http', auth='public', methods=['GET'], csrf=False)
    def get_visitor_fields(self, **kwargs):
        try:
            company_id = kwargs.get("company_id", request.env.company.id)
            location_id = kwargs.get("location_id")

            company = request.env['res.company'].sudo().browse(int(company_id))
            if not company.exists():
                return request.make_json_response({"Status": 0, "Message": "Invalid company ID", "Data": []})

            location = None
            if location_id:
                location = request.env['company.location'].sudo().browse(int(location_id))
                if not location.exists() or location.company_id.id != company.id:
                    return request.make_json_response({"Status": 0, "Message": "Invalid location ID for this company", "Data": []})

            domain = [('enabled', '=', True)]
            if location:
                domain.append(('location_id', '=', location.id))
            else:
                domain.append(('location_id.company_id', '=', company.id)) # now valid

            fields_cfg = request.env['company.field'].sudo().search(domain)

            data = [{
                "id": cfg.id,
                "field_id": cfg.field_id.id,
                "field_name": cfg.field_id.name,
                "label": cfg.label,
                "type": cfg.field_type,
                "required": cfg.required,
            } for cfg in fields_cfg]

            if data:
                return request.make_json_response({"Status": 1, "Message": "Fields fetched successfully", "Data": data})
            else:
                return request.make_json_response({"Status": 0, "Message": "No visitor fields configured", "Data": []})

        except Exception as e:
            _logger.exception("Error in fetching visitor fields: %s", str(e))
            return request.make_json_response({"Status": 0, "Message": f"Error: {str(e)}", "Data": []}, status=500)



class CompanyAPI(http.Controller):

    def _logo_b64(self, company, size_field):
        # Try requested size field first (image_128/256/512/1024/1920), else fallback to logo/image_1920
        bin_val = getattr(company, size_field, None) or company.logo or getattr(company, 'image_1920', None)
        if not bin_val:
            return ""
        # Odoo binary fields are base64; can be bytes or str depending on context
        return bin_val.decode('utf-8') if isinstance(bin_val, (bytes, bytearray)) else bin_val

    @http.route('/visitor/company', type='http', auth='public', methods=['GET'], csrf=False)
    def get_company(self, **kwargs):
        try:
            # optional: pick image size; default to 128 to keep responses small
            size = kwargs.get('size', '128')
            size_field = f"image_{size}" if size in {'128','256','512','1024','1920'} else 'image_128'

            companies = request.env['res.company'].sudo().search([], limit=100)

            data = []
            for company in companies:
                company_data = {
                    "id": company.id,
                    "name": company.name,
                    "email": company.email,
                    "phone": company.phone,
                    "website": company.website,
                    # base64 instead of URL
                    "logo_base64": self._logo_b64(company, size_field),
                    # if you prefer a data URI form for direct <img src>, uncomment next line:
                    # "logo_data_uri": f"data:image/png;base64,{self._logo_b64(company, size_field)}" if self._logo_b64(company, size_field) else ""
                }
                data.append(company_data)

            return request.make_json_response({
                "Status": 1 if data else 0,
                "Message": "Companies fetched successfully" if data else "No companies found",
                "Data": data
            })

        except Exception as e:
            _logger.exception("Error in fetching companies: %s", str(e))
            return request.make_json_response({
                "Status": 0,
                "Message": f"Error: {str(e)}",
                "Data": []
            }, status=500)


    @http.route('/visitor/company/create', type='http', auth='public', methods=['POST'], csrf=False)
    def create_company(self, **kwargs):
        try:
            # Parse raw JSON body
            data = json.loads(request.httprequest.data)

            if not data.get("name"):
                return request.make_json_response(
                    {"error": "Company name is required"}, status=400
                )

            company = request.env["res.company"].sudo().create({
                "name": data.get("name"),
                "street": data.get("street"),
                "city": data.get("city"),
                "state_id": data.get("state_id"),
                "country_id": data.get("country_id"),
                "phone": data.get("phone"),
                "email": data.get("email"),
                "website": data.get("website"),
            })

            return request.make_json_response({
                "success": True,
                "company_id": company.id,
                "message": f"Company '{company.name}' created successfully"
            }, status=200)

        except Exception as e:
            return request.make_json_response(
                {"error": str(e)}, status=500
            )


    @http.route('/visitor/company/<int:company_id>/locations', type='http', auth='public', methods=['GET'], csrf=False)
    def get_company_locations(self, company_id, **kwargs):
        company = request.env['res.company'].sudo().browse(company_id)
        if not company.exists():
            return request.make_response(
                json.dumps({"status": 0, "message": "Company not found", "data": []}),
                headers=[('Content-Type', 'application/json')]
            )

        locations = request.env['company.location'].sudo().search([('company_id', '=', company_id)])
        data = [{
            "id": loc.id,
            "name": loc.name,
            "NDA": {
                "Enabled": loc.nda,
                "Required": loc.nda_required,
            },
            "Photo": {
                "Enabled": loc.photo,
                "Required": loc.photo_required,
            },
            "Questions": {
                "Enabled": loc.question,
                "Required": loc.question_required,
            },
        } for loc in locations]

        return request.make_response(
            json.dumps({"status": 1, "message": "Locations fetched successfully", "data": data}),
            headers=[('Content-Type', 'application/json')]
        )





class VisitorQuestionController(http.Controller):

    @http.route('/visitor/get_questions', auth='public', type='http', methods=['POST'], csrf=False)
    def get_questions(self, **kw):
        """
        Get additional questions for a visitor's company and location.
        Accepts JSON body, form-data, or query params.
        """

        visitor_id = None

        # 1. Try JSON body
        if request.httprequest.data:
            try:
                data = json.loads(request.httprequest.data.decode())
                visitor_id = data.get("visitor_id")
            except Exception:
                pass

        # 2. Try form-data / query params
        if not visitor_id:
            visitor_id = kw.get("visitor_id") or request.params.get("visitor_id")

        # 3. Validate visitor_id
        if not visitor_id:
            return request.make_json_response({
                "Status": 0,
                "Message": "visitor_id is required"
            })

        try:
            visitor_id = int(visitor_id)
        except ValueError:
            return request.make_json_response({
                "Status": 0,
                "Message": "visitor_id must be an integer"
            })

        visitor = request.env["visit.information"].sudo().browse(visitor_id)
        if not visitor.exists():
            return request.make_json_response({
                "Status": 0,
                "Message": "Visitor not found"
            })

        company = visitor.company_id
        if not company:
            return request.make_json_response({
                "Status": 0,
                "Message": "Company not found for this visitor"
            })

        location = visitor.location_id
        if not location:
            return request.make_json_response({
                "Status": 0,
                "Message": "Location not set for this visitor"
            })

        # Build questions from location
        questions = [{
            "id": q.id,
            "question": q.question_text,
            "type": q.question_type,
            "required": q.required
        } for q in location.additional_question_ids]

        return request.make_json_response({
            "Status": 1,
            "VisitorID": visitor.id,
            "CompanyID": company.id,
            "LocationID": location.id,
            "Questions": questions
        })

    @http.route('/visitor/submit_answer', type='http', auth='public', csrf=False, methods=['POST'])
    def submit_notebook(self, **kwargs):
        """
        HTTP endpoint to submit visitor answers.
        Accepts JSON body:
        {
            "visitor_id": 32,
            "answers": [
                {"question_id": 1, "answer_selection": "yes"},
                {"question_id": 2, "answer_selection": "no"}
            ]
        }
        """
        try:
            # Read raw request body
            data = request.httprequest.get_data()
            if not data:
                return request.make_response(json.dumps({"error": "Empty request"}), headers=[('Content-Type', 'application/json')])

            # Parse JSON
            data = json.loads(data)
            visitor_id = data.get("visitor_id")
            answers = data.get("answers", [])

            if not visitor_id:
                return request.make_response(json.dumps({"error": "visitor_id is required"}), headers=[('Content-Type', 'application/json')])

            visitor = request.env['visit.information'].sudo().browse(visitor_id)
            if not visitor.exists():
                return request.make_response(json.dumps({"error": "Visitor not found"}), headers=[('Content-Type', 'application/json')])

            for ans in answers:
                question_id = ans.get("question_id")
                answer_selection = ans.get("answer_selection")

                if not question_id or answer_selection not in ('yes', 'no'):
                    continue

                notebook_entry = request.env['visitor.notebook.entry'].sudo().search([
                    ('visitor_id', '=', visitor.id),
                    ('question_id', '=', question_id)
                ], limit=1)

                if notebook_entry:
                    notebook_entry.sudo().write({'answer_selection': answer_selection})
                else:
                    request.env['visitor.notebook.entry'].sudo().create({
                        'visitor_id': visitor.id,
                        'question_id': question_id,
                        'answer_selection': answer_selection
                    })

            return request.make_response(
                json.dumps({"success": True, "message": "Answers submitted successfully"}),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.exception("Failed to submit visitor notebook answers")
            return request.make_response(
                json.dumps({"error": str(e)}),
                headers=[('Content-Type', 'application/json')]
            )

class VisitorBadgeController(http.Controller):

    @http.route('/visitor/badge/<int:visitor_id>', type='http', auth='public', csrf=False, methods=['GET'])
    def visitor_badge(self, visitor_id, **kwargs):
        try:
            # Load record with sudo so we can at least render the report itself
            visitor = request.env['visit.information'].sudo().browse(visitor_id)
            if not visitor.exists():
                raise NotFound("Visitor not found")

            report_svc = request.env['ir.actions.report'].sudo()
            pdf, _ = report_svc._render("visitor_management.action_visit_report", [visitor.id])

            filename = f"Visitor_Pass_{visitor.display_name or visitor.name or visitor.id}.pdf"
            return request.make_response(
                pdf,
                headers=[
                    ('Content-Type', 'application/pdf'),
                    ('Content-Length', str(len(pdf))),
                    ('Content-Disposition', f'inline; filename="{filename}"'),
                ],
            )

        except NotFound as e:
            return request.make_response(str(e), headers=[('Content-Type', 'text/plain')])
        except Exception as e:
            _logger.exception("Error generating visitor badge for id=%s", visitor_id)
            return request.make_response(f"Error generating badge: {e}", headers=[('Content-Type', 'text/plain')])

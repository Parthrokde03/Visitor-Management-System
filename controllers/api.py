# -*- coding: utf-8 -*-
import json
import random
import requests
import logging
from odoo import http
from odoo.http import Response, request
from datetime import date, datetime, timedelta
from odoo import _
from odoo.addons.bus.models.bus import dispatch

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
            action = payload.get("action")
            device = payload.get("device")

            _logger.info(f"Incoming Payload: {payload}")  # Debug
            
            data = {}

            if device != "1234":
                return {"Status": 0, "Message": f"Device id not matched. Got: {device}", "Data": data}
            
            visitor = request.env['visit.information'].sudo().search([
                ('qr_token', '=', token),
                ('status', '=', "approved")
            ], limit=1)

            if not visitor:
                return {"Status": 0, "Message": "QR does not match any registered visitor.", "Data": data}

            if not visitor.visiting_date or visitor.visiting_date.date() != date.today():
                return {"Status": 0, "Message": "Visitor registered, but not scheduled for today.", "Data": data}

            if action == "checkin":
                if visitor.check_out:
                    # User already checked out
                    return {"Status": 0, "Message": "Visitor has already checked out. Cannot check-in again.", "Data": data}
                
                if not visitor.check_in:
                    check_in_time = datetime.now()
                    visitor.sudo().write({'check_in': check_in_time})
                    
                    # Notification check-out :
                    if visitor.employee and visitor.employee.user_id:
                        message = {
                            'title': "Visitor Check-in",
                            'message': f" {visitor.name} has checked in at {check_in_time.strftime('%H:%M')}.",
                            'sticky': True,
                            'type': 'info'
                        }
                        request.env['bus.bus']._sendone(
                            visitor.employee.user_id.partner_id,
                            'simple_notification',
                            message
                        )
                        
                    data = {"name": visitor.name, "check_in": check_in_time.strftime("%Y-%m-%d %H:%M:%S"),"instruction": visitor.instructions}
                    return {"Status": 1, "Message": "Visitor check-in successful.", "Data": data}
                else:
                    return {"Status": 0, "Message": "Already checked in.", "Data": data}

            elif action == "checkout":
                if visitor.check_in and not visitor.check_out:
                    check_out_time = datetime.now()
                    visitor.sudo().write({'check_out': check_out_time})
                    
                    # Notification check-out :
                    if visitor.employee and visitor.employee.user_id:
                        message = {
                            'title': "Visitor Check-out",
                            'message': f" {visitor.name} has checked in at {check_out_time.strftime('%H:%M')}.",
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
                    return {"Status": 0, "Message": "Cannot check-out before check-in.", "Data": data}
                else:
                    return {"Status": 0, "Message": "Already checked out.", "Data": data}

            else:
                return {"Status": 0, "Message": "Invalid action. Use 'checkin' or 'checkout'.", "Data": data}

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
            action = payload.get("action")  # "checkin" or "checkout"

            _logger.info(f"Incoming OTP Payload: {payload}")

            if not mobile or not otp or not action:
                return {"Status": 0, "Message": "Invalid request. Mobile, OTP and action are required.", "Data": {}}

            # search
            today, tomorrow = self._get_today_range()
            visitor = request.env['visit.information'].sudo().search([
                ('phone', '=', mobile),
                ('visiting_date', '>=', today),
                ('visiting_date', '<', tomorrow)
            ], limit=1)
            
            # OTP check
            if not otp.isdigit() or int(otp) != int(visitor.otp_code or 0):
                return {"Status": 0, "Message": "Invalid OTP!", "Data": {}}

            # No record at all → user never requested OTP
            if not visitor or not visitor.name:
                return {"Status": 1, "Message": "New user - please register", "Data": {}, "Newuser": 1}

            if visitor.status != "approved":
                return {"Status": 0, "Message": f"Visitor not approved yet (status={visitor.status}).", "Data": {}}

            data = {}
            # === Check-in ===
            if action == "checkin":
                if visitor.check_out:
                    return {"Status": 0, "Message": "Already checked out. Cannot check-in again.", "Data": {}}

                if not visitor.check_in:
                    check_in_time = datetime.now()
                    visitor.sudo().write({'check_in': check_in_time})

                    # Notification check-in :
                    if visitor.employee and visitor.employee.user_id:
                        message = {
                            'title': "Visitor Check-in",
                            'message': f" {visitor.name} has checked in at {check_in_time.strftime('%H:%M')}.",
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
                    
                    # Notification check-out :
                    if visitor.employee and visitor.employee.user_id:
                        message = {
                            'title': "Visitor Check-out",
                            'message': f" {visitor.name} has checked in at {check_out_time.strftime('%H:%M')}.",
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
                        "check_out": check_out_time.strftime("%Y-%m-%d %H:%M:%S")
                    }
                    return {"Status": 1, "Message": "Visitor check-out successful.", "Data": data}
                elif not visitor.check_in:
                    return {"Status": 0, "Message": "Cannot check-out before check-in.", "Data": {}}
                else:
                    return {"Status": 0, "Message": "Already checked out.", "Data": {}}

            else:
                return {"Status": 0, "Message": "Invalid action. Use 'checkin' or 'checkout'.", "Data": {}}

        except Exception as e:
            _logger.exception("Error in OTP verification")
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
        today = datetime.combine(datetime.today(), datetime.min.time())
        tomorrow = today + timedelta(days=1)
        return today, tomorrow

    @http.route('/visitor/submitForm', auth='public', type='json', methods=['POST'], csrf=False)
    def submit_form(self, **kw):
        try:
            data = request.get_json_data()

            name = data.get("name")
            email = data.get("email")
            phone = data.get("phone")     
            company = data.get("company")   
            location_id = data.get("location_id")   # expect ID here
            employee = data.get("employee")
            purpose = data.get("purpose")

            if not name or not phone:
                return {"Status": 0, "Message": "Name and Phone are required."}

            # fetch employee first
            emp = request.env["hr.employee"].sudo().browse(int(employee)) if employee else False

            # Search for existing visitor created during OTP stage
            today, tomorrow = self._get_today_range()
            visitor = request.env["visit.information"].sudo().search([
                ('phone', '=', phone),
                ('visiting_date', '>=', today),
                ('visiting_date', '<', tomorrow)
            ], limit=1)

            vals = {
                "name": name,
                "email": email,
                "company": company,          
                "location_id": int(location_id) if location_id else False,
                "employee": int(employee) if employee else False,
                "purpose": purpose,
                "status": "pending",
                "visit_type": "walkin",
                "company_id": emp.company_id.id if emp else request.env.company.id  
            }

            if visitor:
                visitor.sudo().write(vals)
            else:
                vals.update({
                    "phone": phone,
                    "visiting_date": datetime.now(),
                })
                visitor = request.env["visit.information"].sudo().create(vals)

            # Send mail if employee has email
            if emp and emp.work_email:
                template = request.env.ref("visitor_management.email_visit_request")  
                if template:
                    template.sudo().send_mail(visitor.id, force_send=True)

            return {
                "Status": 1,
                "Message": "Form submitted successfully and email to employee!",
                "VisitorID": visitor.id,
                "RequireNDA": visitor.location_id.nda_required,
                "RequirePhoto": visitor.location_id.photo_required,
                "RequireQuestions": visitor.location_id.question_required,
            }

        except Exception as e:
            _logger.error(f"Error submitting form: {str(e)}")
            return {
                "Status": 0,
                "Message": f"Error: {str(e)}"
            }

            

    

    @http.route('/visitor/nda_photo', auth='public', type='json', methods=['POST'], csrf=False)
    def nda_photo(self, **kw):
        try:
            data = request.get_json_data()

            visitor_id = data.get("visitor_id")
            nda_answer = data.get("nda_answer")
            photo_answer = data.get("photo_answer")  

            if not visitor_id:
                return {"Status": 0, "Message": "Visitor ID is required"}

            visitor = request.env["visit.information"].sudo().browse(int(visitor_id))
            if not visitor:
                return {"Status": 0, "Message": "Visitor not found"}

            vals = {}
            if nda_answer is not None:
                vals["nda_answer"] = nda_answer

            if photo_answer:
                vals["photo_answer"] = photo_answer  

            visitor.sudo().write(vals)

            # generate url for frontend
            image_url = (
                f"{request.httprequest.host_url.rstrip('/')}/web/image/visit.information/{visitor.id}/photo_answer"
                if visitor.photo_answer else ""
            )

            return {
                "Status": 1,
                "Message": "NDA/Photo updated successfully!",
                "VisitorID": visitor.id,
                "PhotoURL": image_url
            }

        except Exception as e:
            return {"Status": 0, "Message": f"Error: {str(e)}"}




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
            # Get company_id and location_id from kwargs
            company_id = kwargs.get("company_id", request.env.company.id)
            location_id = kwargs.get("location_id")

            # Validate company
            company = request.env['res.company'].sudo().browse(int(company_id))
            if not company.exists():
                return request.make_json_response({
                    "Status": 0,
                    "Message": "Invalid company ID",
                    "Data": []
                })

            # If location_id is provided, validate it
            location = None
            if location_id:
                location = request.env['company.location'].sudo().browse(int(location_id))  
                # 👆 replace `company.location` with your actual model name for locations
                if not location.exists() or location.company_id.id != company.id:
                    return request.make_json_response({
                        "Status": 0,
                        "Message": "Invalid location ID for this company",
                        "Data": []
                    })

            # Fetch company fields
            domain = [('enabled', '=', True)]
            if location:
                domain.append(('location_id', '=', location.id))
            else:
                domain.append(('company_id', '=', company.id))

            fields = request.env['company.field'].sudo().search(domain)

            fields_data = [{
                "id": field.id,
                "field_id": field.field_id.id,
                "field_name": field.field_id.name,
                "label": field.label,
                "type": field.field_type,
                "required": field.required,
            } for field in fields]


            # Response
            if fields_data:
                return request.make_json_response({
                    "Status": 1,
                    "Message": "Fields fetched successfully",
                    "Data": fields_data
                })
            else:
                return request.make_json_response({
                    "Status": 0,
                    "Message": "No visitor fields configured",
                    "Data": []
                })

        except Exception as e:
            _logger.exception("Error in fetching visitor fields: %s", str(e))
            return request.make_json_response({
                "Status": 0,
                "Message": f"Error: {str(e)}",
                "Data": []
            }, status=500)



class CompanyAPI(http.Controller):

    @http.route('/visitor/company', type='http', auth='public', methods=['GET'], csrf=False)
    def get_company(self, **kwargs):
        try:
            # Limit the number of companies fetched to improve performance
            companies = request.env['res.company'].sudo().search([], limit=100)

            # Fetch only the necessary fields with `read` method, reducing overhead
            data = companies.read(['id', 'name', 'email', 'phone', 'website'])

            # Return response based on whether companies were found
            return request.make_json_response({
                "Status": 1 if data else 0,
                "Message": "Companies fetched successfully" if data else "No companies found",
                "Data": data
            })

        except Exception as e:
            # Log the exception for better debugging
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
            "nda_required": loc.nda_required,
            "photo_required": loc.photo_required,
            "question_required": loc.question_required,
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

    @http.route('/visitor/download_badge', type='http', auth='public', csrf=False, methods=['GET'])
    def download_badge(self, visitor_id=None, **kwargs):
        """
        HTTP endpoint to download visitor badge PDF.

        Usage: GET /visitor/download_badge?visitor_id=32
        """
        try:
            if not visitor_id:
                return request.make_response(
                    "visitor_id parameter is required",
                    headers=[('Content-Type', 'text/plain')]
                )

            visitor = request.env['visit.information'].sudo().browse(int(visitor_id))
            if not visitor.exists():
                return request.make_response(
                    "Visitor not found",
                    headers=[('Content-Type', 'text/plain')]
                )

            # Generate PDF using existing report
            report = request.env.ref('visitor_management.action_visit_report', raise_if_not_found=False)
            if not report:
                return request.make_response(
                    "Badge report template not found",
                    headers=[('Content-Type', 'text/plain')]
                )

            pdf_content, _ = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
                'visitor_management.action_visit_report', visitor.id
            )

            # Return PDF as attachment
            response = request.make_response(
                pdf_content,
                headers=[
                    ('Content-Type', 'application/pdf'),
                    ('Content-Disposition', f'attachment; filename=Visitor_Badge_{visitor.name}.pdf')
                ]
            )
            return response

        except Exception as e:
            _logger.exception("Failed to generate badge PDF for visitor %s", visitor_id)
            return request.make_response(
                str(e),
                headers=[('Content-Type', 'text/plain')]
            )

import io
import json
import base64
import os
import crcmod
import qrcode
from django import forms
from django.utils.translation import gettext_lazy as _
from pretix.base.payment import BasePaymentProvider
from collections import OrderedDict
from django.template.loader import get_template
from i18nfield.forms import I18nFormField, I18nTextarea
from i18nfield.strings import LazyI18nString

# Import the promptpay payload engine
from promptpay import qrcode as promptpay_qr

class PromptPaySlipForm(forms.Form):
    slip = forms.FileField(
        label=_('Upload Payment Slip'),
        help_text=_('Please upload a screenshot or image of your successful PromptPay transfer receipt.'),
        required=True
    )

    def clean_slip(self):
        slip = self.cleaned_data.get('slip')
        if slip:
            ext = os.path.splitext(slip.name)[1].lower()
            if ext not in ['.jpg', '.jpeg', '.png']:
                raise forms.ValidationError(_('Only JPG, JPEG, or PNG images are accepted.'))
        return slip

class PromptPayProvider(BasePaymentProvider):
    identifier = 'promptpay'
    verbose_name = _('PromptPay / QR Code')
    public_name = _('PromptPay')

    @property
    def settings_form_fields(self):
        """
        Defines the configuration fields shown in the pretix organizer backend.
        """
        return OrderedDict(
            list(super().settings_form_fields.items()) + [
                ('promptpay_id', forms.CharField(
                    label=_('PromptPay ID / Account'),
                    help_text=_(
                        'Enter your 10-digit mobile number (e.g., 0812345678) '
                        'or your 13-digit Corporate/Tax ID linked to your PromptPay account.'
                    ),
                    required=True
                )),
                (
                    'payment_instructions',
                    I18nFormField(
                        label=_('Payment instructions'),
                        help_text=_('Displayed to the customer during checkout.'),
                        required=False,
                        widget=I18nTextarea
                )),
            ]
        )

    def payment_pending_render(self, request, payment) -> str:
        """
        Renders the actual PromptPay QR code with exact encoded totals.
        """
        # 1. Pull settings & details
        # Accepts standard 10-digit mobile (e.g., 0812345678) or 13-digit Tax ID
        account_id = self.settings.get('promptpay_id').strip().replace("-", "")
        amount = float(payment.amount) 
        event_slug = request.event.slug
        ref1 = f"PRETIX{event_slug}"
        ref2 = payment.order.code

        # def generate_payload(biller_id, amount,ref1,ref2="0"):
        formatted_amount = f"{amount:.2f}"
        tag30_value = (
            "0016A000000677010112"
            f"0115{account_id}09"
            f"02{len(ref1):02d}{ref1}"
            f"03{len(ref2):02d}{ref2}"
        )

        payload = (
            "000201"
            "010211"
            f"30{len(tag30_value):02d}{tag30_value}"
            "5303764"
            f"54{len(formatted_amount):02d}{formatted_amount}"
            "5802TH"
        )

        payload += "6304"
        crc16 = crcmod.predefined.Crc('crc-ccitt-false')
        crc16.update(payload.encode('utf-8'))
        checksum = f"{crc16.crcValue:04X}"

        payload += checksum


        # 2. Build the strict EMVCo compliant PromptPay string
        # Result looks like: "00020101021229370016A000000677010111..."
        # payload = promptpay_qr.generate_payload(account_id, amount)

        # 3. Render the raw string payload into a QR matrix image
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0f2642", back_color="white") # Dark blue theme optional

        # 4. Stream image payload out as base64 asset bytes
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        qr_base64 = base64.b64encode(buffer.getvalue()).decode()

        # Instantiate the form to render alongside the QR code
        form = PromptPaySlipForm(
            # Pass POST data if the user just clicked submit and failed validation
            data=request.POST if request.method == 'POST' and request.POST.get('payment_action') == 'promptpay_upload' else None,
            files=request.FILES if request.method == 'POST' and request.POST.get('payment_action') == 'promptpay_upload' else None
        )

        # 5. Serve context data directly to your frontend template
        template = get_template('pretix_promptpay/pending.html')
        raw_instructions = self.settings.get('payment_instructions')
        if isinstance(raw_instructions, str) and raw_instructions.startswith('{'):
            try:
                raw_instructions = json.loads(raw_instructions)
            except json.JSONDecodeError:
                pass

        if isinstance(raw_instructions, dict):
            instructions = LazyI18nString(raw_instructions)
        else:
            instructions = raw_instructions
        context = {
            'qr_code_img': f"data:image/png;base64,{qr_base64}",
            'amount': payment.amount,
            'payment': payment,
            'order': payment.order,
            'form': form,
            'instructions': instructions,
        }
        return template.render(context, request)

    def payment_control_perform(self, request, payment) -> str:
        """
        Handles actions initiated by the organizer in the backend dashboard.
        """
        if request.method == "POST" and request.POST.get('payment_action') == 'promptpay_approve':
            payment.confirm(user=request.user)
            payment.order.log_action('pretix.plugins.pretix_promptpay.slip_approved', data={
                'payment': payment.pk,
                'user': request.user.email if request.user else 'System'
            })
            return None
            
        return None

    def payment_control_render(self, request, payment) -> str:
        template = get_template('pretix_promptpay/control.html')
        slip_path = payment.info_data.get('slip_path', '') if payment.info_data else ''
        
        context = {
            'payment': payment,
            'slip_path': slip_path,
        }
        return template.render(context, request)

    def payment_is_valid_session(self, request) -> bool:
        """
        Validates if the current checkout session state is sufficient.
        Returns True if the user has selected PromptPay and it is ready.
        """
        # Since PromptPay requires no upfront choices (like credit card inputs)
        # during checkout, we just check if it's the active selection.
        return request.session.get('payment', '') == self.identifier

    def checkout_prepare(self, request, total) -> bool:
        """
        Called when the user clicks 'Continue' on the payment selection step.
        Saves the provider selection to the user's session storage.
        """
        request.session['payment'] = self.identifier
        return True

    def execute_payment(self, request, payment) -> str:
        """
        Called immediately after the user submits the final confirmation step.
        """
        # For PromptPay, we don't charge an external API immediately.
        # We return None to let pretix proceed to the 'Order Pending' screen,
        # where your `payment_pending_render` method handles showing the QR code.
        return None

    def checkout_confirm_render(self, request, info_data: dict = None) -> str:
        """
        Renders a simple textual notice on the final checkout confirmation page 
        right before the user clicks 'Submit Order'.
        """
        template = get_template('pretix_promptpay/checkout_confirm.html')
        return template.render({}, request)

    def order_pending_handle_form(self, request, payment) -> str:
        """
        Intercepts and processes the customer's slip upload POST submission
        from the presale order detail page.
        """
        if request.method == "POST" and request.POST.get('payment_action') == 'promptpay_upload':
            form = PromptPaySlipForm(request.POST, request.FILES)
            
            if form.is_valid():
                slip_file = request.FILES['slip']
                from pretix.base.models import EventFile
                
                # Save file via pretix EventFile framework
                ef = EventFile.objects.create(
                    event=request.event,
                    filename=slip_file.name,
                    type='image/png' if slip_file.name.endswith('.png') else 'image/jpeg'
                )
                ef.file.save(slip_file.name, slip_file)
                
                # Update payment tracking data block
                info = payment.info_data or {}
                info['slip_path'] = ef.file.url
                payment.info_data = info
                payment.state = payment.STATE_PENDING
                payment.save()
                
                # Track event execution in order logs
                payment.order.log_action('pretix.plugins.pretix_promptpay.slip_uploaded', data={
                    'payment': payment.pk,
                    'file': ef.file.url
                })
                
                # Return True or None to let pretix know the operation succeeded 
                # and to refresh the order page layout cleanly.
                return None
            else:
                # Returning True tells pretix that we handled the POST, 
                # but invalid validation will fall back to displaying form errors
                return None
                
        return None

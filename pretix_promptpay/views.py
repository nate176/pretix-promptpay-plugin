import os
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import View
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from pretix.base.models import CachedFile, OrderPayment, Order

class PromptPaySlipUploadView(View):
    def post(self, request, *args, **kwargs):
        order_code = kwargs.get('order')
        secret = kwargs.get('secret')
        
        order = get_object_or_404(request.event.orders, code=order_code, secret=secret)
        
        payment = order.payments.filter(
            provider='promptpay', 
            state__in=['created', 'pending']
        ).last()

        if not payment:
            messages.error(request, _("No active pending PromptPay transaction found for this order."))
            return redirect(self.get_order_url(request, order))

        if 'slip' not in request.FILES:
            messages.error(request, _("No file was uploaded."))
            return redirect(self.get_order_url(request, order))

        slip_file = request.FILES['slip']
        
        ext = os.path.splitext(slip_file.name)[1].lower()
        if ext not in ['.jpg', '.jpeg', '.png']:
            messages.error(request, _("Only JPG, JPEG, or PNG images are accepted."))
            return redirect(self.get_order_url(request, order))

        # --- FIX: Save to Public Web Storage Path ---
        # Generate a distinct path inside the event's public media folder namespace
        filename = f"pub/event/{request.event.slug}/promptpay_slips/{order.code}_{payment.pk}{ext}"
        
        # Save raw stream directly into the shared media volume
        saved_path = default_storage.save(filename, ContentFile(slip_file.read()))
        
        # Pull the absolute direct public download URL address
        public_url = default_storage.url(saved_path)

        # Map reference link to metadata block
        info = payment.info_data or {}
        info['slip_path'] = public_url
        payment.info_data = info
        payment.state = 'pending'
        payment.save()

        # Record state history tracking entry
        order.log_action('pretix.plugins.pretix_promptpay.slip_uploaded', data={
            'payment': payment.pk,
            'file': public_url
        })

        messages.success(request, _("Your bank transfer slip was uploaded successfully!"))
        return redirect(self.get_order_url(request, order))

    def get_order_url(self, request, order):
        from django.urls import reverse
        return reverse('presale:event.order', kwargs={
            'event': request.event.slug,
            'organizer': request.organizer.slug,
            'order': order.code,
            'secret': order.secret
        })

class PromptPayBulkReviewView(View):
    """
    Organizer control panel dashboard page to verify multiple pending transfer receipts at once.
    """
    def dispatch(self, request, *args, **kwargs):
        # Explicitly check permission since we dropped the EventViewMixin base
        if not request.user.has_event_permission(request.organizer, request.event, 'can_change_orders', request=request):
            raise PermissionDenied(_("You do not have permission to view this page."))
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        # Retrieve all pending payments specifically for this payment plugin identifier
        pending_payments = OrderPayment.objects.filter(
            order__event=request.event,
            provider='promptpay',
            state='pending'
        ).select_related('order')

        return render(request, 'pretix_promptpay/bulk_review.html', {
            'payments': pending_payments,
        })

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')
        payment_ids = request.POST.getlist('payment_ids')

        if not payment_ids:
            messages.warning(request, _("No payments were selected."))
            return redirect(request.path_info)

        # Look up valid pending target records strictly tied to this event namespace
        payments = OrderPayment.objects.filter(
            id__in=payment_ids,
            order__event=request.event,
            provider='promptpay',
            state='pending'
        )

        success_count = 0
        if action == "approve":
            for payment in payments:
                try:
                    # Leverage pretix base logic to change order status, assign mark, & fire confirmation emails
                    payment.confirm(user=request.user)
                    payment.order.log_action('pretix.plugins.pretix_promptpay.slip_approved', data={
                        'payment': payment.pk,
                        'user': request.user.email if request.user else 'System'
                    })
                    success_count += 1
                except Exception as e:
                    messages.error(request, _("Error confirming order {code}: {error}").format(code=payment.order.code, error=str(e)))

            if success_count > 0:
                messages.success(request, _("Successfully approved and confirmed {count} order(s).").format(count=success_count))

        elif action == "reject":
            # For rejections, we transition back to "Failed" state so the user can re-upload
            for payment in payments:
                payment.state = 'failed'
                payment.save()
                payment.order.log_action('pretix.plugins.pretix_promptpay.slip_rejected', data={
                    'payment': payment.pk,
                    'user': request.user.email if request.user else 'System'
                })
                success_count += 1
            
            if success_count > 0:
                messages.warning(request, _("Rejected {count} slip(s). Customers can now upload a new receipt.").format(count=success_count))

        return redirect(request.path_info)

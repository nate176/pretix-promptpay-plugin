from django.dispatch import receiver
from django.urls import reverse
from django.utils.translation import gettext as _
from pretix.base.signals import register_payment_providers
from pretix.control.signals import nav_event

@receiver(register_payment_providers, dispatch_uid="payment_promptpay")
def register_payment_provider(sender, **kwargs):
    from .payment import PromptPayProvider
    return PromptPayProvider

@receiver(nav_event, dispatch_uid="payment_promptpay_nav")
def control_nav_import(sender, request, **kwargs):
    if not request.user.has_event_permission(request.organizer, request.event, 'can_change_orders', request=request):
        return []
        
    url = f"/control/event/{request.organizer.slug}/{request.event.slug}/orders/promptpay/bulk-review/"
    
    # Fix: Query OrderPayment models filtered down by the current event instance context
    from pretix.base.models import OrderPayment
    pending_count = OrderPayment.objects.filter(
        order__event=request.event,
        provider='promptpay', 
        state='pending'
    ).count()
    
    label = _("PromptPay Slips")
    if pending_count > 0:
        label = f"{label} ({pending_count})"

    return [
        {
            'label': label,
            'url': url,
            'active': request.path == url,
            'icon': 'qrcode',
            'parent': 'orders'
        }
    ]

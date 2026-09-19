from django.urls import re_path
from pretix.presale.utils import event_view
from .views import PromptPaySlipUploadView, PromptPayBulkReviewView

# Public-facing customer routes
event_patterns = [
    re_path(
        r'^checkout/(?P<order>[A-Z0-9]{5})/(?P<secret>[A-Za-z0-9]{16})/promptpay/upload/$',
        event_view(PromptPaySlipUploadView.as_view()),
        name='slip_upload'
    ),
]

# Organizer dashboard backend routes
urlpatterns = [
    re_path(
        r'^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/orders/promptpay/bulk-review/$', # 👈 Cleaned: Removed duplicate path prefixes
        PromptPayBulkReviewView.as_view(),
        name='promptpay_bulk_review'  
    ),
]

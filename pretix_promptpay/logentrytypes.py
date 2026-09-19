from django.utils.translation import gettext_lazy as _
from pretix.base.logentrytypes import LogEntryType, log_entry_types

class PromptPaySlipUploadedLogType(LogEntryType):
    action_type = 'pretix.plugins.pretix_promptpay.slip_uploaded'

    def render(self, logentry):
        data = logentry.parsed_data
        file_url = data.get('file', '#')
        return _("PromptPay bank transfer slip uploaded. <a href='{url}' target='_blank'>View File</a>").format(url=file_url)


class PromptPaySlipApprovedLogType(LogEntryType):
    action_type = 'pretix.plugins.pretix_promptpay.slip_approved'

    def render(self, logentry):
        data = logentry.parsed_data
        user_email = data.get('user', _('Organizer staff'))
        return _("PromptPay transfer verified and payment approved by {user}.").format(user=user_email)


# Register them both into pretix's core log registry
log_entry_types.register(PromptPaySlipUploadedLogType())
log_entry_types.register(PromptPaySlipApprovedLogType())

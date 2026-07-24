def profile(request):
    context = {
        'vault_profile': getattr(request.user, 'vault_profile', None)
        if request.user.is_authenticated else None,
        'sensitive_window_expires_at': None,
    }
    if request.user.is_authenticated:
        from .protected_operations import current_operation_window
        window = current_operation_window(request)
        if window:
            context['sensitive_window_expires_at'] = window.expires_at.isoformat()
    return context

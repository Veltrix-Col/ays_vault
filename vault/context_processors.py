def profile(request):
    return {'vault_profile':getattr(request.user,'vault_profile',None) if request.user.is_authenticated else None}

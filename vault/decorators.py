from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
def role_required(*roles):
    def dec(view):
        @login_required
        @wraps(view)
        def inner(request,*a,**k):
            p=getattr(request.user,'vault_profile',None)
            if not p or not p.active or p.role not in roles: raise PermissionDenied
            return view(request,*a,**k)
        return inner
    return dec

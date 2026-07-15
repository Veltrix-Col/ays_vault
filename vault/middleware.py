from .security import audit
class AuditAccessMiddleware:
    def __init__(self,get_response): self.get_response=get_response
    def __call__(self,request):
        response=self.get_response(request)
        if request.user.is_authenticated and request.path.startswith('/vault/') and request.method=='GET': audit(request,'ACCESS',metadata={'path':request.path})
        return response

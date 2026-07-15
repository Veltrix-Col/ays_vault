from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import JsonResponse,HttpResponseBadRequest
from django.shortcuts import get_object_or_404,redirect,render
from django.views.decorators.http import require_POST
from .decorators import role_required
from .forms import CardForm,RevealForm
from .models import PaymentCard,AuditEvent,SecurityAlert,UserProfile
from .security import audit
@login_required
def dashboard(request):
    return render(request,'vault/dashboard.html',{'cards':PaymentCard.objects.filter(active=True).count(),'events_today':AuditEvent.objects.count(),'alerts':SecurityAlert.objects.filter(status='NEW').count(),'recent':AuditEvent.objects.select_related('user','card')[:10]})
@role_required(UserProfile.LEADER,UserProfile.ANALYST)
def card_list(request): return render(request,'vault/card_list.html',{'cards':PaymentCard.objects.order_by('client_name')})
@role_required(UserProfile.LEADER)
def card_create(request):
    form=CardForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        card=form.save(user=request.user); audit(request,'CREATE',card,reason='Registro de tarjeta'); messages.success(request,'Tarjeta registrada y cifrada.'); return redirect('vault:card_detail',card.pk)
    return render(request,'vault/card_form.html',{'form':form,'title':'Nueva tarjeta'})
@role_required(UserProfile.LEADER,UserProfile.ANALYST)
def card_detail(request,pk):
    card=get_object_or_404(PaymentCard,pk=pk); audit(request,'VIEW',card); return render(request,'vault/card_detail.html',{'card':card,'form':RevealForm(user=request.user),'events':card.auditevent_set.select_related('user')[:15]})
@require_POST
@role_required(UserProfile.LEADER,UserProfile.ANALYST)
def reveal(request,pk):
    card=get_object_or_404(PaymentCard,pk=pk,active=True); form=RevealForm(request.POST,user=request.user)
    if not form.is_valid(): return JsonResponse({'ok':False,'errors':form.errors.get_json_data()},status=400)
    field=form.cleaned_data['field']; value=card.get_pan() if field=='pan' else card.get_expiry(); audit(request,'REVEAL',card,field,form.cleaned_data['reason']); return JsonResponse({'ok':True,'value':value,'expires_in':25})
@require_POST
@role_required(UserProfile.LEADER,UserProfile.ANALYST)
def copy_event(request,pk):
    card=get_object_or_404(PaymentCard,pk=pk,active=True); field=request.POST.get('field',''); reason=request.POST.get('reason','')
    if field not in {'pan','expiry'} or not reason:return HttpResponseBadRequest('Datos incompletos')
    audit(request,'COPY',card,field,reason); return JsonResponse({'ok':True})
@role_required(UserProfile.ADMIN,UserProfile.LEADER)
def audit_list(request): return render(request,'vault/audit.html',{'events':AuditEvent.objects.select_related('user','card')[:250]})

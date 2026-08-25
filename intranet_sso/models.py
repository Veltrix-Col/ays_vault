from django.conf import settings
from django.db import models


class IntranetPrincipal(models.Model):
    """Identidad SSO de la intranet, provisionada como User Django propio.

    Las apps heredadas por SSO (soat, conciliacion, cotizacion_colectivos) no
    tienen login de Django: su unico gate es TrustedIntranetAccessMiddleware.
    Para que puedan atribuir cada accion a una persona real (permisos,
    asignaciones, notificaciones, auditoria), se crea aqui un User dedicado
    por cada 'sub' de WordPress la primera vez que se valida. Esta cuenta
    nunca se cruza con las de CardManager (vault): siempre tiene contrasena
    inutilizable, no es staff ni superusuario, y no se busca ni reutiliza
    ningun User existente al provisionarla.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="intranet_principal",
    )
    subject = models.CharField(max_length=190, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)


class ConsumedAssertion(models.Model):
    """Registra el jti de cada aserción SSO de la intranet ya canjeada.

    La unicidad de jti es lo que impide reproducir (replay) una misma
    aserción firmada por WordPress dentro de su corta ventana de validez.
    """

    jti = models.CharField(max_length=64, unique=True)
    subject = models.CharField(max_length=190)
    consumed_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=["expires_at"])]

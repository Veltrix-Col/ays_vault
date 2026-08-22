from django.db import models


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

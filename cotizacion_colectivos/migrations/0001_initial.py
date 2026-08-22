import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="SolicitudColectivo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("public_id", models.CharField(editable=False, max_length=24, unique=True)),
                ("source_kind", models.CharField(choices=[("company", "Empresa"), ("person", "Individuo")], max_length=12)),
                ("source_reference_hash", models.CharField(db_index=True, max_length=64)),
                ("policy_reference_hash", models.CharField(db_index=True, max_length=64)),
                ("encrypted_policy_token", models.TextField(editable=False)),
                ("masked_policy_reference", models.CharField(max_length=80)),
                ("client_label", models.CharField(max_length=180)),
                ("branch_code", models.CharField(db_index=True, max_length=8)),
                ("branch_name", models.CharField(max_length=100)),
                ("request_type", models.CharField(choices=[("ACTUALIZACION", "Actualización de datos"), ("RENOVACION", "Renovación"), ("INCLUSION", "Inclusión"), ("RETIRO", "Retiro"), ("MODIFICACION", "Modificación"), ("COTIZACION", "Cotización"), ("OTRO", "Otro")], db_index=True, max_length=20)),
                ("status", models.CharField(choices=[("BORRADOR", "Borrador"), ("LISTA_PARA_ENVIAR", "Lista para enviar"), ("ENVIADA", "Enviada"), ("ABIERTA_POR_CLIENTE", "Abierta por cliente"), ("RESPONDIDA", "Respondida"), ("EN_REVISION", "En revisión"), ("REQUIERE_CORRECCION", "Requiere corrección"), ("APROBADA", "Aprobada"), ("PENDIENTE_ZOHO", "Pendiente Zoho"), ("CARGADA_ZOHO", "Cargada Zoho"), ("CERRADA", "Cerrada"), ("VENCIDA", "Vencida"), ("CANCELADA", "Cancelada")], db_index=True, default="BORRADOR", max_length=24)),
                ("deadline", models.DateField(db_index=True)),
                ("zoho_profile", models.CharField(max_length=12)),
                ("snapshot_version", models.PositiveSmallIntegerField(default=1)),
                ("snapshot_revision", models.PositiveIntegerField(default=1)),
                ("encrypted_snapshot", models.TextField(editable=False)),
                ("record_count", models.PositiveIntegerField(default=0)),
                ("warnings", models.JSONField(blank=True, default=list)),
                ("encrypted_internal_notes", models.TextField(blank=True)),
                ("origin", models.CharField(choices=[("INTERNO", "Interno"), ("FORMULARIO_WEB", "Formulario web futuro"), ("EXCEL", "Excel futuro")], default="INTERNO", max_length=20)),
                ("is_test", models.BooleanField(db_index=True, default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("assigned_to", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="solicitudes_colectivos_asignadas", to=settings.AUTH_USER_MODEL)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="solicitudes_colectivos_creadas", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ("-created_at",),
                "permissions": (("view_requests", "Puede ver solicitudes de Colectivos"), ("create_requests", "Puede crear solicitudes de Colectivos"), ("edit_requests", "Puede editar borradores de Colectivos"), ("assign_requests", "Puede asignar solicitudes de Colectivos"), ("approve_requests", "Puede aprobar solicitudes de Colectivos"), ("close_requests", "Puede cerrar solicitudes de Colectivos"), ("cancel_requests", "Puede cancelar solicitudes de Colectivos"), ("export_excel", "Puede exportar Excel de Colectivos"), ("view_economic_data", "Puede ver información económica de Colectivos"), ("view_personal_data", "Puede ver datos personales de Colectivos"), ("manage_notifications", "Puede gestionar notificaciones de Colectivos")),
            },
        ),
        migrations.CreateModel(
            name="SolicitudColectivoRegistro",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("element_type", models.CharField(choices=[("PERSONA", "Persona"), ("BENEFICIARIO", "Beneficiario"), ("INMUEBLE", "Inmueble"), ("VEHICULO", "Vehículo"), ("OBLIGACION", "Obligación"), ("OTRO", "Otro")], max_length=20)),
                ("role", models.CharField(max_length=40)),
                ("external_reference_hash", models.CharField(max_length=64)),
                ("initial_status", models.CharField(blank=True, max_length=80)),
                ("entry_date", models.DateField(blank=True, null=True)),
                ("exit_date", models.DateField(blank=True, null=True)),
                ("plan", models.CharField(blank=True, max_length=120)),
                ("economic_values", models.JSONField(blank=True, default=dict)),
                ("encrypted_branch_payload", models.TextField(blank=True)),
                ("original_position", models.PositiveIntegerField()),
                ("checksum", models.CharField(max_length=64)),
                ("active", models.BooleanField(default=True)),
                ("request", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="records", to="cotizacion_colectivos.solicitudcolectivo")),
            ],
            options={"ordering": ("original_position",)},
        ),
        migrations.CreateModel(
            name="EventoSolicitudColectivo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(max_length=40)),
                ("previous_status", models.CharField(blank=True, max_length=24)),
                ("new_status", models.CharField(blank=True, max_length=24)),
                ("safe_metadata", models.JSONField(blank=True, default=dict)),
                ("correlation_id", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("origin", models.CharField(default="INTERNO", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("request", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="cotizacion_colectivos.solicitudcolectivo")),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.CreateModel(
            name="NotificacionColectivos",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("notification_type", models.CharField(max_length=40)),
                ("title", models.CharField(max_length=120)),
                ("message", models.CharField(max_length=240)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                ("priority", models.CharField(default="NORMAL", max_length=12)),
                ("deduplication_key", models.CharField(max_length=120)),
                ("request", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notifications", to="cotizacion_colectivos.solicitudcolectivo")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notificaciones_colectivos", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.AddIndex(model_name="solicitudcolectivo", index=models.Index(fields=["status", "deadline"], name="colect_status_deadline")),
        migrations.AddIndex(model_name="solicitudcolectivo", index=models.Index(fields=["assigned_to", "status"], name="colect_owner_status")),
        migrations.AddIndex(model_name="solicitudcolectivo", index=models.Index(fields=["branch_code", "request_type"], name="colect_branch_type")),
        migrations.AddIndex(model_name="solicitudcolectivoregistro", index=models.Index(fields=["request", "element_type"], name="colect_request_element")),
        migrations.AddConstraint(model_name="solicitudcolectivoregistro", constraint=models.UniqueConstraint(fields=("request", "original_position"), name="colect_request_position_unique")),
        migrations.AddIndex(model_name="notificacioncolectivos", index=models.Index(fields=["user", "read_at"], name="colect_user_unread")),
        migrations.AddConstraint(model_name="notificacioncolectivos", constraint=models.UniqueConstraint(fields=("user", "deduplication_key"), name="colect_notification_dedup")),
    ]

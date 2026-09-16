from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from email_exceptions.models import ExceptionCase


class CaseInboxTests(TestCase):
    def setUp(self):
        self.operator = get_user_model().objects.create_user(
            username="inbox-operator", first_name="Ana", last_name="Analista"
        )
        self.operator.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="email_exceptions",
                codename="view_email_exceptions_operational",
            ),
            Permission.objects.get(
                content_type__app_label="email_exceptions",
                codename="operate_email_exceptions",
            ),
        )
        self.client.force_login(self.operator)
        self.now = timezone.now()

    def case(self, key, **kwargs):
        values = {
            "case_key": key,
            "opened_at": self.now,
            "last_activity_at": self.now,
        }
        values.update(kwargs)
        return ExceptionCase.objects.create(**values)

    def ids_for(self, **params):
        response = self.client.get(reverse("email_exceptions:list"), params)
        self.assertEqual(response.status_code, 200)
        return list(response.context["page_obj"].object_list), response

    def test_quick_views_count_cases_and_exclude_closed_from_follow_up(self):
        mine = self.case("inbox-mine", assigned_to=self.operator)
        unassigned = self.case("inbox-unassigned")
        overdue = self.case("inbox-overdue", follow_up_at=self.now - timedelta(days=1))
        today = self.case("inbox-today", follow_up_at=self.now + timedelta(hours=1))
        future = self.case("inbox-future", follow_up_at=self.now + timedelta(days=2))
        closed = self.case(
            "inbox-closed", status=ExceptionCase.Status.CLOSED,
            follow_up_at=self.now - timedelta(days=2),
        )

        cases, response = self.ids_for(quick="mine")
        self.assertEqual([case.pk for case in cases], [mine.pk])
        self.assertEqual(response.context["quick_counts"], {
            "all": 6, "mine": 1, "unassigned": 5, "overdue": 1, "today": 1,
        })
        self.assertEqual([case.pk for case in self.ids_for(quick="unassigned")[0]], [overdue.pk, today.pk, future.pk, unassigned.pk, closed.pk])
        self.assertEqual([case.pk for case in self.ids_for(quick="overdue")[0]], [overdue.pk])
        self.assertEqual([case.pk for case in self.ids_for(quick="today")[0]], [today.pk])

    def test_operational_order_places_overdue_then_today_then_future_then_closed(self):
        overdue = self.case("order-overdue", follow_up_at=self.now - timedelta(hours=2))
        today = self.case("order-today", follow_up_at=self.now + timedelta(hours=2))
        future = self.case("order-future", follow_up_at=self.now + timedelta(days=2))
        no_follow_up = self.case("order-none")
        closed = self.case("order-closed", status=ExceptionCase.Status.CLOSED)
        cases, _response = self.ids_for()
        self.assertEqual(
            [case.pk for case in cases],
            [overdue.pk, today.pk, future.pk, no_follow_up.pk, closed.pk],
        )

    def test_filters_combine_with_responsible_organization_action_and_preserve_quick_view(self):
        match = self.case(
            "filter-match", organization="BEMSA", action_type="CONTACTAR",
            assigned_to=self.operator,
        )
        self.case("filter-other-org", organization="SURA", action_type="CONTACTAR", assigned_to=self.operator)
        response = self.client.get(reverse("email_exceptions:list"), {
            "quick": "mine", "organization": "BEMSA", "action_type": "CONTACTAR",
            "assigned_to": str(self.operator.pk),
        })
        self.assertEqual(response.context["page_obj"].object_list[0].pk, match.pk)
        self.assertContains(response, "quick=mine")
        self.assertContains(response, "organization=BEMSA")
        self.assertContains(response, "action_type=CONTACTAR")
        self.assertContains(response, str(self.operator.pk))

    def test_list_has_one_case_row_and_paginates_at_fifty(self):
        for index in range(201):
            self.case(f"inbox-page-{index}")
        response = self.client.get(reverse("email_exceptions:list"), {"quick": "all"})
        self.assertEqual(response.context["page_obj"].paginator.count, 201)
        self.assertEqual(len(response.context["cases"]), 50)
        self.assertContains(response, "page=2")
        self.assertContains(response, "aria-label=\"Vistas rápidas de casos\"")

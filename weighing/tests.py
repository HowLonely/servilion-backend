from datetime import timedelta

from django.test import Client as HttpClient, TestCase

from authentication.models import User
from authentication.services import issue_tokens
from companies.models import Client, Company
from orders.models import ServiceType
from weighing import services
from weighing.models import WeighingSettings, WeighIn


class ExpressQuotaTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(username='bascula', password='test', role=User.Role.PESAJE)
        self.client_obj = Client.objects.create(name='Peñón', reference_prefix='P')
        self.company = Company.objects.create(client=self.client_obj, name='Contratista X')
        settings_row = WeighingSettings.load()
        settings_row.express_monthly_limit = 2
        settings_row.save()

    def weigh(self, service_type=ServiceType.EXPRESS) -> WeighIn:
        return services.create_weigh_in(
            client_id=self.client_obj.id,
            company_id=self.company.id,
            garment_count=3,
            weight_kg=5,
            weighed_by=self.operator,
            service_type=service_type,
        )

    def test_express_consumes_quota_until_limit(self):
        self.weigh()
        self.weigh()
        self.assertEqual(services.express_quota()['used'], 2)
        self.assertEqual(services.express_quota()['remaining'], 0)

        with self.assertRaises(services.WeighInError):
            self.weigh()
        # Un cargo normal no depende del cupo.
        self.weigh(ServiceType.NORMAL)
        self.assertEqual(services.express_quota()['used'], 2)

    def test_rejected_express_does_not_burn_reference(self):
        self.weigh()
        self.weigh()
        count_before = WeighIn.objects.count()
        with self.assertRaises(services.WeighInError):
            self.weigh()
        self.assertEqual(WeighIn.objects.count(), count_before)

    def test_voiding_express_returns_the_slot(self):
        first = self.weigh()
        self.weigh()
        services.void_weigh_in(first.id, self.operator, 'mal pesado')
        self.assertEqual(services.express_quota()['used'], 1)
        self.weigh()

    def test_previous_month_does_not_count(self):
        old = self.weigh()
        WeighIn.objects.filter(pk=old.pk).update(
            weighed_at=services.current_month_start() - timedelta(minutes=1)
        )
        self.assertEqual(services.express_quota()['used'], 0)

    def test_quota_endpoint_and_admin_only_settings(self):
        self.weigh()
        http = HttpClient()
        operator_auth = {'HTTP_AUTHORIZATION': f"Bearer {issue_tokens(self.operator)['access']}"}

        response = http.get('/api/weighing/express-quota', **operator_auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['used'], 1)
        self.assertEqual(response.json()['limit'], 2)

        response = http.put(
            '/api/weighing/settings',
            data={'express_monthly_limit': 50},
            content_type='application/json',
            **operator_auth,
        )
        self.assertEqual(response.status_code, 403)

        admin = User.objects.create_user(username='admin-x', password='test', role=User.Role.ADMIN)
        admin_auth = {'HTTP_AUTHORIZATION': f"Bearer {issue_tokens(admin)['access']}"}
        response = http.put(
            '/api/weighing/settings',
            data={'express_monthly_limit': 50},
            content_type='application/json',
            **admin_auth,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WeighingSettings.load().express_monthly_limit, 50)

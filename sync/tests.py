from datetime import timedelta
from unittest import mock

from django.test import Client as HttpClient, TestCase, override_settings
from django.utils import timezone

from authentication.models import User
from authentication.services import issue_tokens
from companies.models import Client, Company
from orders.models import LaundryOrder, OrderItem, OrderStatus
from sync import cloud, feed, triggers, window
from sync.apply import SOURCE_CLOUD, SOURCE_EDGE, apply_changes
from sync.models import ChangeLog, SyncIssue
from sync.registry import SYNCED_MODELS, get_spec
from sync.serialization import serialize
from workers.models import Worker

EDGE_ID = 1_000_000_000_000


class SyncTestCase(TestCase):
    def setUp(self):
        self.client_row = Client.objects.create(name='Minera Test', reference_prefix='M')
        self.company = Company.objects.create(client=self.client_row, name='Contratista Test')
        self.worker = Worker.objects.create(company=self.company, badge_code='W1', full_name='Juan Pérez')
        self.user = User.objects.create_user(username='operador', password='x', role=User.Role.SUPERVISOR)
        ChangeLog.objects.all().delete()

    def make_order(self, **overrides) -> LaundryOrder:
        values = {
            'worker': self.worker,
            'company': self.company,
            'received_at': timezone.now(),
            'reference': 'M1000A',
            'status': OrderStatus.RECEIVED,
        }
        values.update(overrides)
        return LaundryOrder.objects.create(**values)

    def change(self, instance, op='U') -> dict:
        spec = get_spec(instance._meta.db_table)
        return {'table': spec.table, 'id': instance.pk, 'op': op, 'data': serialize(spec, instance)}


class TriggerTests(SyncTestCase):
    def test_every_write_path_is_logged(self):
        worker = Worker.objects.create(company=self.company, badge_code='W2', full_name='Ana')
        Worker.objects.filter(pk=worker.pk).update(full_name='Ana María')
        order = self.make_order()
        OrderItem.objects.bulk_create([OrderItem(order=order, custom_name='Bolso', quantity=1)])

        logged = set(ChangeLog.objects.values_list('table', flat=True))
        self.assertEqual(logged, {'workers_worker', 'orders_laundryorder', 'orders_orderitem'})

    def test_sync_writes_are_not_logged_back(self):
        with triggers.applying():
            Worker.objects.create(company=self.company, badge_code='W3', full_name='Eco')
        self.assertFalse(ChangeLog.objects.exists())
        # Y fuera del bloque se vuelve a anotar.
        Worker.objects.create(company=self.company, badge_code='W4', full_name='Normal')
        self.assertEqual(ChangeLog.objects.count(), 1)

    def test_registry_matches_triggers(self):
        from importlib import import_module

        migration = import_module('sync.migrations.0002_changelog_triggers')
        self.assertEqual({spec.table for spec in SYNCED_MODELS}, set(migration.TABLES))


class FeedTests(SyncTestCase):
    def test_collect_merges_repeated_changes_and_reports_deletes(self):
        order = self.make_order()
        order.observations = 'uno'
        order.save()
        order.observations = 'dos'
        order.save()
        worker = Worker.objects.create(company=self.company, badge_code='W9', full_name='Temporal')
        worker_id = worker.pk
        worker.delete()

        changes, last_id = feed.collect(0, 100)
        by_key = {(c['table'], c['id']): c for c in changes}

        self.assertEqual(last_id, ChangeLog.objects.order_by('-id').first().id)
        self.assertEqual(by_key[('orders_laundryorder', order.pk)]['data']['observations'], 'dos')
        self.assertEqual(by_key[('workers_worker', worker_id)]['op'], 'D')
        self.assertEqual(len([c for c in changes if c['table'] == 'orders_laundryorder']), 1)

    def test_service_accounts_never_leave_the_node(self):
        User.objects.create(username='nodo-x', is_service_account=True)
        changes, _ = feed.collect(0, 100)
        self.assertFalse([c for c in changes if c['table'] == 'authentication_user'])


class ApplyTests(SyncTestCase):
    def test_lww_keeps_the_newest_edit_and_preserves_its_timestamp(self):
        change = self.change(self.worker)
        edited_at = timezone.now() + timedelta(minutes=5)
        change['data'].update(full_name='Editado en planta', updated_at=edited_at.isoformat())

        apply_changes([change], source=SOURCE_EDGE)
        self.worker.refresh_from_db()
        self.assertEqual(self.worker.full_name, 'Editado en planta')
        self.assertEqual(self.worker.updated_at, edited_at)
        self.assertFalse(ChangeLog.objects.exists())

        stale = self.change(self.worker)
        stale['data'].update(full_name='Edición vieja', updated_at=(edited_at - timedelta(hours=1)).isoformat())
        apply_changes([stale], source=SOURCE_CLOUD)
        self.worker.refresh_from_db()
        self.assertEqual(self.worker.full_name, 'Editado en planta')
        self.assertTrue(SyncIssue.objects.filter(kind=SyncIssue.Kind.DISCARDED).exists())

    def test_edge_push_does_not_undo_a_delivery_registered_in_the_cloud(self):
        order = self.make_order(status=OrderStatus.DISPATCHED, dispatched_at=timezone.now())
        from_edge = self.change(order)
        from_edge['data']['observations'] = 'faltó una toalla'

        # Mientras tanto la app móvil entregó el morral en la nube.
        delivered_at = timezone.now()
        LaundryOrder.objects.filter(pk=order.pk).update(
            status=OrderStatus.DELIVERED, delivered_at=delivered_at, delivered_by=self.user
        )

        apply_changes([from_edge], source=SOURCE_EDGE)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.DELIVERED)
        self.assertEqual(order.delivered_at, delivered_at)
        self.assertEqual(order.observations, 'faltó una toalla')

    def test_edge_keeps_its_plant_changes_when_the_cloud_delivers(self):
        order = self.make_order(status=OrderStatus.DISPATCHED, dispatched_at=timezone.now(), observations='local')
        from_cloud = self.change(order)
        from_cloud['data'].update(
            status=OrderStatus.DELIVERED,
            delivered_at=timezone.now().isoformat(),
            observations='versión vieja de la nube',
        )

        apply_changes([from_cloud], source=SOURCE_CLOUD)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.DELIVERED)
        self.assertIsNotNone(order.delivered_at)
        self.assertEqual(order.observations, 'local')

    def test_same_username_created_on_both_sides_converges(self):
        cloud_user = User.objects.create_user(username='juan', password='x')
        edge_user = User(id=EDGE_ID + 7, username='juan', role=User.Role.PESAJE, password='!')
        change = {
            'table': 'authentication_user', 'id': edge_user.id, 'op': 'U',
            'data': serialize(get_spec('authentication_user'), edge_user),
        }

        apply_changes([change], source=SOURCE_EDGE)

        cloud_user.refresh_from_db()
        self.assertEqual(cloud_user.username, 'juan')
        self.assertEqual(User.objects.get(pk=EDGE_ID + 7).username, 'juan~0007')
        self.assertTrue(SyncIssue.objects.filter(kind=SyncIssue.Kind.RENAMED).exists())

    def test_existing_loser_is_renamed_the_same_way(self):
        # El otro lado: el servidor local ya tenía a su "juan" y recibe el de la nube.
        User.objects.create(id=EDGE_ID + 7, username='juan', password='!')
        cloud_user = User(id=42, username='juan', password='!')
        change = {
            'table': 'authentication_user', 'id': 42, 'op': 'U',
            'data': serialize(get_spec('authentication_user'), cloud_user),
        }
        apply_changes([change], source=SOURCE_CLOUD)
        self.assertEqual(User.objects.get(pk=42).username, 'juan')
        self.assertEqual(User.objects.get(pk=EDGE_ID + 7).username, 'juan~0007')

    def test_children_are_applied_after_their_parent(self):
        order = LaundryOrder(
            id=EDGE_ID + 1, worker=self.worker, company=self.company, received_at=timezone.now(), reference='M1001A'
        )
        item = OrderItem(id=EDGE_ID + 2, order_id=order.id, custom_name='Bolso', quantity=2)
        changes = [
            {'table': 'orders_orderitem', 'id': item.id, 'op': 'U', 'data': serialize(get_spec('orders_orderitem'), item)},
            {'table': 'orders_laundryorder', 'id': order.id, 'op': 'U',
             'data': serialize(get_spec('orders_laundryorder'), order)},
        ]
        result = apply_changes(changes, source=SOURCE_EDGE)
        self.assertEqual(result.applied, 2)
        self.assertEqual(LaundryOrder.objects.get(pk=EDGE_ID + 1).items.get().quantity, 2)

    def test_a_broken_row_does_not_block_the_batch(self):
        broken = {'table': 'orders_orderitem', 'id': EDGE_ID + 5, 'op': 'U',
                  'data': {'order_id': 999999, 'custom_name': 'X', 'quantity': 1}}
        good = self.change(self.worker)
        good['data']['full_name'] = 'Sigue'
        result = apply_changes([broken, good], source=SOURCE_EDGE)
        self.assertEqual(result.applied, 1)
        self.assertEqual(len(result.issues), 1)
        self.worker.refresh_from_db()
        self.assertEqual(self.worker.full_name, 'Sigue')


@override_settings(SERVILION_NODE='edge', SYNC_LOCAL_RETENTION_DAYS=90)
class WindowTests(SyncTestCase):
    def test_edge_ignores_old_closed_orders_from_the_cloud(self):
        old = LaundryOrder(
            id=77, worker=self.worker, company=self.company, reference='M1500A',
            received_at=timezone.now() - timedelta(days=200), status=OrderStatus.DELIVERED,
        )
        spec = get_spec('orders_laundryorder')
        result = apply_changes([{'table': spec.table, 'id': 77, 'op': 'U', 'data': serialize(spec, old)}],
                               source=SOURCE_CLOUD)
        self.assertEqual(result.skipped, 1)
        self.assertFalse(LaundryOrder.objects.filter(pk=77).exists())

    def test_prune_removes_only_finished_old_orders(self):
        long_ago = timezone.now() - timedelta(days=120)
        finished = self.make_order(received_at=long_ago, status=OrderStatus.DELIVERED, reference='M1100A')
        still_open = self.make_order(received_at=long_ago, status=OrderStatus.INCOMPLETE, reference='M1101A')
        recent = self.make_order(status=OrderStatus.DELIVERED, reference='M1102A')
        ChangeLog.objects.all().delete()

        window.prune_local()

        remaining = set(LaundryOrder.objects.values_list('id', flat=True))
        self.assertEqual(remaining, {still_open.id, recent.id})
        self.assertNotIn(finished.id, remaining)
        # El recorte local no viaja: en la nube la guía sigue existiendo.
        self.assertFalse(ChangeLog.objects.exists())

    def test_prune_waits_for_the_outbox(self):
        self.make_order(received_at=timezone.now() - timedelta(days=120), status=OrderStatus.DELIVERED)
        self.assertIn('skipped', window.prune_local())
        self.assertEqual(LaundryOrder.objects.count(), 1)


class NodeApiTests(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.node, self.token = cloud.register_node('planta-test')
        self.http = HttpClient()
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}
        ChangeLog.objects.all().delete()

    def test_push_applies_plant_operation(self):
        order = LaundryOrder(
            id=EDGE_ID + 10, worker=self.worker, company=self.company, received_at=timezone.now(),
            reference='M1200A',
        )
        spec = get_spec('orders_laundryorder')
        body = {'changes': [{'table': spec.table, 'id': order.id, 'op': 'U', 'data': serialize(spec, order)}],
                'pending': 3}
        response = self.http.post('/api/sync/push', body, content_type='application/json', **self.auth)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['applied'], 1)
        self.assertTrue(LaundryOrder.objects.filter(pk=EDGE_ID + 10).exists())
        # Lo recibido no vuelve a salir por el feed de la nube.
        self.assertFalse(ChangeLog.objects.exists())
        self.node.refresh_from_db()
        self.assertEqual(self.node.reported_pending, 3)

    def test_pull_returns_cloud_changes_and_acks_the_cursor(self):
        self.worker.full_name = 'Editado en la web'
        self.worker.save()
        response = self.http.get('/api/sync/pull', {'after': 0}, **self.auth)
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual([c['data']['full_name'] for c in body['changes']], ['Editado en la web'])

        # Al pedir desde el último id, lo anterior ya se puede borrar.
        self.http.get('/api/sync/pull', {'after': body['last_id']}, **self.auth)
        self.assertFalse(ChangeLog.objects.exists())

    def test_person_tokens_cannot_use_the_node_endpoints(self):
        token = issue_tokens(self.user)['access']
        response = self.http.get('/api/sync/pull', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(response.status_code, 401)

    def test_node_token_reads_the_regular_api(self):
        response = self.http.get('/api/workers/', **self.auth)
        self.assertEqual(response.status_code, 200)

    def test_snapshot_pages_a_table(self):
        response = self.http.get('/api/sync/snapshot', {'table': 'workers_worker', 'limit': 10}, **self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['id'] for row in response.json()['rows']], [self.worker.id])

    def test_status_for_the_web(self):
        token = issue_tokens(self.user)['access']
        response = self.http.get('/api/sync/status', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['node'], 'cloud')
        self.assertEqual(response.json()['nodes'][0]['name'], 'planta-test')


class PlantOnlyTests(SyncTestCase):
    @override_settings(ALLOW_PLANT_OPERATIONS=False)
    def test_cloud_refuses_weighing_and_digitizing(self):
        token = issue_tokens(self.user)['access']
        http = HttpClient()
        weighing = http.post('/api/weighing/', {
            'client_id': self.client_row.id, 'company_id': self.company.id, 'garment_count': 3, 'weight_kg': 4.5,
        }, content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(weighing.status_code, 409)
        packing = http.post('/api/orders/scan/packing', {'code': 'M1000A'},
                            content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(packing.status_code, 409)

    @override_settings(ALLOW_PLANT_OPERATIONS=True)
    def test_plant_accepts_them(self):
        token = issue_tokens(self.user)['access']
        response = HttpClient().post('/api/weighing/', {
            'client_id': self.client_row.id, 'company_id': self.company.id, 'garment_count': 3, 'weight_kg': 4.5,
        }, content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(response.status_code, 201, response.content)


@override_settings(SERVILION_NODE='edge', SYNC_CLOUD_URL='https://nube.test', SYNC_NODE_TOKEN='srvnode_x')
class HistoryProxyTests(SyncTestCase):
    def setUp(self):
        super().setUp()
        from sync import state

        state.put(state.ONLINE, True)
        self.http = HttpClient()
        self.auth = {'HTTP_AUTHORIZATION': f"Bearer {issue_tokens(self.user)['access']}"}

    def test_old_range_is_served_by_the_cloud(self):
        with mock.patch('sync.client.request', return_value=(200, b'{"items": [], "count": 0}', {})) as request:
            response = self.http.get('/api/orders/', {'date_from': '2020-01-01'}, **self.auth)
        self.assertEqual(response['X-Servilion-Source'], 'cloud')
        self.assertIn('/api/orders/?date_from=2020-01-01', request.call_args.args[1])

    def test_recent_range_stays_local(self):
        recent = (timezone.now() - timedelta(days=10)).date().isoformat()
        with mock.patch('sync.client.request') as request:
            response = self.http.get('/api/orders/', {'date_from': recent}, **self.auth)
        request.assert_not_called()
        self.assertEqual(response.status_code, 200)

    def test_without_internet_answers_locally(self):
        from sync.client import CloudUnavailable

        with mock.patch('sync.client.request', side_effect=CloudUnavailable('sin red')):
            response = self.http.get('/api/orders/', **self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Servilion-Source'], 'local')

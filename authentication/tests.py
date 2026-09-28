from django.test import Client as HttpClient, TestCase

from authentication.models import StaffRole, User
from authentication.permissions import ALL_PERMISSIONS, Perm
from authentication.services import issue_tokens


class UsersAndRolesTests(TestCase):
    def setUp(self):
        self.http = HttpClient()
        self.admin = User.objects.create_user(username='admin1', password='x', role=User.Role.ADMIN)
        self.supervisor = User.objects.create_user(username='super1', password='x', role=User.Role.SUPERVISOR)

    def call(self, user, method, path, body=None):
        headers = {'HTTP_AUTHORIZATION': f"Bearer {issue_tokens(user)['access']}"}
        send = getattr(self.http, method)
        if body is None:
            return send(path, **headers)
        return send(path, body, content_type='application/json', **headers)

    def test_me_carries_role_name_and_permissions(self):
        body = self.call(self.supervisor, 'get', '/api/auth/me').json()
        self.assertEqual(body['role_name'], 'Supervisor')
        self.assertIn(Perm.DIGITIZE, body['permissions'])
        self.assertNotIn(Perm.USERS, body['permissions'])
        admin = self.call(self.admin, 'get', '/api/auth/me').json()
        self.assertEqual(set(admin['permissions']), set(ALL_PERMISSIONS))

    def test_admin_creates_a_role_and_assigns_it(self):
        response = self.call(self.admin, 'post', '/api/roles/', {
            'name': 'Bodega Lencería', 'permissions': [Perm.LINEN_DISPATCH, Perm.LINEN_VIEW],
        })
        self.assertEqual(response.status_code, 201, response.content)
        role = response.json()
        self.assertEqual(role['code'], 'BODEGA_LENCERIA')

        response = self.call(self.admin, 'post', '/api/users/', {
            'username': 'bodega1', 'first_name': 'Rosa', 'role': role['code'], 'password': 'Lavanderia.2026',
        })
        self.assertEqual(response.status_code, 201, response.content)
        user = User.objects.get(username='bodega1')
        self.assertTrue(user.check_password('Lavanderia.2026'))

        me = self.call(user, 'get', '/api/auth/me').json()
        self.assertEqual(sorted(me['permissions']), sorted([Perm.LINEN_DISPATCH, Perm.LINEN_VIEW]))
        # El permiso de la API sigue al rol: sin pesaje no pesa.
        self.assertEqual(self.call(user, 'post', '/api/weighing/1/void', {'reason': 'x'}).status_code, 403)

    def test_editing_a_role_changes_what_its_users_can_do(self):
        role = StaffRole.objects.get(code=User.Role.SUPERVISOR)
        self.assertEqual(self.call(self.supervisor, 'get', '/api/reports/quality/incidents').status_code, 200)
        self.call(self.admin, 'put', f'/api/roles/{role.id}', {
            'name': role.name, 'permissions': [Perm.WEIGHING],
        })
        self.assertEqual(self.call(self.supervisor, 'get', '/api/reports/quality/incidents').status_code, 403)

    def test_admin_role_is_not_editable(self):
        role = StaffRole.objects.get(code=User.Role.ADMIN)
        response = self.call(self.admin, 'put', f'/api/roles/{role.id}', {'name': 'X', 'permissions': []})
        self.assertEqual(response.status_code, 400)

    def test_user_admins_cannot_escalate(self):
        manager_role = StaffRole.objects.create(code='RRHH', name='RRHH', permissions=[Perm.USERS])
        manager = User.objects.create_user(username='rrhh', password='x', role=manager_role.code)

        make_admin = self.call(manager, 'post', '/api/users/', {
            'username': 'intruso', 'role': User.Role.ADMIN, 'password': 'Lavanderia.2026',
        })
        self.assertEqual(make_admin.status_code, 403)
        grant_more = self.call(manager, 'put', f'/api/roles/{manager_role.id}', {
            'name': 'RRHH', 'permissions': [Perm.USERS, Perm.CATALOG],
        })
        self.assertEqual(grant_more.status_code, 403)

    def test_last_admin_cannot_be_demoted(self):
        response = self.call(self.admin, 'put', f'/api/users/{self.admin.id}', {
            'username': 'admin1', 'role': User.Role.SUPERVISOR, 'is_active': True,
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('administrador activo', response.json()['detail'])

    def test_weak_passwords_are_rejected(self):
        response = self.call(self.admin, 'post', '/api/users/', {
            'username': 'debil', 'role': User.Role.PESAJE, 'password': '123',
        })
        self.assertEqual(response.status_code, 400)

    def test_roles_in_use_cannot_be_deleted(self):
        role = StaffRole.objects.create(code='TEMP', name='Temporal', permissions=[])
        User.objects.create_user(username='temp', password='x', role='TEMP')
        self.assertEqual(self.call(self.admin, 'delete', f'/api/roles/{role.id}').status_code, 400)

    def test_permission_catalog(self):
        body = self.call(self.supervisor, 'get', '/api/roles/permissions').json()
        self.assertEqual({p['code'] for p in body}, set(ALL_PERMISSIONS))

    def test_service_accounts_cannot_log_in(self):
        user = User.objects.create_user(username='nodo', password='Lavanderia.2026', is_service_account=True)
        response = self.http.post('/api/auth/login', {'username': user.username, 'password': 'Lavanderia.2026'},
                                  content_type='application/json')
        self.assertEqual(response.status_code, 401)

import json
import logging
import os
import stat
import sys
import tempfile
import threading
from datetime import date, datetime, timedelta, timezone as dt_tz
from http.server import BaseHTTPRequestHandler, HTTPServer

from django.conf import settings as dj_settings
from django.test import TestCase, override_settings

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from users.models import User

from .backends import BackendError, EmsReport, PatchBackend
from .backends.powershell import LocalConfigForm, LocalPowerShellBackend
from .backends.rest import RestBackend, dig, parse_status_map
from .custom_fields import CF_CLASS, CF_INTERVAL, CF_LAST, CF_LOCK, ensure_custom_fields
from .jobs import poll_status
from .logic import apply_ems_report, due_date, new_log, plannable_objects, record_patch
from .models import EmsState, PatchLog, PatchSettings, UpdateClass

TODAY = date(2026, 11, 22)
LOG = logging.getLogger('test')


def grant(user, model, actions, constraints=None):
    """NetBox evaluates permissions via ObjectPermission only (Django user_permissions are ignored)."""
    from django.contrib.contenttypes.models import ContentType
    from users.models import ObjectPermission
    perm = ObjectPermission.objects.create(name=f'test {model.__name__} {actions}', actions=actions,
                                           constraints=constraints)
    perm.object_types.add(ContentType.objects.get_for_model(model))
    perm.users.add(user)


class FakeBackend(PatchBackend):
    """Proves the core works with any backend implementing the interface."""
    key = 'fake'
    label = 'Fake'
    states = {}

    def can_start(self, obj):
        return True, ''

    def start(self, obj, job_ref):
        return f'job-{obj.pk}'

    def status(self, external_id):
        if external_id not in self.states:
            raise BackendError('unreachable')
        return self.states[external_id]


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        ensure_custom_fields()
        ensure_custom_fields()  # idempotent
        cls.user = User.objects.create(username='tester')
        cls.cls = UpdateClass.objects.create(name='Server monthly', interval_days=30)
        site = Site.objects.create(name='S', slug='s')
        dt = DeviceType.objects.create(manufacturer=Manufacturer.objects.create(name='M', slug='m'),
                                       model='T', slug='t')
        role = DeviceRole.objects.create(name='R', slug='r')

        def dev(name, **cf):
            return Device.objects.create(name=name, site=site, device_type=dt, role=role,
                                         custom_field_data={CF_CLASS: cls.cls.pk, **cf})
        cls.never = dev('never')
        cls.overdue = dev('overdue', **{CF_LAST: (TODAY - timedelta(days=40)).isoformat()})
        cls.fresh = dev('fresh', **{CF_LAST: (TODAY - timedelta(days=5)).isoformat()})
        cls.override = dev('override', **{CF_LAST: (TODAY - timedelta(days=10)).isoformat(), CF_INTERVAL: 7})
        cls.locked = dev('locked', **{CF_LOCK: True})


class ManualTests(Base):
    def test_due_date(self):
        self.assertIsNone(due_date(None, 30))
        self.assertEqual(due_date(TODAY, 30), TODAY + timedelta(days=30))

    def test_planning_excludes_locked_and_sorts(self):
        names = [str(r['obj']) for r in plannable_objects(self.cls, TODAY)]
        self.assertEqual(names, ['never', 'overdue', 'override'])  # fresh not due, locked hidden
        all_names = [str(r['obj']) for r in plannable_objects(self.cls, TODAY, only_due=False)]
        self.assertIn('fresh', all_names)
        self.assertNotIn('locked', all_names)

    def test_due_exactly_on_reference_date(self):
        names = [str(r['obj']) for r in plannable_objects(self.cls, TODAY - timedelta(days=3))]
        self.assertIn('override', names)  # last 10 days ago, interval 7 -> due exactly on this reference date

    def test_record_patch(self):
        logged, locked, managed = record_patch([self.overdue, self.locked], TODAY, 'OS updates', self.user)
        self.assertEqual((logged, locked, managed), ([self.overdue], [self.locked], []))
        self.overdue.refresh_from_db()
        self.assertEqual(self.overdue.custom_field_data[CF_LAST], TODAY.isoformat())
        log = PatchLog.objects.get(object_id=self.overdue.pk)
        self.assertEqual((log.status, log.update_class, log.user_name), ('succeeded', self.cls, 'tester'))

    def test_backfill_does_not_move_date_back(self):
        record_patch([self.fresh], TODAY - timedelta(days=60), 'backfill', self.user)
        self.fresh.refresh_from_db()
        self.assertEqual(self.fresh.custom_field_data[CF_LAST], (TODAY - timedelta(days=5)).isoformat())
        self.assertEqual(PatchLog.objects.filter(object_id=self.fresh.pk).count(), 1)

    def test_views_render(self):
        admin = User.objects.create(username='admin_t', is_superuser=True)
        self.client.force_login(admin)
        for url in ('/plugins/patch-management/planning/?update_class=%d&day=%s' % (self.cls.pk, TODAY),
                    '/plugins/patch-management/update-classes/', f'/plugins/patch-management/update-classes/{self.cls.pk}/',
                    '/plugins/patch-management/log/', f'/plugins/patch-management/record/?device={self.never.pk}',
                    '/plugins/patch-management/settings/', '/plugins/patch-management/settings/?backend=rest',
                    '/plugins/patch-management/settings/?backend=pwsh_ssh',
                    self.never.get_absolute_url(), '/api/plugins/patch-management/update-classes/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.get('/plugins/patch-management/planning/?update_class=%d' % self.cls.pk)  # no date -> today
        self.assertContains(r, '>never<')
        self.assertContains(r, f'/plugins/dependencies/impact/?scenario=maintenance&amp;device={self.never.pk}')
        self.assertNotContains(r, '>locked<')

    def test_record_view_post(self):
        admin = User.objects.create(username='admin_p', is_superuser=True)
        self.client.force_login(admin)
        r = self.client.post('/plugins/patch-management/record/', {
            'device': [self.never.pk, self.locked.pk], 'day': TODAY.isoformat(), 'comment': 'done'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(PatchLog.objects.filter(object_id__in=[self.never.pk, self.locked.pk]).count(), 1)

    def test_settings_save(self):
        admin = User.objects.create(username='admin_s', is_superuser=True)
        self.client.force_login(admin)
        r = self.client.post('/plugins/patch-management/settings/', {
            'backend': 'rest', 'compliance_makes_due': 'on', 'cfg-base_url': 'https://ems.example.test',
            'cfg-timeout': 10, 'cfg-start_method': 'POST', 'cfg-status_map': 'Done=succeeded'})
        self.assertEqual(r.status_code, 302)
        s = PatchSettings.load()
        self.assertEqual((s.backend, s.backend_config['base_url'], s.backend_config['timeout']),
                         ('rest', 'https://ems.example.test', 10))
        r = self.client.post('/plugins/patch-management/settings/', {
            'backend': 'rest', 'cfg-base_url': 'https://x.test', 'cfg-timeout': 10, 'cfg-start_method': 'POST',
            'cfg-status_map': 'Done=great'})
        self.assertEqual(r.status_code, 200)  # invalid mapping is rejected


class TranslationTests(TestCase):
    def test_compiled_catalog_is_current(self):
        """django.mo is shipped (no gettext needed on the target) - it must contain every translated msgid."""
        import gettext
        import re
        base = os.path.join(os.path.dirname(__file__), 'locale', 'de', 'LC_MESSAGES')
        po = open(os.path.join(base, 'django.po'), encoding='utf-8').read()
        with open(os.path.join(base, 'django.mo'), 'rb') as f:
            catalog = gettext.GNUTranslations(f)._catalog
        missing = [m for m in re.findall(r'^msgid "(.+)"\nmsgstr "(?!")', po, re.M)
                   if m.replace('\\"', '"') not in catalog]
        self.assertEqual(missing, [], 'django.mo is outdated - run msgfmt (see CLAUDE.md)')
        self.assertEqual(re.findall(r'^#, .*fuzzy.*\n(?:#\|.*\n)*msgid "(.*)"', po, re.M), [], 'fuzzy (guessed) translations - fix them')


class GuardTests(Base):
    def test_last_patched_only_via_plugin(self):
        from utilities.exceptions import AbortRequest
        from .logic import mark_patched
        dev = Device.objects.get(pk=self.fresh.pk)
        dev.custom_field_data[CF_LAST] = '2030-01-01'  # e.g. REST API, bulk edit, CSV import
        with self.assertRaises(AbortRequest):
            dev.save()
        dev.refresh_from_db()
        self.assertTrue(mark_patched(dev, TODAY))
        dev.description = 'other change'  # unrelated edits stay possible
        dev.save()

    def test_settings_superuser_only(self):
        user = User.objects.create(username='settings_admin')
        grant(user, PatchSettings, ['view', 'change'])
        self.assertTrue(user.has_perm('netbox_patch_management.change_patchsettings'))  # has the permission ...
        self.client.force_login(user)
        self.assertEqual(self.client.get('/plugins/patch-management/settings/').status_code, 403)  # ... but no superuser


class EmsTests(Base):
    def setUp(self):
        self.backend = FakeBackend()

    def test_status_poll_uses_ems_finish_time(self):
        ok = new_log(self.never, TODAY, self.user, source='fake', status='started', external_id='a')
        bad = new_log(self.overdue, TODAY, self.user, source='fake', status='started', external_id='b')
        odd = new_log(self.override, TODAY, self.user, source='fake', status='started', external_id='c')
        down = new_log(self.fresh, TODAY, self.user, source='fake', status='started', external_id='d')
        finished = datetime(2026, 11, 20, 23, 30, tzinfo=dt_tz.utc)
        FakeBackend.states = {'a': ('succeeded', 'Finished', finished), 'b': ('failed', 'Error', None),
                              'c': ('weird', 'X', None)}
        poll_status(self.backend, LOG)
        for log in (ok, bad, odd, down):
            log.refresh_from_db()
        self.assertEqual([ok.status, bad.status, odd.status, down.status],
                         ['succeeded', 'failed', 'started', 'started'])
        self.never.refresh_from_db()
        self.assertEqual(self.never.custom_field_data[CF_LAST], ok.date.isoformat())
        self.assertIn(ok.date, (date(2026, 11, 20), date(2026, 11, 21)))  # EMS time in local zone, not poll date

    def test_ems_date_is_authoritative_and_logged_once(self):
        older = TODAY - timedelta(days=20)  # older than the manual value (5 days ago)
        self.assertTrue(apply_ems_report(self.fresh, EmsReport(older, True, ''), self.backend))
        self.fresh.refresh_from_db()
        self.assertEqual(self.fresh.custom_field_data[CF_LAST], older.isoformat())
        self.assertFalse(apply_ems_report(self.fresh, EmsReport(older, True, ''), self.backend))
        self.assertEqual(PatchLog.objects.filter(object_id=self.fresh.pk, source='fake').count(), 1)

    def test_non_compliant_is_due_and_manual_record_refused(self):
        last = date.fromisoformat(self.fresh.custom_field_data[CF_LAST])
        apply_ems_report(self.fresh, EmsReport(last, False, 'NonCompliant'), self.backend)
        rows = plannable_objects(self.cls, TODAY, backend=self.backend)
        self.assertEqual(str(rows[0]['obj']), 'fresh')  # not due by interval, but non-compliant -> first
        self.assertTrue(rows[0]['non_compliant'])
        logged, locked, managed = record_patch([self.fresh, self.never], TODAY, 'x', self.user, backend=self.backend)
        self.assertEqual((logged, managed), ([self.never], [self.fresh]))
        self.assertEqual(EmsState.objects.count(), 1)


class _Handler(BaseHTTPRequestHandler):
    routes = {}
    seen = []

    def log_message(self, *args):
        pass

    def _reply(self):
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0)).decode()
        type(self).seen.append((self.command, self.path, body, self.headers.get('Authorization')))
        code, data = self.routes.get((self.command, self.path.split('?')[0]), (404, {}))
        payload = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = _reply


class RestBackendTests(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server = HTTPServer(('127.0.0.1', 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        super().tearDownClass()

    def backend(self):
        return RestBackend({
            'base_url': self.base, 'auth_header': 'Authorization', 'auth_prefix': 'Bearer ', 'token_env': 'PATCH_BACKEND_TEST_TOKEN',
            'timeout': 5, 'start_method': 'POST', 'start_url': '/jobs/{{ job_ref }}/run',
            'start_body': '{"host": {{ obj.name }}}', 'start_id_path': 'result.id',
            'status_url': '/runs/{{ external_id }}', 'status_path': 'state', 'status_finished_path': 'end',
            'status_map': 'Done=succeeded\nBroken=failed',
            'state_url': '/hosts/{{ obj.name }}', 'state_date_path': 'patched', 'state_compliant_path': 'ok',
            'state_compliant_value': 'true', 'state_detail_path': 'info',
        })

    def test_helpers(self):
        self.assertEqual(dig({'a': [{'b': 1}]}, 'a.0.b'), 1)
        self.assertIsNone(dig({'a': []}, 'a.3'))
        with self.assertRaises(Exception):
            parse_status_map('x=nope')

    def test_start_status_state(self):
        _Handler.routes = {
            ('POST', '/jobs/monthly/run'): (201, {'result': {'id': 'r1'}}),
            ('GET', '/runs/r1'): (200, {'state': 'Done', 'end': '2026-11-20T10:00:00Z'}),
            ('GET', '/hosts/never'): (200, {'patched': '2026-11-19', 'ok': False, 'info': '2 missing'}),
        }
        _Handler.seen = []
        b = self.backend()
        with self.assertRaises(BackendError):  # token variable not set
            b.start(self.never, 'monthly')
        os.environ['PATCH_BACKEND_TEST_TOKEN'] = 'secret'
        try:
            self.assertEqual(b.start(self.never, 'monthly'), 'r1')
            self.assertEqual(_Handler.seen[-1][2], '{"host": "never"}')
            self.assertEqual(_Handler.seen[-1][3], 'Bearer secret')
            status, raw, finished = b.status('r1')
            self.assertEqual((status, raw, finished.date()), ('succeeded', 'Done', date(2026, 11, 20)))
            reports = b.patch_state([self.never, self.fresh])  # fresh -> 404 -> not reported
            self.assertEqual(list(reports), [self.never])
            self.assertEqual((reports[self.never].last_patched, reports[self.never].compliant),
                             (date(2026, 11, 19), False))
        finally:
            del os.environ['PATCH_BACKEND_TEST_TOKEN']

    def test_object_data_cannot_change_url_or_body(self):
        _Handler.routes, _Handler.seen = {('POST', '/jobs/j/run'): (201, {'result': {'id': 'r'}})}, []
        evil = Device.objects.get(pk=self.never.pk)
        evil.name = 'x", "admin": true, "y": "../../admin?'
        b = self.backend()
        b.config['token_env'] = ''
        b.config['start_url'] = '/jobs/j/run?h={{ obj.name }}'
        self.assertEqual(b.start(evil, 'j'), 'r')
        method, path, body, _auth = _Handler.seen[-1]
        self.assertEqual(json.loads(body), {'host': evil.name})          # JSON-encoded, no injected keys
        self.assertNotIn('../', path.split('?h=')[1])                     # URL-encoded
        b.config['start_url'] = '{{ cf.x }}'
        evil.custom_field_data['x'] = 'https://attacker.example/steal'
        with self.assertRaises(BackendError):                              # never leaves the base origin
            b.start(evil, 'j')
        b.config['start_url'] = 'https://attacker.example/steal'
        with self.assertRaises(BackendError):
            b.start(evil, 'j')

    def test_secret_env_name_and_https(self):
        from .backends.rest import RestConfigForm
        form = RestConfigForm({'base_url': 'https://ems.test', 'timeout': 5, 'start_method': 'POST',
                               'token_env': 'SECRET_KEY'})
        self.assertFalse(form.is_valid())
        self.assertIn('token_env', form.errors)
        b = RestBackend({'base_url': 'http://ems.example.test', 'token_env': 'PATCH_BACKEND_X', 'start_url': '/x',
                         'timeout': 1})
        os.environ['PATCH_BACKEND_X'] = 'secret'
        try:
            with self.assertRaisesMessage(BackendError, 'HTTPS'):
                b.start(self.never, 'j')
        finally:
            del os.environ['PATCH_BACKEND_X']


FAKE_PWSH = '''#!{python}
import json, sys
action = sys.argv[sys.argv.index('-Action') + 1]
req = json.load(sys.stdin)
if action == 'fail':
    sys.stderr.write('boom'); sys.exit(1)
out = {{'test': lambda: {{'info': 'fake'}}, 'jobs': lambda: {{'jobs': [{{'id': 'j1', 'label': 'Job 1'}}]}},
       'start': lambda: {{'id': 'x-' + str(req['objects'][0]['id'])}},
       'status': lambda: {{'status': 'succeeded', 'raw': 'ok', 'finished_at': '2026-11-21T08:00:00+00:00'}},
       'state': lambda: {{'states': {{o['key']: {{'last_patched': '2026-11-18', 'compliant': True}} for o in req['objects']}}}}}}
print(json.dumps(out[action]()))
'''


class PowerShellBackendTests(Base):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.pwsh = os.path.join(self.root, 'fake-pwsh')
        with open(self.pwsh, 'w') as f:
            f.write(FAKE_PWSH.format(python=sys.executable))
        os.chmod(self.pwsh, os.stat(self.pwsh).st_mode | stat.S_IEXEC)
        self.script = os.path.join(self.root, 'backend.ps1')
        open(self.script, 'w').close()
        cfg = dict(dj_settings.PLUGINS_CONFIG)
        cfg['netbox_patch_management'] = {**cfg.get('netbox_patch_management', {}), 'script_root': self.root,
                                          'pwsh_path': self.pwsh}
        self.override = override_settings(PLUGINS_CONFIG=cfg)
        self.override.enable()

    def tearDown(self):
        self.override.disable()

    def test_contract(self):
        b = LocalPowerShellBackend({'script': self.script, 'pwsh': '/bin/sh', 'timeout': 10})  # UI value is ignored
        self.assertEqual(b.test(), 'fake')
        self.assertEqual(b.job_choices(), [('j1', 'Job 1')])
        self.assertEqual(b.start(self.never, 'j1'), f'x-{self.never.pk}')
        self.assertEqual(b.status('x')[0], 'succeeded')
        self.assertEqual(b.patch_state([self.never])[self.never].last_patched, date(2026, 11, 18))
        with self.assertRaises(BackendError):
            b.call('fail')

    def test_script_outside_root_rejected(self):
        form = LocalConfigForm({'script': '/etc/passwd', 'timeout': 10})
        self.assertFalse(form.is_valid())
        with self.assertRaises(BackendError):
            LocalPowerShellBackend({'script': '/etc/passwd'}).test()
        link = os.path.join(self.root, 'link.ps1')
        os.symlink('/etc/passwd', link)  # symlink out of the root is resolved and rejected
        self.assertFalse(LocalConfigForm({'script': link, 'timeout': 10}).is_valid())

    def test_ssh_settings_reject_injection(self):
        from .backends.powershell import SshConfigForm, SshPowerShellBackend
        base = {'host': 'jump01', 'port': 22, 'user': 'svc', 'key_file': '/k', 'known_hosts_file': '/kh',
                'remote_script': 'C:\\scripts\\patch.ps1', 'pwsh': 'pwsh', 'timeout': 10}
        self.assertTrue(SshConfigForm(base).is_valid())
        for field, value in (('host', '-oProxyCommand'), ('user', '-oX'), ('remote_script', 'x.ps1 -Command Stop-Computer'),
                             ('key_file', 'relative')):
            with self.subTest(field=field):
                self.assertFalse(SshConfigForm({**base, field: value}).is_valid())
                with self.assertRaises(BackendError):
                    SshPowerShellBackend({**base, field: value}).command('test')

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
                    '/plugins/patch-management/log/', f'/plugins/patch-management/record/?devices={self.never.pk}',
                    '/plugins/patch-management/settings/', '/plugins/patch-management/settings/?backend=rest',
                    '/plugins/patch-management/settings/?backend=pwsh_ssh',
                    self.never.get_absolute_url(), '/api/plugins/patch-management/update-classes/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.get('/plugins/patch-management/planning/?update_class=%d' % self.cls.pk)  # no date -> today
        self.assertContains(r, '>never<')
        self.assertNotContains(r, '>locked<')

    def test_record_view_post(self):
        admin = User.objects.create(username='admin_p', is_superuser=True)
        self.client.force_login(admin)
        r = self.client.post('/plugins/patch-management/record/', {
            'devices': [self.never.pk, self.locked.pk], 'day': TODAY.isoformat(), 'comment': 'done'})
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
        code, data = self.routes.get((self.command, self.path), (404, {}))
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
            'base_url': self.base, 'auth_header': 'Authorization', 'auth_prefix': 'Bearer ', 'token_env': 'PM_TEST_TOKEN',
            'timeout': 5, 'start_method': 'POST', 'start_url': '/jobs/{{ job_ref }}/run',
            'start_body': '{"host": "{{ obj.name }}"}', 'start_id_path': 'result.id',
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
        os.environ['PM_TEST_TOKEN'] = 'secret'
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
            del os.environ['PM_TEST_TOKEN']


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
        cfg['netbox_patch_management'] = {**cfg.get('netbox_patch_management', {}), 'script_root': self.root}
        self.override = override_settings(PLUGINS_CONFIG=cfg)
        self.override.enable()

    def tearDown(self):
        self.override.disable()

    def test_contract(self):
        b = LocalPowerShellBackend({'script': self.script, 'pwsh': self.pwsh, 'timeout': 10})
        self.assertEqual(b.test(), 'fake')
        self.assertEqual(b.job_choices(), [('j1', 'Job 1')])
        self.assertEqual(b.start(self.never, 'j1'), f'x-{self.never.pk}')
        self.assertEqual(b.status('x')[0], 'succeeded')
        self.assertEqual(b.patch_state([self.never])[self.never].last_patched, date(2026, 11, 18))
        with self.assertRaises(BackendError):
            b.call('fail')

    def test_script_outside_root_rejected(self):
        form = LocalConfigForm({'script': '/etc/passwd', 'pwsh': 'pwsh', 'timeout': 10})
        self.assertFalse(form.is_valid())
        with self.assertRaises(BackendError):
            LocalPowerShellBackend({'script': '/etc/passwd', 'pwsh': self.pwsh}).test()

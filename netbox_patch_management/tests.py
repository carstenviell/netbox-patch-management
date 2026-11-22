import logging
from datetime import date, timedelta

from django.test import TestCase, override_settings

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from users.models import User

from .backends import BackendError, PatchBackend
from .custom_fields import CF_CLASS, CF_INTERVAL, CF_LAST, CF_LOCK, ensure_custom_fields
from .jobs import poll_status
from .logic import due_date, plannable_objects, record_patch
from .models import PatchLog, PatchStatusChoices, UpdateClass

TODAY = date(2026, 11, 22)


class FakeBackend(PatchBackend):
    """Proves the core works with any backend implementing the interface."""
    name = 'fake'
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


class PatchTests(TestCase):
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
        logged, refused = record_patch([self.overdue, self.locked], TODAY, 'OS updates', self.user)
        self.assertEqual((logged, refused), ([self.overdue], [self.locked]))
        self.overdue.refresh_from_db()
        self.assertEqual(self.overdue.custom_field_data[CF_LAST], TODAY.isoformat())
        log = PatchLog.objects.get(object_id=self.overdue.pk)
        self.assertEqual((log.status, log.update_class, log.user_name), ('succeeded', self.cls, 'tester'))

    def test_backfill_does_not_move_date_back(self):
        record_patch([self.fresh], TODAY - timedelta(days=60), 'backfill', self.user)
        self.fresh.refresh_from_db()
        self.assertEqual(self.fresh.custom_field_data[CF_LAST], (TODAY - timedelta(days=5)).isoformat())
        self.assertEqual(PatchLog.objects.filter(object_id=self.fresh.pk).count(), 1)

    def test_status_poll_with_fake_backend(self):
        from .logic import new_log
        ok = new_log(self.never, TODAY, self.user, source='fake', status='started', external_id='a')
        bad = new_log(self.overdue, TODAY, self.user, source='fake', status='started', external_id='b')
        odd = new_log(self.override, TODAY, self.user, source='fake', status='started', external_id='c')
        down = new_log(self.fresh, TODAY, self.user, source='fake', status='started', external_id='d')
        FakeBackend.states = {'a': ('succeeded', 'Finished'), 'b': ('failed', 'Error'), 'c': ('weird', 'X')}
        poll_status(FakeBackend(), logging.getLogger('test'))
        for log in (ok, bad, odd, down):
            log.refresh_from_db()
        self.assertEqual([ok.status, bad.status, odd.status, down.status],
                         ['succeeded', 'failed', 'started', 'started'])
        self.never.refresh_from_db()
        self.assertEqual(self.never.custom_field_data[CF_LAST], date.today().isoformat())

    def test_views_render(self):
        admin = User.objects.create(username='admin_t', is_superuser=True)
        self.client.force_login(admin)
        for url in ('/plugins/patch-management/planning/?update_class=%d&day=%s' % (self.cls.pk, TODAY),
                    '/plugins/patch-management/update-classes/', f'/plugins/patch-management/update-classes/{self.cls.pk}/',
                    '/plugins/patch-management/log/', f'/plugins/patch-management/record/?devices={self.never.pk}',
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

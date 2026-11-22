# NetBox Patch Management

NetBox plugin for **OS patch planning** of devices and virtual machines.

- **Update classes** with a patch interval (e.g. "Servers monthly" = 30 days), optionally overridden per object
- **Patch lock** per object: locked systems never appear in planning, impact analysis or job starts
- **Planning view**: pick a class and see every due system, most overdue first
- **Impact analysis**: one click opens the *ImpactAnalyse* script from
  [netbox-abhaengigkeiten](https://github.com/carstenviell/netbox-abhaengigkeiten) in the
  maintenance scenario, with the selected systems preselected
- **Patch log**: every recorded patch (date, user, comment, source, status) - filterable and exportable;
  `last_patched` is only ever set through the log, and changes appear in the NetBox changelog
- **Pluggable backends**: start patch jobs in an endpoint-management system and track their status
  (first backend: baramundi via [netbox-baramundi](https://github.com/carstenviell/netbox-baramundi), in progress)

Requires NetBox ≥ 4.7.

## Installation

```bash
pip install netbox-patch-management
```

`configuration.py`:

```python
PLUGINS = ["netbox_patch_management"]
PLUGINS_CONFIG = {
    "netbox_patch_management": {
        "backend": None,            # dotted path to a PatchBackend subclass, None = manual only
        "status_interval_min": 10,  # how often running backend jobs are polled
    },
}
```

Then run `manage.py migrate`. The plugin creates these custom fields on devices and VMs
(group "Patch-Management"). Existing fields with the same name are never modified:

| Field | Type | Purpose |
|---|---|---|
| `patch_class` | object (update class) | Assignment to an update class |
| `patch_interval_days` | integer | Overrides the class interval |
| `patch_lock` | boolean | Excludes the system from patching |
| `last_patched` | date (read-only in the UI) | Set by the patch log |

Labels are created in the instance's `LANGUAGE_CODE`. Rename them in *Customization → Custom Fields* if needed.

## Usage

1. *Patch Management → Update classes*: create classes with interval and maintenance window.
2. Assign devices/VMs to a class (edit form, bulk edit or CSV import of `cf_patch_class`).
3. *Patch Management → Planning*: choose a class. Select systems and
   - **Impact of selection**: opens the impact analysis (maintenance scenario)
   - **Record as patched**: date + mandatory comment, writes the patch log
   - **Start via &lt;backend&gt;**: only with a configured backend and the permission `start_patch_job`
4. Device/VM detail pages show a *Patch Management* panel with due date, lock, latest log entries and actions.

### Interface to netbox-abhaengigkeiten

The impact link targets the script named `ImpactAnalyse` and its fields `szenario`, `devices`, `vms`.
If the script is not installed, the impact buttons are hidden.

## Backends

A backend implements `netbox_patch_management.backends.PatchBackend`:

```python
class PatchBackend:
    name = "mybackend"     # stored as PatchLog.source
    label = "My EMS"       # shown in the UI
    def job_choices(self): ...            # [(job_ref, label)] for the update class form
    def can_start(self, obj): ...         # (bool, reason)
    def start(self, obj, job_ref): ...    # -> external id
    def status(self, external_id): ...    # -> (PatchStatusChoices value, raw status)
```

Starting runs as a background job. The system job *Patch job status* polls running jobs.
On success it sets `last_patched`, while failed or cancelled jobs are only logged.

## License

Apache-2.0

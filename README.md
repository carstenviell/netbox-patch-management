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
        "status_interval_min": 10,                 # sync interval for job status and patch state
        "script_root": "/opt/netbox/patch-scripts", # local PowerShell scripts must live below this directory
        "extra_backends": [],                      # additional PatchBackend classes (dotted paths)
        "secret_env_prefix": "PATCH_BACKEND_",     # tokens may only be read from env vars with this prefix
        "pwsh_path": "pwsh",                       # local PowerShell executable (not editable in the UI)
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
| `last_patched` | date (read-only) | Set by the patch log / the EMS. Changes via REST API, bulk edit or CSV import are rejected (HTTP 400); new objects may bring a past date (initial import) |

Labels are created in the instance's `LANGUAGE_CODE`. Rename them in *Customization → Custom Fields* if needed.

## Usage

1. *Patch Management → Update classes*: create classes with interval, maintenance window and (with an EMS) the job to run.
2. Assign devices/VMs to a class (edit form, bulk edit or CSV import of `cf_patch_class`).
3. *Patch Management → Planning*: choose a class. Select systems and
   - **Impact of selection**: opens the impact analysis (maintenance scenario)
   - **Record as patched**: date + mandatory comment, writes the patch log (systems without EMS data only)
   - **Start via &lt;EMS&gt;**: only with a configured backend and the permission `start_patch_job`
4. Device/VM detail pages show a *Patch Management* panel with due date, lock, EMS status, latest log entries and actions.

### Interface to netbox-abhaengigkeiten

The impact link targets the script named `ImpactAnalyse` and its fields `szenario`, `devices`, `vms`.
If the script is not installed, the impact buttons are hidden.

## Endpoint-management systems (EMS)

Choose the EMS under *Patch Management → Settings* (**superusers only** – the settings define the URLs, hosts and
scripts NetBox calls). **Secrets are never stored in the database** – settings only name environment variables
(which must start with `PATCH_BACKEND_`) or files. *Test connection* checks the configuration.

Before a job starts, its reference is checked against the backend's job list and the confirmation page shows the
job name, so a job reference set via API cannot silently start an unexpected job.

**The EMS is authoritative.** The sync job (every `status_interval_min` minutes)

- polls started jobs; on success the patch date is the EMS finish time (not the time of the poll),
- reads the patch state of all systems with an update class: a reported last-patch date replaces
  `last_patched` (also if it is older) and is written to the patch log with the EMS as source – so patches
  run directly in the EMS show up too,
- stores compliance; with *Non-compliant systems are due* enabled (default) such systems are due regardless of
  their interval and listed first.

Systems with a patch date from the EMS cannot be recorded manually. Systems the EMS does not know
(switches, firewalls, ...) stay manual.

| Backend | Use |
|---|---|
| baramundi | Requires [netbox-baramundi](https://github.com/carstenviell/netbox-baramundi) (connection settings and endpoint mapping live there). Requires **baramundi bConnect API v2**. |
| REST API | Generic HTTP/JSON EMS: URL and body templates (Jinja2, sandboxed; variables `obj`, `cf`, `job_ref`, `external_id`), values via dotted JSON paths (`data.0.id`), status mapping `raw=succeeded|failed|cancelled|started`. Token from an environment variable, sent via HTTPS only. |
| PowerShell | Local `pwsh` (`pwsh_path`) in the NetBox worker container (install PowerShell in your image), script below `script_root`. |
| PowerShell (SSH) | Script on a remote host via OpenSSH, absolute key file and pinned `known_hosts` file (unknown hosts are refused). Remote paths must not contain spaces. |

**REST templates are injection-safe by construction:** every `{{ ... }}` value is URL-encoded in URL templates
and JSON-encoded in the body template. Write `{"host": {{ obj.name }}}` (no quotes around the expression).
URL templates are paths below the base URL – requests never leave the configured origin.

### PowerShell contract

```
pwsh -NoProfile -NonInteractive -File <script> -Action <test|jobs|start|status|state>
```

Input arrives as JSON on **stdin** (`action`, `objects` [`key`, `type`, `id`, `name`, `custom_fields`],
`job_ref`, `external_id`). Object data is never passed on the command line. The script prints JSON:

| Action | Output |
|---|---|
| `test` | `{"info": "..."}` |
| `jobs` | `{"jobs": [{"id": "...", "label": "..."}]}` |
| `start` | `{"id": "<external id>"}` |
| `status` | `{"status": "succeeded\|failed\|cancelled\|started", "raw": "...", "finished_at": "<ISO 8601>"}` |
| `state` | `{"states": {"<key>": {"last_patched": "<YYYY-MM-DD>", "compliant": true, "detail": "..."}}}` |

A non-zero exit code is an error (stderr is shown). See [`examples/patch-backend.ps1`](examples/patch-backend.ps1).

### Own backends

Subclass `netbox_patch_management.backends.PatchBackend` (`key`, `label`, `config_form`, `test`, `job_choices`,
`can_start`, `start`, `status`, `patch_state`) and add it to `extra_backends`.

## License

Apache-2.0

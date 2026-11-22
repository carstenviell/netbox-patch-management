**English** | [Deutsch](README.de.md)

# NetBox Patch Management

[NetBox](https://github.com/netbox-community/netbox) plugin for **OS patch planning** of devices and virtual machines.

## Features

- **Update classes** with a patch interval (e.g. "Servers monthly" = 30 days), optionally overridden per object
- **Patch lock** per object: locked systems never appear in planning, impact analysis or job starts
- **Planning view**: pick a class and see every due system, most overdue first
- **Impact analysis**: one click opens the impact analysis of
  [netbox-dependencies](https://github.com/carstenviell/netbox-dependencies) in the
  maintenance scenario, with the selected systems preselected
- **Patch log**: every recorded patch (date, user, comment, source, status) - filterable and exportable;
  `last_patched` is only ever set through the log, and changes appear in the NetBox changelog
- **Pluggable backends**: start patch jobs in an endpoint-management system and track their status
  (built in: REST API, PowerShell local/SSH; baramundi via the plugin netbox-baramundi – not published yet)

## Requirements

- NetBox **4.7** or later (Python ≥ 3.10, as shipped with NetBox)
- A running NetBox **background worker** (`netbox-rq` / `netbox-worker`): starting patch jobs and the
  periodic sync run as background jobs
- Optional: the plugin [netbox-dependencies](https://github.com/carstenviell/netbox-dependencies) ≥ 2.0
  for the impact links
- Optional: an endpoint-management system (see [below](#endpoint-management-systems-ems))

The plugin is not published on PyPI – install it from the GitHub release archive (no `git` needed).
Replace `v0.3.0` with the [release](https://github.com/carstenviell/netbox-patch-management/releases) you want.

## Installation

### NetBox installed from source (systemd)

```bash
sudo /opt/netbox/venv/bin/pip install \
  https://github.com/carstenviell/netbox-patch-management/archive/refs/tags/v0.3.0.tar.gz

# keep the plugin when NetBox is upgraded (upgrade.sh reinstalls local_requirements.txt)
echo "https://github.com/carstenviell/netbox-patch-management/archive/refs/tags/v0.3.0.tar.gz" | \
  sudo tee -a /opt/netbox/local_requirements.txt
```

Enable the plugin in `/opt/netbox/netbox/netbox/configuration.py`:

```python
PLUGINS = [
    # ...
    "netbox_patch_management",
]

# optional - these are the defaults
PLUGINS_CONFIG = {
    "netbox_patch_management": {
        "status_interval_min": 10,                  # sync interval for job status and patch state
        "script_root": "/opt/netbox/patch-scripts", # local PowerShell scripts must live below this directory
        "extra_backends": [],                       # additional PatchBackend classes (dotted paths)
        "secret_env_prefix": "PATCH_BACKEND_",      # tokens may only be read from env vars with this prefix
        "pwsh_path": "pwsh",                        # local PowerShell executable (not editable in the UI)
    },
}
```

Apply the migrations and restart **both** services (the worker registers the sync job at start):

```bash
cd /opt/netbox/netbox
sudo /opt/netbox/venv/bin/python manage.py migrate
sudo systemctl restart netbox netbox-rq
```

### netbox-docker

`plugin_requirements.txt`:

```text
https://github.com/carstenviell/netbox-patch-management/archive/refs/tags/v0.3.0.tar.gz
```

`Dockerfile-Plugins`:

```dockerfile
FROM netboxcommunity/netbox:v4.7
COPY ./plugin_requirements.txt /opt/netbox/
RUN /usr/local/bin/uv pip install -r /opt/netbox/plugin_requirements.txt
```

Add `"netbox_patch_management"` to `PLUGINS` in `configuration/plugins.py`, let `docker-compose.override.yml`
build `netbox`, `netbox-worker` and `netbox-housekeeping` from `Dockerfile-Plugins`, then:

```bash
docker compose build --no-cache && docker compose up -d
```

Migrations run automatically when the container starts.

### After the installation

1. **Check:** the menu *Patch Management* appears; *Customization → Custom Fields* lists the four fields below
   (group "Patch-Management").
2. **Permissions** (*Admin → Permissions*) for non-superusers:

   | Task | Permission |
   |---|---|
   | View planning, update classes | `netbox_patch_management.view_updateclass` |
   | Maintain update classes | `add/change/delete_updateclass` |
   | Record patches | `netbox_patch_management.add_patchlog` + `change` on the devices/VMs |
   | View the patch log | `netbox_patch_management.view_patchlog` |
   | Start patch jobs in the EMS | `netbox_patch_management.start_patch_job` + `change` on the devices/VMs |
   | Settings (EMS) | superusers only |
3. **Secrets for an EMS** (e.g. a REST token) are read from environment variables starting with `PATCH_BACKEND_`.
   Set them for **both** the web service and the worker (systemd: `systemctl edit netbox netbox-rq`
   → `Environment=PATCH_BACKEND_TOKEN=...`; Docker: `env/netbox.env`, which both containers use).

### Upgrade

Install the new release archive (also update the line in `local_requirements.txt` / `plugin_requirements.txt`),
then run `migrate` and restart `netbox` and `netbox-rq` (Docker: rebuild).

### Custom fields

The plugin creates these custom fields on devices and VMs (group "Patch-Management").
Existing fields with the same name are never modified:

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

### Interface to netbox-dependencies

The impact link opens `/plugins/dependencies/impact/?scenario=maintenance&device=<id>&virtualmachine=<id>`.
If netbox-dependencies is not installed, the impact buttons are hidden.

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
| baramundi | Requires the plugin netbox-baramundi (not published yet; connection settings and endpoint mapping live there). Requires **baramundi bConnect API v2**. |
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

## Language

The user interface is English and ships with a German translation. It follows the language the user selected in
NetBox (*Profile → Preferences → Language*); languages without a translation fall back to English.

## License

[Apache License 2.0](LICENSE)

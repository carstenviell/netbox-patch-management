**English** | [Deutsch](CHANGELOG.de.md)

# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [SemVer](https://semver.org/).

## [Unreleased]
### Open
- Verify the baramundi backend against a real bMS (job start; whether `POST JobInstances` already runs without `/Start`).
- PowerShell local: `pwsh` is not installed in the test image, tested only with a fake executable. SSH untested without a test host.
- Publish netbox-baramundi (local only so far).

## [0.3.0] – 2026-11-22
### Changed
- **Impact analysis via the plugin netbox-dependencies ≥ 2.0** (successor of netbox-abhaengigkeiten): links open `/plugins/dependencies/impact/?scenario=maintenance&device=…&virtualmachine=…` instead of the custom script `ImpactAnalyse`. Without the plugin the impact buttons are hidden.
- **Breaking:** the URL parameters for recording/starting are now named like the models (`device`, `virtualmachine` instead of `devices`, `vms`) – shared with netbox-dependencies.
- Documentation in two languages: `README.md`/`README.de.md`, `CHANGELOG.md`/`CHANGELOG.de.md`.

## [0.2.2] – 2026-11-22
### Fixed
- Installation guide: the plugin is not on PyPI. The guide now describes the installation from the GitHub release archive (works without `git`, also in the netbox-docker image) for source installations (incl. `local_requirements.txt` for upgrades) and netbox-docker (`plugin_requirements.txt`), plus worker restart, permissions, secrets for web and worker, and upgrade.
- The German translation was missing after installation: the compiled `django.mo` is now shipped (before only the `.po`; without `gettext` on the target system the UI stayed English). A test makes sure the `.mo` matches the `.po`.
- Three messages of the EMS features were translated wrongly or appeared in English (automatically guessed translations marked "fuzzy").

## [0.2.1] – 2026-11-22
### Security
Result of the security audit of 2026-11-22 (report kept locally, not in the repository):
- Tokens are only read from environment variables with the prefix `PATCH_BACKEND_` (`secret_env_prefix`). Before, a settings admin could have sent any process secret to external hosts.
- REST: expressions in URL templates are URL-encoded, URLs stay below the base URL (same origin), the token is only sent via HTTPS (loopback excepted). Expressions in the body are JSON-encoded (**breaking:** `{"host": {{ obj.name }}}` instead of `"{{ obj.name }}"`).
- PowerShell: the local `pwsh` only via `PLUGINS_CONFIG['pwsh_path']`, no longer in the UI; SSH host/user must not start with `-`, remote paths without spaces (no argument injection), key/known_hosts paths absolute.
- `last_patched` can no longer be changed past the patch log via REST API, bulk edit or CSV import (HTTP 400). New objects may bring a past date.
- Job start checks the job reference against the backend's job list. The confirmation page shows the job name.
- Settings and connection test for superusers only. The class selection of the planning respects object permissions.

## [0.2.0] – 2026-11-22
### Added
- Settings page *Patch Management → Settings*: choice of the endpoint-management system (none/manual, baramundi, REST API, PowerShell local, PowerShell via SSH, own backends via `extra_backends`), backend-specific fields, "Test connection". Secrets are never stored in the DB (only names of environment variables or file paths).
- **The EMS is authoritative:** the sync job takes over the finish time of successful jobs and the last patch state reported by the EMS as `last_patched` (also backwards) and logs it with the EMS as source – including updates that ran directly in the EMS.
- Compliance from the EMS: column "EMS status" in planning and panel; non-compliant systems are due regardless of the interval (can be switched off) and listed first.
- Backends: REST (Jinja2 templates in the NetBox sandbox, JSON paths, status mapping), PowerShell local/SSH (fixed contract, JSON via stdin/stdout, script only below `script_root`, SSH with pinned host key), baramundi (via `netbox-baramundi`).
- Example script `examples/patch-backend.ps1`.
### Changed
- Manual recording is blocked for systems with a patch date from the EMS.
- Backend interface: `status()` also returns the finish time; new `test()`, `patch_state()`, `config_form`. The config key `backend` is gone (selection now in the UI).
- The system job is now called "Patch sync" (job status + patch state).

## [0.1.0] – 2026-11-22
### Added
- Update classes (name, interval, maintenance window, backend job) with list, detail, edit, delete, REST API.
- Custom fields on devices/VMs: `patch_class`, `patch_interval_days`, `patch_lock`, `last_patched` (created idempotently after every `migrate`, existing fields stay unchanged).
- Planning per class: due systems (never patched first), locked systems hidden, interval overridable per object, reference date selectable.
- Impact link: link to the script `ImpactAnalyse` (netbox-abhaengigkeiten), maintenance scenario, selection prefilled.
- Record a patch (date, mandatory comment): patch log + `last_patched` + changelog entry; locked systems are refused, an older date is logged as backfill without moving `last_patched` back.
- Patch log (append only, filterable, CSV export, REST API read only).
- Panel "Patch Management" on device/VM pages.
- Backend interface `PatchBackend` with a background job for starting and a system job for the status sync.
- German translation.

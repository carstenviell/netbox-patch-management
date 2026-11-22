# Changelog

Format angelehnt an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionen nach [SemVer](https://semver.org/lang/de/).

## [Unveröffentlicht]
### Offen
- baramundi-Backend am echten bMS verifizieren (Job-Start, ob `POST JobInstances` ohne `/Start` schon ausführt).
- PowerShell lokal: `pwsh` ist im Test-Image nicht installiert, getestet nur mit Fake-Executable. SSH ohne Testhost ungetestet.

## [0.2.1] – 2026-11-22
### Sicherheit
Ergebnis des Security-Audits vom 2026-11-22 (Bericht lokal, nicht im Repo):
- Tokens nur noch aus Umgebungsvariablen mit Präfix `PATCH_BACKEND_` (`secret_env_prefix`). Vorher hätte ein Einstellungs-Admin beliebige Prozess-Secrets an externe Hosts senden können.
- REST: Ausdrücke in URL-Vorlagen werden URL-kodiert, URLs bleiben unterhalb der Basis-URL (gleicher Origin), Token nur über HTTPS (Loopback ausgenommen). Ausdrücke im Body werden JSON-kodiert (**Breaking:** `{"host": {{ obj.name }}}` statt `"{{ obj.name }}"`).
- PowerShell: lokales `pwsh` nur noch über `PLUGINS_CONFIG['pwsh_path']`, nicht mehr in der Oberfläche; SSH-Host/-User dürfen nicht mit `-` beginnen, Remote-Pfade keine Leerzeichen (keine Argument-Injection), Key-/known_hosts-Pfade absolut.
- `last_patched` kann nicht mehr per REST-API, Massenbearbeitung oder CSV-Import am Patch-Protokoll vorbei geändert werden (HTTP 400). Neue Objekte dürfen ein vergangenes Datum mitbringen.
- Jobstart prüft die Job-Referenz gegen die Job-Liste des Backends. Die Bestätigungsseite zeigt den Job-Namen.
- Einstellungen und Verbindungstest nur für Superuser. Die Klassen-Auswahl der Planung respektiert Objektrechte.

## [0.2.0] – 2026-11-22
### Neu
- Einstellungsseite *Patch-Management → Einstellungen*: Auswahl des Endpoint-Management-Systems (keins/manuell, baramundi, REST-API, PowerShell lokal, PowerShell per SSH, eigene Backends über `extra_backends`), backend-spezifische Felder, „Verbindung testen“. Secrets werden nie in der DB gespeichert (nur Namen von Umgebungsvariablen bzw. Dateipfade).
- **EMS ist führend:** Sync-Job übernimmt den Endzeitpunkt erfolgreicher Jobs und den vom EMS gemeldeten letzten Patch-Stand als `last_patched` (auch rückwärts) und protokolliert ihn mit dem EMS als Quelle – auch Updates, die direkt im EMS liefen.
- Compliance aus dem EMS: Spalte „EMS-Status“ in Planung und Panel; nicht konforme Systeme sind (abschaltbar) unabhängig vom Intervall fällig und stehen oben.
- Backends: REST (Jinja2-Vorlagen in der NetBox-Sandbox, JSON-Pfade, Status-Mapping), PowerShell lokal/SSH (fester Vertrag, JSON über stdin/stdout, Script nur unterhalb von `script_root`, SSH mit gepinntem Host-Key), baramundi (über `netbox-baramundi`).
- Beispiel-Script `examples/patch-backend.ps1`.
### Geändert
- Manuelles Erfassen ist für Systeme mit Patch-Datum aus dem EMS gesperrt.
- Backend-Schnittstelle: `status()` liefert zusätzlich den Endzeitpunkt, neu `test()`, `patch_state()`, `config_form`. Der Config-Key `backend` entfällt (Auswahl jetzt in der Oberfläche).
- System-Job heißt jetzt „Patch sync“ (Job-Status + Patch-Stand).

## [0.1.0] – 2026-11-22
### Neu
- Update-Klassen (Name, Intervall, Wartungsfenster, Backend-Job) mit Liste, Detail, Bearbeiten, Löschen, REST-API.
- Custom Fields an Devices/VMs: `patch_class`, `patch_interval_days`, `patch_lock`, `last_patched` (idempotent nach jedem `migrate` angelegt, bestehende Felder bleiben unverändert).
- Planung je Klasse: fällige Systeme (nie gepatchte zuerst), gesperrte Systeme ausgeblendet, Intervall je Objekt überschreibbar, Stichtag wählbar.
- Impact-Verknüpfung: Link auf das Script `ImpactAnalyse` (netbox-abhaengigkeiten), Szenario Wartung, Auswahl vorbefüllt.
- Patch erfassen (Datum, Pflicht-Kommentar): Patch-Protokoll + `last_patched` + Changelog-Eintrag; gesperrte Systeme werden abgewiesen, älteres Datum wird als Nachtrag protokolliert, ohne `last_patched` zurückzusetzen.
- Patch-Protokoll (nur anhängen, filterbar, CSV-Export, REST-API nur lesend).
- Panel „Patch-Management“ auf Device-/VM-Seiten.
- Backend-Schnittstelle `PatchBackend` mit Hintergrund-Job für den Start und System-Job für den Status-Abgleich.
- Deutsche Übersetzung.

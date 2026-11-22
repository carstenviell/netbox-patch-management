# Changelog

Format angelehnt an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionen nach [SemVer](https://semver.org/lang/de/).

## [Unveröffentlicht]
### Offen
- baramundi-Backend am echten bMS verifizieren (Job-Start, ob `POST JobInstances` ohne `/Start` schon ausführt).
- PowerShell lokal: `pwsh` ist im Test-Image nicht installiert, getestet nur mit Fake-Executable. SSH ohne Testhost ungetestet.
- Security-Audit (`SECURITY-ANALYSE-<Datum>.md`).

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

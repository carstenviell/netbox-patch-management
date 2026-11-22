# Changelog

Format angelehnt an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionen nach [SemVer](https://semver.org/lang/de/).

## [Unveröffentlicht]
### Offen
- baramundi-Backend (`backends/baramundi.py`) – wartet auf `netbox-baramundi` und die bConnect-Specs für Jobs/Endpoints.
- Security-Audit (`SECURITY-ANALYSE-<Datum>.md`).

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

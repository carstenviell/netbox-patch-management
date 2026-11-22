[English](README.md) | **Deutsch**

# NetBox Patch Management

[NetBox](https://github.com/netbox-community/netbox)-Plugin für die **OS-Update-Planung** von Geräten und virtuellen
Maschinen.

## Funktionen

- **Update-Klassen** mit Patch-Intervall (z. B. „Server monatlich“ = 30 Tage), je Objekt überschreibbar
- **Update-Sperre** je Objekt: Gesperrte Systeme erscheinen nie in Planung, Impact-Analyse oder Jobstart
- **Planung:** Klasse wählen und alle fälligen Systeme sehen, die am längsten überfälligen zuerst
- **Impact-Analyse:** Ein Klick öffnet die Impact-Analyse von
  [netbox-dependencies](https://github.com/carstenviell/netbox-dependencies) im Szenario Wartung, mit den ausgewählten
  Systemen vorbelegt
- **Patch-Protokoll:** jeder erfasste Patch (Datum, Benutzer, Kommentar, Quelle, Status) – filterbar und exportierbar;
  `last_patched` wird ausschließlich über das Protokoll gesetzt, Änderungen erscheinen im NetBox-Changelog
- **Austauschbare Backends:** Patch-Jobs in einem Endpoint-Management-System (EMS) starten und den Status verfolgen
  (eingebaut: REST-API, PowerShell lokal/SSH; baramundi über das Plugin netbox-baramundi – noch nicht veröffentlicht)

## Voraussetzungen

- NetBox **4.7** oder neuer (Python ≥ 3.10, wie mit NetBox ausgeliefert)
- Ein laufender NetBox-**Hintergrund-Worker** (`netbox-rq` / `netbox-worker`): Jobstart und regelmäßiger Abgleich laufen
  als Hintergrund-Jobs
- Optional: das Plugin [netbox-dependencies](https://github.com/carstenviell/netbox-dependencies) ≥ 2.0 für die
  Impact-Links
- Optional: ein Endpoint-Management-System (siehe [unten](#endpoint-management-systeme-ems))

Das Plugin liegt nicht auf PyPI – installiert wird aus dem GitHub-Release-Archiv (ohne `git`).
`v0.3.0` ggf. durch das gewünschte [Release](https://github.com/carstenviell/netbox-patch-management/releases) ersetzen.

## Installation

### NetBox aus dem Quellcode (systemd)

```bash
sudo /opt/netbox/venv/bin/pip install \
  https://github.com/carstenviell/netbox-patch-management/archive/refs/tags/v0.3.0.tar.gz

# bleibt bei NetBox-Upgrades erhalten (upgrade.sh installiert local_requirements.txt neu)
echo "https://github.com/carstenviell/netbox-patch-management/archive/refs/tags/v0.3.0.tar.gz" | \
  sudo tee -a /opt/netbox/local_requirements.txt
```

Plugin aktivieren in `/opt/netbox/netbox/netbox/configuration.py`:

```python
PLUGINS = [
    # ...
    "netbox_patch_management",
]

# optional – das sind die Standardwerte
PLUGINS_CONFIG = {
    "netbox_patch_management": {
        "status_interval_min": 10,                  # Abgleich-Intervall für Job-Status und Patch-Stand
        "script_root": "/opt/netbox/patch-scripts", # lokale PowerShell-Scripts müssen unterhalb liegen
        "extra_backends": [],                       # weitere PatchBackend-Klassen (Dotted Paths)
        "secret_env_prefix": "PATCH_BACKEND_",      # Tokens nur aus Umgebungsvariablen mit diesem Präfix
        "pwsh_path": "pwsh",                        # lokales PowerShell-Programm (nicht in der Oberfläche änderbar)
    },
}
```

Migrationen anwenden und **beide** Dienste neu starten (der Worker registriert den Abgleich-Job beim Start):

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

`"netbox_patch_management"` in `PLUGINS` in `configuration/plugins.py` eintragen, `netbox`, `netbox-worker` und
`netbox-housekeeping` per `docker-compose.override.yml` aus `Dockerfile-Plugins` bauen lassen, dann:

```bash
docker compose build --no-cache && docker compose up -d
```

Die Migrationen laufen beim Start des Containers automatisch.

### Nach der Installation

1. **Prüfen:** Das Menü *Patch Management* erscheint; unter *Customization → Custom Fields* stehen die vier Felder
   unten (Gruppe „Patch-Management“).
2. **Berechtigungen** (*Admin → Berechtigungen*) für Nicht-Superuser:

   | Aufgabe | Berechtigung |
   |---|---|
   | Planung und Update-Klassen ansehen | `netbox_patch_management.view_updateclass` |
   | Update-Klassen pflegen | `add/change/delete_updateclass` |
   | Patches erfassen | `netbox_patch_management.add_patchlog` + `change` auf den Geräten/VMs |
   | Patch-Protokoll ansehen | `netbox_patch_management.view_patchlog` |
   | Patch-Jobs im EMS starten | `netbox_patch_management.start_patch_job` + `change` auf den Geräten/VMs |
   | Einstellungen (EMS) | nur Superuser |
3. **Secrets für ein EMS** (z. B. ein REST-Token) werden aus Umgebungsvariablen mit Präfix `PATCH_BACKEND_` gelesen.
   Für **beide** Dienste setzen, Web und Worker (systemd: `systemctl edit netbox netbox-rq`
   → `Environment=PATCH_BACKEND_TOKEN=...`; Docker: `env/netbox.env`, die beide Container nutzen).

### Upgrade

Neues Release-Archiv installieren (auch die Zeile in `local_requirements.txt` bzw. `plugin_requirements.txt`
anpassen), danach `migrate` und `netbox` sowie `netbox-rq` neu starten (Docker: neu bauen).

### Custom Fields

Das Plugin legt diese Custom Fields an Geräten und VMs an (Gruppe „Patch-Management“). Bestehende Felder mit gleichem
Namen werden nie verändert:

| Feld | Typ | Zweck |
|---|---|---|
| `patch_class` | Objekt (Update-Klasse) | Zuordnung zu einer Update-Klasse |
| `patch_interval_days` | Ganzzahl | Überschreibt das Intervall der Klasse |
| `patch_lock` | Boolean | Nimmt das System vom Patchen aus |
| `last_patched` | Datum (nur lesbar) | Wird über das Patch-Protokoll bzw. das EMS gesetzt. Änderungen per REST-API, Massenbearbeitung oder CSV-Import werden abgewiesen (HTTP 400); neue Objekte dürfen ein vergangenes Datum mitbringen (Erstimport) |

Die Feldbezeichnungen werden in der `LANGUAGE_CODE` der Instanz angelegt und lassen sich unter
*Customization → Custom Fields* umbenennen.

## Benutzung

1. *Patch Management → Update-Klassen*: Klassen mit Intervall, Wartungsfenster und (mit EMS) dem auszuführenden Job anlegen.
2. Geräte/VMs einer Klasse zuordnen (Bearbeiten, Massenbearbeitung oder CSV-Import von `cf_patch_class`).
3. *Patch Management → Planung*: Klasse wählen. Systeme auswählen und
   - **Impact der Auswahl**: öffnet die Impact-Analyse (Szenario Wartung)
   - **Als gepatcht erfassen**: Datum + Pflicht-Kommentar, schreibt das Patch-Protokoll (nur Systeme ohne EMS-Daten)
   - **Über &lt;EMS&gt; starten**: nur mit eingerichtetem Backend und der Berechtigung `start_patch_job`
4. Detailseiten von Geräten/VMs zeigen einen Bereich *Patch-Management* mit Fälligkeit, Sperre, EMS-Status, letzten
   Protokolleinträgen und Aktionen.

### Schnittstelle zu netbox-dependencies

Der Impact-Link öffnet `/plugins/dependencies/impact/?scenario=maintenance&device=<id>&virtualmachine=<id>`.
Ist netbox-dependencies nicht installiert, werden die Impact-Buttons ausgeblendet.

## Endpoint-Management-Systeme (EMS)

Das EMS wird unter *Patch Management → Einstellungen* gewählt (**nur Superuser** – die Einstellungen legen fest, welche
URLs, Hosts und Scripts NetBox aufruft). **Secrets werden nie in der Datenbank gespeichert** – die Einstellungen nennen
nur Umgebungsvariablen (Präfix `PATCH_BACKEND_`) oder Dateien. *Verbindung testen* prüft die Konfiguration.

Vor dem Start eines Jobs wird seine Referenz mit der Job-Liste des Backends abgeglichen, und die Bestätigungsseite zeigt
den Job-Namen – eine per API gesetzte Job-Referenz kann also nicht unbemerkt einen unerwarteten Job starten.

**Das EMS ist führend.** Der Abgleich-Job (alle `status_interval_min` Minuten)

- fragt gestartete Jobs ab; bei Erfolg ist das Patch-Datum der Endzeitpunkt im EMS (nicht der Zeitpunkt der Abfrage),
- liest den Patch-Stand aller Systeme mit Update-Klasse: Ein gemeldetes Datum des letzten Patches ersetzt `last_patched`
  (auch wenn es älter ist) und wird mit dem EMS als Quelle protokolliert – so erscheinen auch Patches, die direkt im
  EMS liefen,
- speichert die Compliance; mit *Nicht konforme Systeme sind fällig* (Standard) sind solche Systeme unabhängig vom
  Intervall fällig und stehen oben.

Systeme mit Patch-Datum aus dem EMS lassen sich nicht manuell erfassen. Systeme, die das EMS nicht kennt (Switche,
Firewalls …), bleiben manuell.

| Backend | Einsatz |
|---|---|
| baramundi | Erfordert das Plugin netbox-baramundi (noch nicht veröffentlicht; dort liegen Verbindungseinstellungen und Endpoint-Zuordnung). Erfordert **baramundi bConnect API v2**. |
| REST-API | Generisches HTTP/JSON-EMS: URL- und Body-Vorlagen (Jinja2 in der Sandbox; Variablen `obj`, `cf`, `job_ref`, `external_id`), Werte über JSON-Pfade (`data.0.id`), Status-Zuordnung `rohwert=succeeded|failed|cancelled|started`. Token aus einer Umgebungsvariable, nur über HTTPS. |
| PowerShell | Lokales `pwsh` (`pwsh_path`) im NetBox-Worker-Container (PowerShell im Image installieren), Script unterhalb von `script_root`. |
| PowerShell (SSH) | Script auf einem entfernten Host über OpenSSH, absolute Key-Datei und gepinnte `known_hosts`-Datei (unbekannte Hosts werden abgelehnt). Entfernte Pfade ohne Leerzeichen. |

**REST-Vorlagen sind von Haus aus injection-sicher:** Jeder `{{ ... }}`-Wert wird in URL-Vorlagen URL-kodiert und in der
Body-Vorlage JSON-kodiert. Also `{"host": {{ obj.name }}}` schreiben (ohne Anführungszeichen um den Ausdruck).
URL-Vorlagen sind Pfade unterhalb der Basis-URL – Anfragen verlassen nie den konfigurierten Server.

### PowerShell-Vertrag

```
pwsh -NoProfile -NonInteractive -File <script> -Action <test|jobs|start|status|state>
```

Die Eingabe kommt als JSON über **stdin** (`action`, `objects` [`key`, `type`, `id`, `name`, `custom_fields`],
`job_ref`, `external_id`). Objektdaten werden nie über die Befehlszeile übergeben. Das Script gibt JSON aus:

| Aktion | Ausgabe |
|---|---|
| `test` | `{"info": "..."}` |
| `jobs` | `{"jobs": [{"id": "...", "label": "..."}]}` |
| `start` | `{"id": "<externe ID>"}` |
| `status` | `{"status": "succeeded\|failed\|cancelled\|started", "raw": "...", "finished_at": "<ISO 8601>"}` |
| `state` | `{"states": {"<key>": {"last_patched": "<JJJJ-MM-TT>", "compliant": true, "detail": "..."}}}` |

Ein Exit-Code ungleich 0 gilt als Fehler (stderr wird angezeigt). Siehe
[`examples/patch-backend.ps1`](examples/patch-backend.ps1).

### Eigene Backends

Von `netbox_patch_management.backends.PatchBackend` ableiten (`key`, `label`, `config_form`, `test`, `job_choices`,
`can_start`, `start`, `status`, `patch_state`) und in `extra_backends` eintragen.

## Sprache

Die Oberfläche ist englisch, eine deutsche Übersetzung wird mitgeliefert. Angezeigt wird die Sprache, die der Benutzer
in NetBox gewählt hat (*Profil → Einstellungen → Sprache*); für Sprachen ohne Übersetzung erscheint Englisch.

## Lizenz

[Apache License 2.0](LICENSE)

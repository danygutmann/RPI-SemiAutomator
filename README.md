# RPI-SemiAutomator

Ein dockerbasiertes Tool für den Raspberry Pi, das mehrere serielle
Schnittstellen (z. B. `/dev/ttyUSB0`, `/dev/ttyACM0`, ...) gleichzeitig
anzeigt. Über ein Web-UI (Python + [NiceGUI](https://nicegui.io/)) können
Daten live mitgelesen, eigene Befehle gesendet und vordefinierte
"Makro"-Kommandos per Knopfdruck ausgelöst werden.

## Funktionen

- Serielle Ports werden automatisch erkannt; auf der Startseite werden jedoch
  nur Schnittstellen angezeigt, die zuvor als **Favorit** gespeichert wurden
- Verbinden/Trennen einzelner Ports (die Baudrate wird in den Einstellungen am Favoriten festgelegt, nicht im Terminal)
- Live-Log der gesendeten (`TX`) und empfangenen (`RX`) Daten je Port, mit Button zum Leeren der Anzeige; das Terminal-Fenster ist in der Höhe per Ziehen am unteren Rand verstellbar
- Download des aktuellen Logs als lokale Textdatei
- Freitext-Eingabe zum Senden beliebiger Befehle
- Individuelle, selbst angelegte Makros (global oder je Schnittstelle) im Burger-Menü als mehrstufiger, ein-/ausklappbarer Baum; es gibt keine vordefinierten Standard-Makros mehr
- Eigene **Einstellungen**-Seite zum Hinzufügen/Entfernen von Favoriten sowie zum Sichern (Backup) und Neuladen der gespeicherten Einstellungen
- Läuft als Docker-Container, geeignet für den Raspberry Pi

## Konfiguration

Die als Voreinstellung in der Einstellungen-Seite wählbaren Ports werden in
[`config/config.yaml`](config/config.yaml) definiert:

```yaml
ports:
  - name: "USB0"
    device: "/dev/ttyUSB0"
    baudrate: 115200
```

Der Pfad zur Konfigurationsdatei kann über die Umgebungsvariable
`RPI_SEMIAUTOMATOR_CONFIG` überschrieben werden.

Auf der **Einstellungen**-Seite (erreichbar über den Button oben rechts) kann
eine Schnittstelle (aus den konfigurierten Ports oder aktuell erkannten
Geräten) mit eigenem Namen und Baudrate als **Favorit** gespeichert werden;
nur Favoriten werden auf der Startseite als Terminal-Tabs angezeigt.
Favoriten werden in `data/favorites.yaml` gespeichert (alternativ über
`RPI_SEMIAUTOMATOR_FAVORITES`) und können auf der Einstellungen-Seite auch
wieder entfernt werden.

Makros werden im **Burger-Menü** (links) als Baum angezeigt. Ein Klick auf
einen Eintrag sendet das Makro an die aktuell gewählte Schnittstelle. Mit der
**rechten Maustaste** (auf einen Ordner oder in den freien Bereich) wird ein
neuer Eintrag hinzugefügt. Die Ebenen des Baums entstehen über `/` im Namen,
z. B. `Gruppe/Untergruppe/Ping`. Makros werden in `data/macros.yaml`
gespeichert (alternativ über `RPI_SEMIAUTOMATOR_MACROS`) und gelten global
oder nur für die aktuelle Schnittstelle. Es gibt keine vordefinierten
Standard-Makros.

Favoriten (und damit Baudrate) werden ausschließlich in den Einstellungen
verwaltet; im Terminal gibt es weder Baudraten-Auswahl noch einen
Favoriten-Button. Die Tabs der Favoriten erscheinen nur oben zentriert.

Auf der Einstellungen-Seite steht außerdem ein **Backup herunterladen**-Button
zur Verfügung, der die aktuell gespeicherten Favoriten und Makros als eine
YAML-Datei exportiert, sowie ein **Einstellungen neu laden**-Button, der
Favoriten und Makros erneut von der Festplatte einliest (z. B. nachdem die
Dateien extern verändert oder aus einem Backup wiederhergestellt wurden).

Beim Download enthält die Logdatei die aktuell im Speicher verfügbaren
empfangenen Zeilen; der Button **Anzeige leeren** setzt die Anzeige und den
Zwischenspeicher eines Ports zurück.

## Start mit Docker

```bash
docker compose up --build
```

Die Weboberfläche ist danach unter `http://<raspberry-pi-ip>:8080`
erreichbar.

Die Anwendung zeigt serielle Gerätedateien wie `/dev/ttyUSB*`, `/dev/ttyACM*`
und `/dev/ttyAMA*` an. `lsusb` listet auch USB-Geräte auf, die keine serielle
Schnittstelle bereitstellen; diese können nicht als serielle Terminals genutzt
werden. Docker bindet `/dev` ein und begrenzt den Gerätezugriff über
`device_cgroup_rules` auf die genannten seriellen Gerätetypen. Die
Ordnerbindung `./data:/app/data` bewahrt gespeicherte Favoriten über
Container-Neustarts hinweg.

## Lokale Entwicklung (ohne Docker)

```bash
pip install -r requirements.txt
python app/main.py
```

## Tests

```bash
pip install -r requirements.txt pytest
pytest
```

## Dark mode, Fenstergröße, Makros

- Dunkelmodus per Button im Header (wird pro Browser gespeichert).
- Die Höhe der Konsole (Log) wird pro Schnittstelle im Browser gespeichert.
- Unter Einstellungen können Favoriten (Name/Baudrate) und Makros bearbeitet und gelöscht werden.

## API

Optional absicherbar mit `RPI_SEMIAUTOMATOR_API_TOKEN` (Bearer-Token im `Authorization`-Header oder `?token=`).

### Schnittstellen per Alias

Als **Alias** dient der Name eines Favoriten (Einstellungen). So lassen sich
mehrere Systeme generisch ansprechen, ohne die tatsächlichen Ports
(`/dev/ttyUSB0` …) zu kennen. Aliase werden exakt (Groß-/Kleinschreibung) und
URL-kodiert verwendet; unbekannte Aliase liefern `404`.

- `GET /api/interfaces` – alle Schnittstellen mit `alias`, `device`, `baudrate`, `connected`
- `POST /api/interfaces/{alias}/send` – `{"command": "...", "raw": false}`

### Makros einer Schnittstelle lesen/bearbeiten

- `GET /api/interfaces/{alias}/macros` – nutzbare Makros (`label`, `command`, `raw`, `scope`: `interface` oder `global`)
- `PUT /api/interfaces/{alias}/macros` – ersetzt **alle schnittstellenspezifischen** Makros durch die übergebene JSON-Liste `[{"label": "...", "command": "...", "raw": false}]` (globale Makros bleiben unverändert; Labels müssen eindeutig sein)
- `PUT /api/interfaces/{alias}/macros/{label}` – legt ein einzelnes Makro an oder ändert es: `{"command": "...", "raw": false}`; `/` im Label bildet die Ebenen des Baums
- `DELETE /api/interfaces/{alias}/macros/{label}` – löscht ein schnittstellenspezifisches Makro

Globale Makros sind über die API nur lesbar und werden im Burger-Menü bearbeitet.

### Backup

- `GET /api/backup` – liefert als YAML-Datei (`rpi-semiautomator-backup.yaml`) die Favoriten (Alias/Name, Port, Baudrate) und alle Makros (global und je Schnittstelle). Das ist derselbe Inhalt wie beim Backup-Button in den Einstellungen und genügt, um ein System zu migrieren oder wiederherzustellen.

Einschränkungen: Die statische Datei `config/config.yaml`, Logs und der
Verbindungsstatus sind nicht enthalten. Es gibt (noch) keinen Restore-Endpunkt;
zum Wiederherstellen werden die Einträge aus dem Backup in
`data/favorites.yaml` (`ports`) bzw. `data/macros.yaml` (`macros`) übernommen und
anschließend in den Einstellungen **Einstellungen neu laden** ausgelöst.

### Weitere Endpunkte

- `GET /api/ports` – Schnittstellen und Status
- `POST /api/send` – `{"device": "/dev/ttyUSB0", "command": "...", "raw": false}`
- `WS /api/ws?device=/dev/ttyUSB0` – streamt `{"direction": "rx"|"tx", "text": "..."}`; Text oder `{"command": "..."}` senden

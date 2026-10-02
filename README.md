# RPI-SemiAutomator

Ein dockerbasiertes Tool für den Raspberry Pi, das mehrere serielle
Schnittstellen (z. B. `/dev/ttyUSB0`, `/dev/ttyACM0`, ...) gleichzeitig
anzeigt. Über ein Web-UI (Python + [NiceGUI](https://nicegui.io/)) können
Daten live mitgelesen, eigene Befehle gesendet und vordefinierte
"Makro"-Kommandos per Knopfdruck ausgelöst werden.

## Funktionen

- Serielle Ports werden automatisch erkannt; auf der Startseite werden jedoch
  nur Schnittstellen angezeigt, die zuvor als **Favorit** gespeichert wurden
- Verbinden/Trennen einzelner Ports mit einstellbarer Baudrate
- Live-Log der gesendeten (`TX`) und empfangenen (`RX`) Daten je Port, mit Button zum Leeren der Anzeige; das Terminal-Fenster ist in der Höhe per Ziehen am unteren Rand verstellbar
- Download des aktuellen Logs als lokale Textdatei
- Freitext-Eingabe zum Senden beliebiger Befehle
- Individuelle, selbst angelegte Makro-Buttons (global oder je Schnittstelle); es gibt keine vordefinierten Standard-Makros mehr
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

Makros werden ausschließlich individuell über das Terminal einer
Schnittstelle angelegt (Button **+** neben den Makros) und in
`data/macros.yaml` gespeichert (alternativ über
`RPI_SEMIAUTOMATOR_MACROS`); ein Makro kann global oder nur für die
Schnittstelle gelten, bei der es angelegt wurde. Es gibt keine
vordefinierten Standard-Makros.

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

- `GET /api/ports` – Schnittstellen und Status
- `POST /api/send` – `{"device": "/dev/ttyUSB0", "command": "...", "raw": false}`
- `WS /api/ws?device=/dev/ttyUSB0` – streamt `{"direction": "rx"|"tx", "text": "..."}`; Text oder `{"command": "..."}` senden

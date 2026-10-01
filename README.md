# RPI-SemiAutomator

Ein dockerbasiertes Tool für den Raspberry Pi, das mehrere serielle
Schnittstellen (z. B. `/dev/ttyUSB0`, `/dev/ttyACM0`, ...) gleichzeitig
anzeigt. Über ein Web-UI (Python + [NiceGUI](https://nicegui.io/)) können
Daten live mitgelesen, eigene Befehle gesendet und vordefinierte
"Makro"-Kommandos per Knopfdruck ausgelöst werden.

## Funktionen

- Serielle Ports werden automatisch erkannt und als getrennte Terminal-Tabs angezeigt
- Verbinden/Trennen einzelner Ports mit einstellbarer Baudrate
- Live-Log der gesendeten (`TX`) und empfangenen (`RX`) Daten je Port, mit Button zum Leeren der Anzeige
- Download des aktuellen Logs als lokale Textdatei
- Freitext-Eingabe zum Senden beliebiger Befehle
- Globale und schnittstellenspezifische Makro-Buttons
- Schnittstellen mit selbst vergebenem Namen und Baudrate als Favoriten speichern
- Übersicht der gespeicherten Favoriten (inkl. Entfernen) direkt auf der Startseite
- Läuft als Docker-Container, geeignet für den Raspberry Pi

## Konfiguration

Ports und Makros werden in [`config/config.yaml`](config/config.yaml) definiert:

```yaml
ports:
  - name: "USB0"
    device: "/dev/ttyUSB0"
    baudrate: 115200

macros:
  - label: "Status"
    command: "status"
  - label: "USB0 zurücksetzen"
    command: "reset"
    device: "/dev/ttyUSB0"
```

Der Pfad zur Konfigurationsdatei kann über die Umgebungsvariable
`RPI_SEMIAUTOMATOR_CONFIG` überschrieben werden.
Makros ohne `device` werden für alle Schnittstellen angezeigt; mit `device`
erscheinen sie nur bei der angegebenen Schnittstelle. In der Weboberfläche
kann die Baudrate angepasst werden; über **Als Favorit speichern** öffnet sich
ein Dialog, in dem ein eigener Name für die Schnittstelle vergeben werden
kann. Favoriten werden in `data/favorites.yaml` gespeichert (alternativ über
`RPI_SEMIAUTOMATOR_FAVORITES`) und erscheinen in der aufklappbaren
**Favoriten**-Liste auf der Startseite sowie mit einem Stern (★) im
jeweiligen Terminal-Tab; von dort können sie auch wieder entfernt werden.
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

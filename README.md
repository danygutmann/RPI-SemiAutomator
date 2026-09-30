# RPI-SemiAutomator

Ein dockerbasiertes Tool für den Raspberry Pi, das mehrere serielle
Schnittstellen (z. B. `/dev/ttyUSB0`, `/dev/ttyACM0`, ...) gleichzeitig
anzeigt. Über ein Web-UI (Python + [NiceGUI](https://nicegui.io/)) können
Daten live mitgelesen, eigene Befehle gesendet und vordefinierte
"Makro"-Kommandos per Knopfdruck ausgelöst werden.

## Funktionen

- Übersicht aller konfigurierten sowie automatisch erkannten seriellen Ports
- Verbinden/Trennen einzelner Ports mit einstellbarer Baudrate
- Live-Log der empfangenen Daten je Port
- Freitext-Eingabe zum Senden beliebiger Befehle
- Konfigurierbare Makro-Buttons für häufig genutzte Befehle
- Läuft als Docker-Container, geeignet für den Raspberry Pi

## Konfiguration

Ports und Makros werden in [`config/config.yaml`](config/config.yaml)
definiert:

```yaml
ports:
  - name: "USB0"
    device: "/dev/ttyUSB0"
    baudrate: 115200

macros:
  - label: "Status"
    command: "status"
```

Der Pfad zur Konfigurationsdatei kann über die Umgebungsvariable
`RPI_SEMIAUTOMATOR_CONFIG` überschrieben werden.

## Start mit Docker

```bash
docker compose up --build
```

Die Weboberfläche ist danach unter `http://<raspberry-pi-ip>:8080`
erreichbar.

Passe in [`docker-compose.yml`](docker-compose.yml) die Liste unter
`devices:` an die tatsächlich verwendeten seriellen Schnittstellen deines
Raspberry Pi an (`ls /dev/tty{USB,ACM}*` auf dem Host zeigt die verfügbaren
Geräte).

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


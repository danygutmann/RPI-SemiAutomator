# RPI-SemiAutomator

Ein dockerbasiertes Tool für den Raspberry Pi, das mehrere serielle
Schnittstellen (z. B. `/dev/ttyUSB0`, `/dev/ttyACM0`, ...) gleichzeitig
anzeigt. Über ein Web-UI (Python + [NiceGUI](https://nicegui.io/)) können
Daten live mitgelesen, eigene Befehle gesendet und vordefinierte
"Makro"-Kommandos per Knopfdruck ausgelöst werden.

## Funktionen

- Serielle Ports werden automatisch erkannt; auf der Startseite werden jedoch
  nur Schnittstellen angezeigt, die zuvor als **Favorit** gespeichert wurden
- Verbinden/Trennen einzelner Ports; Baudraten werden in den Einstellungen
  pro Favorit konfiguriert
- Live-Konsole je Port: In die Konsole klicken und direkt tippen (Enter, Tab, Pfeiltasten, Strg+Taste werden direkt gesendet; leeres Enter möglich), mit Button zum Leeren der Anzeige; das Terminal-Fenster ist in der Höhe per Ziehen am unteren Rand verstellbar
- Download des aktuellen Logs als lokale Textdatei
- Freitext-Eingabe zum Senden beliebiger Befehle
- Individuelle, aliasgebundene Makros im Burger-Menü; Ordner
  können über `/` mehrstufig verschachtelt und ein- oder ausgeklappt werden
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

Makros werden individuell über das Burger-Menü angelegt (Rechtsklick auf einen
Makro-Ordner, einen Eintrag oder den freien Bereich im Makro-Burger-Menü) und
in `data/macros.yaml` gespeichert (alternativ über
`RPI_SEMIAUTOMATOR_MACROS`). Jedes Makro ist ausschließlich an den Alias der
aktiven Schnittstelle gebunden, nicht an den Gerätepfad; dadurch kann derselbe
Alias auch auf Systemen mit abweichenden Gerätepfaden verwendet werden. Ein
Ordnerpfad wie `System/Start` erstellt eine verschachtelte Makrostruktur.
Makros werden nur beim passenden Alias im Burger-Menü angezeigt und dort
ausgeführt; Bearbeiten und Löschen erfolgt weiterhin in den Einstellungen.
Es gibt keine vordefinierten Standard-Makros.

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
- Die Schnittstellen-Tabs stehen zentriert oben; der Burger enthält nur die
  Makros, keine zusätzliche Schnittstellenauswahl.
- Favoriten und deren Baudraten werden unter **Einstellungen** verwaltet.
- Makros werden über das Burger-Menü nach Ordnern organisiert. Mit einem
  Rechtsklick auf den freien Bereich, einen Ordner oder ein Makro kann ein
  weiteres Makro in dieser Gruppe angelegt werden; Ordnerpfade verwenden `/`.
- Unter **Einstellungen** können Favoriten und Makros bearbeitet und gelöscht
  werden.

## API

Optional absicherbar mit `RPI_SEMIAUTOMATOR_API_TOKEN` (Bearer-Token im `Authorization`-Header oder `?token=`).

- Swagger/OpenAPI-Doku: `/docs` (Schema unter `/openapi.json`)
- `GET /api/ports` – Schnittstellen und Status
- `POST /api/send` – `{"device": "/dev/ttyUSB0", "command": "...", "raw": false}`
- `WS /api/ws?device=/dev/ttyUSB0` – streamt `{"direction": "rx"|"tx", "text": "..."}`; Text oder `{"command": "..."}` senden
- `GET /api/aliases` – Schnittstellen, die mit einem Alias konfiguriert sind
- `POST /api/aliases/{alias}/send` – wie `/api/send`, aber statt `device`
  wird der Alias im URL-Pfad verwendet; der JSON-Body enthält
  `{"command": "...", "raw": false}`
- `WS /api/aliases/{alias}/ws` – WebSocket-Stream und Eingabe wie `/api/ws`,
  adressiert über den Alias statt den Gerätepfad
- `GET /api/macros` – alle aliasgebundenen Makros; mit `?alias=Bench` nur die
  Makros des angegebenen Alias
- `GET /api/aliases/{alias}/macros` – Makros für den angegebenen Alias
- `POST /api/macros` – Makro anlegen, zum Beispiel:
  `{"label":"Status","command":"status","raw":false,"alias":"Bench","category":"System"}`
  (`alias` ist erforderlich; bei gleichem Namen und Alias wird HTTP 409
  zurückgegeben)
- `PUT /api/macros` – Makro anhand seiner bisherigen Angaben bearbeiten. Der
  Body enthält dieselben Felder wie beim Anlegen sowie `old_label` und
  `old_alias`. Ein nicht gefundenes Makro ergibt HTTP 404.
- `DELETE /api/macros/{label}?alias=Bench` – Makro für den Alias löschen. Ein
  nicht gefundenes Makro ergibt HTTP 404.
- `GET /api/backup` – lädt ein YAML-Backup mit Favoriten und Makros als
  `rpi-semiautomator-backup.yaml` herunter.

Der Alias ist der **Name** des Ports aus `config/config.yaml` oder der Name
eines unter Einstellungen gespeicherten Favoriten. Favoriten-Namen haben
Vorrang. `GET /api/ports` enthält ebenfalls ein Feld `alias` (bei nicht
benannten Schnittstellen `null`). Jeder Alias muss eindeutig sein; ein
mehrdeutiger Alias wird beim Senden mit HTTP 409 abgewiesen. Für Aliase mit
Leerzeichen müssen diese im URL-Pfad URL-kodiert werden. Die bestehenden
gerätepfadbasierten Endpunkte bleiben weiterhin verfügbar.

Alle HTTP-Endpunkte können optional mit `RPI_SEMIAUTOMATOR_API_TOKEN`
abgesichert werden. Bei gesetztem Token muss es im Authorization-Header als
Bearer-Token mitgesendet werden; alternativ ist der Query-Parameter `token`
möglich. Bei geschützten WebSockets gelten dieselben Optionen (Token im
Query-Parameter oder Bearer-Header). Die Makro-API liest und bearbeitet die
individuellen Makros aus `data/macros.yaml` (bzw.
`RPI_SEMIAUTOMATOR_MACROS`); Port-Voreinstellungen aus `config/config.yaml`
werden dadurch nicht verändert. Das Backup enthält gespeicherte Favoriten und
individuelle Makros.

# Betrieb auf dem AI-Host

## Voraussetzungen

nginx muss auf `127.0.0.1:443` erreichbar sein und für SNI/Host
`nominatim.oklabflensburg.de` die bestehende Nominatim-Konfiguration bedienen.
Das Zertifikat muss gültig sein, den Hostnamen enthalten und mit vollständiger
Zertifikatskette ausgeliefert werden. Der Geocoder nutzt das öffentliche
CA-Bundle von certifi; Erneuerung und CA-Bundle-Updates gehören zum Betrieb.

Der uWSGI-Upstream bleibt ausschließlich bei nginx:

```nginx
uwsgi_pass nominatim_service;
```

Keine Änderung am Socket-Protokoll, kein HTTP-Client gegen `/run/nominatim.sock`.
Keine öffentliche Reverse-Proxy-Route für den Geocoder einrichten.

## Datenschutz über die gesamte Aufrufkette

Der Geocoder selbst schreibt keine Ortsdaten und deaktiviert sein Request-Logging.
Die interne GET-Anfrage an nginx enthält jedoch `q`, `lat`, `lon` oder `osm_ids`.
Vor produktiven Tests mit echten Daten müssen auch nginx und Nominatim dafür
konfiguriert sein:

- In den betroffenen nginx-Locations `/status`, `/search`, `/reverse`, `/lookup`
  Access-Logs deaktivieren. Keine Logs mit `$request`, `$args`, `$request_uri`,
  Authorization, Bodies oder Upstream-Antworten verwenden.
- Auch nginx-Error-Logs können die Request-URL enthalten. Für diese Locations
  sensible Error-Logs deaktivieren, etwa mit `error_log /dev/null;`, oder eine
  nachweisbar vor dem Schreiben bereinigende Lösung betreiben.
- Nominatim-/Gunicorn-/uWSGI-Access-Logging für diese Requests abschalten.
  Debug-Logs, APM, Tracing, Analytics und Response-Caches nicht einschalten.
- Die vorhandenen `uwsgi_pass`-/`include`-Direktiven beibehalten. Beispielhafte
  Datenschutzdirektiven zum Einfügen in die **bestehenden** Locations:

```nginx
access_log off;
error_log /dev/null;
```

Das ist keine vollständige nginx-Konfiguration. Vererbte oder weitere Log-Ziele,
Upstream-Logs und Monitoring-Agenten auf dem Zielhost prüfen. Konfigurationsänderungen
mit `nginx -t` validieren. Ausschließlich synthetische Testdaten verwenden, bis
klar ist, dass die ganze Kette keine Ortsdaten oder Bearer-Keys protokolliert.
Die mitgelieferte Unit verhindert zusätzlich Core-Dumps (`LimitCORE=0`).

## Installation

Die folgenden Schritte führt die Administration auf dem Zielhost aus.
Repository und venv bleiben im Betrieb schreibgeschützt für den Service-User.

```bash
sudo useradd --system --user-group --no-create-home \
  --shell /usr/sbin/nologin research-geocoder
sudo install -d -m 0755 /opt/uranus-research-geocoder
sudo git clone https://github.com/sndcds/uranus-research-geocoder.git \
  /opt/uranus-research-geocoder
cd /opt/uranus-research-geocoder
# Geprüften Release/Commit auschecken, bevor die Installation erfolgt.
sudo uv sync --locked --no-dev --python 3.13 --python-preference only-system
sudo chown -R root:root /opt/uranus-research-geocoder
sudo chmod -R go-w /opt/uranus-research-geocoder
sudo install -d -m 0700 /etc/research-geocoder
```

`uv` und ein für den Service-User ausführbares Python 3.13 müssen systemweit
vorhanden sein. Keine venv mit Interpreter-Symlink unter `/home` oder `/root`
verwenden: `ProtectHome=true` sperrt diese Verzeichnisse.
`--python-preference only-system` vermeidet einen Interpreter aus dem uv-Cache;
mit `.venv/bin/python --version` und `readlink -f .venv/bin/python` prüfen.

Environment-Datei einmalig mit zufälligem Schlüssel erzeugen, ohne ihn auszugeben
oder in der Shell-History zu speichern (bricht ab, wenn die Datei schon existiert):

```bash
sudo python3 - <<'PY'
import os
import secrets

path = '/etc/research-geocoder/geocoder.env'
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as target:
    target.write('GEOCODER_API_KEY=' + secrets.token_urlsafe(48) + '\n')
    target.write('GEOCODER_NOMINATIM_BASE_URL=https://nominatim.oklabflensburg.de\n')
    target.write('GEOCODER_TIMEOUT_SECONDS=5\nGEOCODER_CONCURRENCY=4\n')
PY
sudo install -m 0644 deploy/research-geocoder.service \
  /etc/systemd/system/research-geocoder.service
sudo systemctl daemon-reload
sudo systemctl enable --now research-geocoder
```

systemd liest die root-eigene Environment-Datei, bevor es zum Service-User
wechselt. Port ist ausschließlich **`127.0.0.1:6337`**, Worker-Anzahl **1**.
Die [Unit](../deploy/research-geocoder.service) enthält Dateisystem-, Kernel-,
Geräte- und Prozesshärtung sowie Memory-/Task-Limits.

## Lokale Smoke-Tests

Auf dem AI-Host ausführen; `GEOCODER_API_KEY` sicher aus der Environment-Datei
in die aktuelle Shell übernehmen. Keine Shell-Debug-Ausgabe (`set -x`) und
kein `curl -v` verwenden. `$GEOCODER_API_KEY` nie als Literal in die History schreiben.
Die unten verlangte `curl -H`-Form kann kurzzeitig im Prozessargument sichtbar
sein; auf gemeinsam genutzten Hosts den Header über eine geschützte stdin-
Konfiguration (`curl --config -`) einspeisen.

nginx-Routing mit normaler TLS-Prüfung prüfen:

```bash
curl --noproxy '*' --resolve nominatim.oklabflensburg.de:443:127.0.0.1 \
  --max-time 5 --fail-with-body -sS \
  'https://nominatim.oklabflensburg.de/status?format=json'
```

Erwartet: HTTP 200 und `status: 0`. Kein `-k`, kein `-L`.

```bash
curl -sS http://127.0.0.1:6337/health

curl -sS \
  -H "Authorization: Bearer $GEOCODER_API_KEY" \
  http://127.0.0.1:6337/ready

curl -sS \
  -X POST \
  -H "Authorization: Bearer $GEOCODER_API_KEY" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:6337/search \
  -d '{"query":"Bachstraße Flensburg","limit":5}' \
  | jq

curl -sS \
  -X POST \
  -H "Authorization: Bearer $GEOCODER_API_KEY" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:6337/reverse \
  -d '{"latitude":54.79,"longitude":9.43}' \
  | jq
```

Health: `{"status":"ok"}`; Ready: `{"status":"ready"}`.
Search/Reverse liefern normalisierte Orte oder `404 no_match`, abhängig vom
lokal importierten Datenbestand. Eine echte vom Search gelieferte OSM-ID lässt
sich über `/lookup` prüfen. Für Curl auf Hosts mit Proxy-Environment zusätzlich
`--noproxy '*'` verwenden; der Dienst selbst ignoriert Proxy-Environment immer.

Mit `ss -ltn '( sport = :6337 )'` die Loopback-Bindung kontrollieren.
Authentifiziertes Ready prüft den gesamten lokalen Weg einschließlich TLS und
Nominatim-Datenbank; Health prüft nur den Prozess. Keine sensitiven Payloads
in Monitoring aufnehmen. Für Ausfälle nur Status/Fehlercode verwenden.

## Updates

Nach geprüftem Commit-Update `uv sync --locked --no-dev` mit systemweitem Python
wiederholen und den Dienst neu starten. Bei Zertifikatsproblemen nginx-Zertifikat,
Kette, Hostnamen und Systemzeit korrigieren; keine TLS-Prüfung abschalten.
Der Dienst bietet keine Konfiguration für alternative URLs, Ports oder Proxys.
Tests mit geändertem Transport sind ausschließlich eine lokale Test-Injektion.

Die CI prüft API, Grenzen und TLS mit synthetischen Daten. Ein erfolgreicher
CI-Lauf ersetzt die Smoke-Tests auf dem tatsächlichen AI-Host nicht.

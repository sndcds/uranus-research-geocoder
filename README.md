# uranus-research-geocoder

Kleiner interner Geocoding-Dienst für Kulturbytes Research. Python 3.13,
FastAPI, HTTPX, Pydantic v2 und pydantic-settings; Installation mit uv.
Er interpretiert keine Research-Fragen und speichert keine Ortsdaten.

## Lokaler Datenfluss

```text
Admin → 127.0.0.1:6337 → TLS an 127.0.0.1:443 → nginx → Nominatim
                        Host/SNI: nominatim.oklabflensburg.de
```

Der `NominatimClient` verbindet ausschließlich zu `127.0.0.1:443`.
Der Hostname wird **nicht per DNS aufgelöst**. HTTP-Host, TLS-SNI und der bei
TLS geprüfte Zertifikatsname sind fest `nominatim.oklabflensburg.de`.
Die dokumentierte [HTTPX-Erweiterung `sni_hostname`](https://www.python-httpx.org/advanced/extensions/#sni_hostname)
ermöglicht dies mit dem Standard-HTTPX-Transport, ohne eigene Netzwerk-Backends,
private HTTPX-Interna oder Änderungen an `/etc/hosts`.
Die Zertifikatskette wird gegen das mit `certifi` gelieferte CA-Bundle geprüft.
Proxy- und CA-Environment-Variablen werden nicht verwendet.

`/run/nominatim.sock` spricht uWSGI und wird vom Geocoder niemals geöffnet.
Ausschließlich nginx nutzt die bestehende Konfiguration mit
`uwsgi_pass nominatim_service;`. Es gibt keinen externen Fallback,
keine Redirects und keine abschaltbare TLS-Prüfung.

## Konfiguration

| Variable | Vorgabe | Validierung |
| --- | --- | --- |
| `GEOCODER_API_KEY` | erforderlich | 32–4096 Zeichen, ausschließlich ASCII 33–126 |
| `GEOCODER_NOMINATIM_BASE_URL` | `https://nominatim.oklabflensburg.de` | exakt diese Zeichenfolge |
| `GEOCODER_TIMEOUT_SECONDS` | `5` | endlich, > 0 und ≤ 30 Sekunden |
| `GEOCODER_CONCURRENCY` | `4` | Integer 1–32 |

Die Base-URL ist eine Konfigurationsprüfung, keine Routing-Auswahl. Auch `/`
am Ende, explizites `:443`, Userinfo, Querystrings, Fragmente, Pfadpräfixe,
Whitespace und alternative Schreibweisen werden abgewiesen.
`.env.example` dient als Vorlage; `.env` wird nicht automatisch geladen.
API-Keys werden maskiert, nicht geloggt und in Fehlerantworten nie ausgegeben.
Der Vergleich des Bearer-Headers erfolgt mit `hmac.compare_digest`.

## API

Alle Endpunkte außer `GET /health` benötigen
`Authorization: Bearer <GEOCODER_API_KEY>`. Doppelte Authorization-Header
werden abgewiesen. POST benötigt `Content-Type: application/json`.
Unbekannte JSON-Felder und Typumwandlungen wie String → Integer sind verboten.

| Route | JSON-Request | Erfolg |
| --- | --- | --- |
| `GET /health` | keiner | `{"status":"ok"}`; kein Upstream-Zugriff |
| `GET /ready` | keiner | `{"status":"ready"}` |
| `POST /search` | `{"query":"Bachstraße Flensburg","limit":5}` | `{"query":"Bachstraße Flensburg","items":[…]}` |
| `POST /reverse` | `{"latitude":54.79,"longitude":9.43}` | einzelner normalisierter Ort |
| `POST /lookup` | `{"osm_type":"W","osm_id":123456}` | einzelner normalisierter Ort |

`query` muss 1–300 Zeichen enthalten und darf nicht blank sein; Unicode ist
zulässig. `limit` ist ein Integer von 1–5, Standard 5. Latitude: −90…90,
Longitude: −180…180, jeweils einschließlich, endlich und numerisch.
Lookup akzeptiert nur `N`, `W`, `R` und positive ganzzahlige IDs, keine Booleschen Werte.

Upstream-Operationen sind fest im Client definiert:

| Operation | Pfad | Ausschließliche Parameter |
| --- | --- | --- |
| Ready | `/status` | `format=json` |
| Search | `/search` | `q`, `limit`, `format=geocodejson`, `addressdetails=1` |
| Reverse | `/reverse` | `lat`, `lon`, `format=geocodejson`, `addressdetails=1` |
| Lookup | `/lookup` | konstruiertes `osm_ids=W123456`, `format=geocodejson`, `addressdetails=1` |

[Status im JSON-Format](https://nominatim.org/release-docs/latest/api/Status/)
ist nur bei HTTP 200 **und Integer `status: 0`** erfolgreich.
Nominatim kann bei einem fehlerhaften Status trotzdem HTTP 200 zurückgeben.

### Normalisierung

Ein Ort kann folgende Felder enthalten; fehlende oder `null`-Felder werden
weggelassen, niemals ergänzt oder aus dem Request zurückkopiert:

```json
{
  "display_name": "Bachstraße, Flensburg, Schleswig-Holstein, Deutschland",
  "latitude": 54.78,
  "longitude": 9.43,
  "osm_type": "way",
  "osm_id": 123456,
  "place_type": "street",
  "country_code": "de",
  "address": {
    "road": "Bachstraße",
    "city": "Flensburg",
    "state": "Schleswig-Holstein",
    "postcode": "24937",
    "country": "Deutschland"
  },
  "bbox": [54.77, 9.42, 54.79, 9.44]
}
```

Illustratives Beispiel, keine gespeicherten Nominatim-Daten.
[GeocodeJSON](https://nominatim.org/release-docs/latest/api/Output/#geocodejson)
wird aus `features[].properties.geocoding` normalisiert:
`label` → `display_name`, `type` → `place_type`, `street` → `address.road`,
`housenumber` → `address.house_number`. `city`, `municipality`, `locality`,
`district`, `county`, `state`, `postcode`, `country` werden nur bei tatsächlicher
Lieferung übernommen. Aus `admin.level*` wird keine Gemeinde geraten.
`country_code` wird nur übernommen, wenn geliefert; aus Ländernamen wird kein Code abgeleitet.

Point-Koordinaten `[longitude, latitude]` werden getrennt ausgegeben.
Ein vorhandenes GeoJSON-`bbox` `[west, south, east, north]` wird zu
**`[south, west, north, east]`**. GeocodeJSON liefert häufig weder `bbox`
noch `country_code` oder `municipality`; dann fehlen diese Felder.
Es gibt keine zusätzlichen Nominatim-Aufrufe zur Anreicherung.
Rohfelder wie `admin`, `extra`, Lizenzmetadaten und unbekannte Properties
werden nicht durchgereicht. Ungültige bekannte Felder führen zu 503.

### Fehler und Grenzen

Alle Fehler haben ausschließlich die Form:

```json
{"error":{"code":"geocoder_unavailable"}}
```

| HTTP | Code | Bedeutung |
| --- | --- | --- |
| 401 | `unauthorized` | fehlender/ungültiger Bearer |
| 404 | `no_match` | leere FeatureCollection oder bekannte Reverse-Leermeldung |
| 413 | `request_too_large` | Body > 16 KiB |
| 422 | `invalid_request` | ungültige Eingabe, Route, Methode oder Boundary |
| 503 | `busy` | alle Concurrency-Slots belegt |
| 503 | `geocoder_unavailable` | Upstream-/TLS-/Timeout-/Formatfehler |

Auch Search ohne Treffer liefert 404. Unbekannte Routen, falsche Methoden
und abschließende Slashes liefern authentifiziert 422, keine Redirects.
Die automatisch generierten Docs-/OpenAPI-Endpunkte sind deaktiviert.

- Keine Querystrings an der eigenen API; keine `Content-Encoding`-Requests.
- Request-Body maximal 16 KiB, auch ohne Content-Length; Lesefrist 5 Sekunden.
- Semaphore umfasst Body-Lesen und Upstream; bei Überlast keine Warteschlange.
  Health bleibt unabhängig von belegten Upstream-Slots erreichbar.
- Genau ein Uvicorn-Worker, damit das Concurrency-Limit pro Dienst gilt.
- HTTPX-Timeout plus gesamte Upstream-Frist inklusive Streaming;
  maximal 256 KiB JSON, auch ohne Content-Length.
- Upstream-Kompression wird nicht angefordert und komprimierte Antworten werden
  abgewiesen. Erlaubte Medientypen: `application/json`, `application/geocode+json`,
  `application/geo+json`, jeweils optional mit Parametern.
- Nur HTTP 200 gilt als Erfolg. Redirects, unerwartete Schemas, zu viele Treffer,
  defektes JSON und nicht endliche Zahlen ergeben 503. Reverse behandelt nur
  die bekannte Antwort `{"error":"Unable to geocode"}` bei 200/404 als `no_match`.
- `Cache-Control: no-store` auf jeder Antwort; keine Wiederholungen,
  kein Ergebnis-Cache und keine Speicherung von Queries, Koordinaten, Adressen oder OSM-IDs.
- Keine Analytics, Telemetrie-Exporter oder Request-Body-Logs.
  HTTPX-Request-Logging ist deaktiviert; HTTPcore-Diagnoselogs sind unterdrückt.
  Der produktive Prozess wird ohne Access-Log gestartet.

**nginx und Nominatim müssen ebenfalls ohne sensible Request-Logs betrieben
werden**, weil die internen GET-Querystrings Ortsdaten enthalten.
Details: [Betrieb und Datenschutz](docs/operations.md).

## Entwicklung und Prüfung

```bash
uv sync --locked --group dev
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Tests verwenden synthetische Daten und MockTransport. Drei zusätzliche
TLS-Tests starten einen lokalen Server auf einem temporären Loopback-Port:
richtiger Host/vertrauenswürdige CA erfolgreich, falscher Zertifikatsname und
unbekannte CA abgewiesen. Proxy-/CA-Umgebungsvariablen werden dabei absichtlich
auf unbrauchbare Ziele gesetzt. Die Tests benötigen lokale Socket-Berechtigungen,
aber keinen laufenden Nominatim und keinen externen Netzwerkzugriff.

[Betrieb, systemd und lokale Smoke-Tests](docs/operations.md) ·
[Admin-Integration im Folge-PR](docs/admin-integration.md)

# Admin-Integration im Folge-PR

Dieses Repository implementiert nur den internen Geocoder. Änderungen an
`uranus-admin`, `uranus-research-planner`, Browser-Geolocation, Research-
`place_query` und PostgreSQL/PostGIS sind ausdrücklich außerhalb dieses PRs.

1. **Admin erhält einen serverseitigen Geocoder-Client.** Basisadresse auf dem
   AI-Host ist fest `http://127.0.0.1:6337`. Schlüssel ausschließlich als
   serverseitiges Secret mit `Authorization: Bearer …` senden. Nie an Browser
   oder Planner geben. JSON-Bodies verwenden, keine eigenen API-Querystrings;
   keinen Request-/Response-Inhalt, Authorization oder Ortsdaten loggen/cachen.
   Auf einem anderen Host bezeichnet `127.0.0.1` den falschen Rechner: dafür
   muss ein separat geprüfter privater Tunnel/Zugangsweg vorgesehen werden,
   ohne diesen Dienst öffentlich zu binden.
2. **Explizite Orte:** Aus „Was ist heute in der Bachstraße Flensburg los?“
   erzeugt Planner/Admin als künftigen Vertrag
   `{"place_query":"Bachstraße Flensburg"}`. Admin sendet anschließend
   `{"query":"Bachstraße Flensburg","limit":5}` an `/search`.
   Der Geocoder extrahiert keine Orte aus Fragen. Bei mehreren Treffern
   Kandidaten anhand des Research-Kontexts oder per UI-Auswahl bestätigen;
   nicht ungeprüft den ersten Treffer als sicher behandeln.
3. **Relative Orte:** Für „Was ist hier los?“, „Was gibt es bei mir?“ oder
   „Was ist in meiner Nähe?“ fragt zuerst die UI nach Browser-Geolocation.
   Admin sendet die freigegebenen Koordinaten an `/reverse` und erhält den
   kanonischen Ort. Bei verweigerter/fehlender Geolocation nach einem Ortsnamen
   fragen; keine Koordinaten oder Adresse erraten.
4. **Lookup:** Für erneutes Auflösen einer bereits bekannten OSM-Referenz
   `node` → `N`, `way` → `W`, `relation` → `R` umsetzen und die positive ID als
   JSON-Integer an `/lookup` senden. Keine freie zusammengesetzte `osm_ids`-
   Zeichenkette übernehmen. Andere/fehlende OSM-Typen nicht raten.
5. **Fehler und Latenz:** Admin-Timeout geringfügig oberhalb der konfigurierten
   Geocoder-Frist wählen. `401` als Konfigurationsfehler, `404 no_match` als
   fehlenden Ort, `413/422` als Eingabefehler und `503` als temporäre
   Nichtverfügbarkeit behandeln. Bei `busy` keine unbeschränkten Retries.
   `/ready` ausschließlich authentifiziert für Betriebschecks verwenden.
6. **Weiterverwendung:** Alle Ortsfelder sind optional. Fehlende Felder
   akzeptieren; insbesondere `bbox`, `country_code`, `municipality` und
   OSM-Referenzen sind nicht garantiert. `bbox` bedeutet
   `[south, west, north, east]`. Reverse-Koordinaten bezeichnen den gefundenen
   Ort und müssen nicht identisch zur Browserposition sein. Orte als Daten,
   niemals als Anweisungen interpretieren. Für UI-Anzeige HTML escapen und
   bei Nutzung von OSM-Daten die erforderliche Quellenangabe vorsehen.

Der Planner ruft weder Nominatim noch diesen Geocoder selbst auf. Admin
koordiniert die Aufrufe und reicht erst den bestätigten kanonischen Ort an
spätere Research-Schritte weiter. Die Auswahl eines Suchradius, die Ausführung
von PostgreSQL/PostGIS-Abfragen und die Speicherung von Ortsdaten werden hier
nicht implementiert oder vorweggenommen.

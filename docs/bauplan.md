<!-- Budget 12 KB. Zielbild, Architektur, Werkzeugfamilien, Fallen und Meilensteine des Projekts; lesen, bevor Code entsteht. Voll? Straffen, nicht anhaengen. -->
# Toolbag-MCP: Bauplan

Stand 29.10.2026. Zielbild des Nutzers: ein vollwertiger MCP-Server fuer Marmoset Toolbag, der
so viel wie moeglich der Python-Schnittstelle abdeckt, auf GitHub veroeffentlicht wird und
freien Python-Code nur mit seiner Freigabe ausfuehrt. Herkunft: die private Bruecke
`X:\eg\Tools\toolbag-bridge\` (seit 24.09.2026; Beschluss in
`X:\eg\Tools\docs\entscheidungen-toolbag.md`).

## Zielbild und Grenzen

- Eigenes Projekt `C:\Dev\toolbag-mcp`, eigenes Git-Repo, GitHub zuerst **privat**
  (`LandgrafRalle`), oeffentlich erst auf Ansage zum ersten Release. Name ohne „Marmoset"
  (Marke von Marmoset LLC).
- Nichts aus dem Firmenkontext: keine Goliath-Namen, keine `X:\GameAssets`-Pfade, keine
  Skew-Masken, kein `fix_ao`. Das bleibt in Unreal Tools und haengt sich spaeter als
  Erweiterung an.
- Toolbag 5.0x primaer (lokal 5.0.2.2; Referenz `marmoset.co/python/reference5.html`, Build
  5.03), 4.0x nach Moeglichkeit, macOS ungetestet. Toolbags Python ist 3.12 ohne pip und numpy.
- Entscheidungen des Nutzers, nicht meine: Lizenz (Vorschlag MIT) und Autorenname, Zeitpunkt
  privat → oeffentlich, endgueltiger Projektname.

## Architektur: zwei Teile, ein Repo

### `plugin/` (laeuft in Toolbag)

- `__main__.py`: HTTP-Server (stdlib) im Nebenthread auf `127.0.0.1:<port>` (neuer Standard,
  z. B. 18770, im Fenster einstellbar). `GET /` liefert Status (Version, Szene, Haken),
  `POST /call` nimmt `{"action": "...", "params": {...}, "timeout": s}`. Jede Anfrage braucht
  den Pflicht-Header (`X-Toolbag-MCP: 1`), einen passenden `Host` und das Token des Starts.
  Browser koennen den Header nicht ohne Vorabanfrage senden, und der Server beantwortet keine.
- Warteschlange: der Nebenthread legt Jobs ab, `mset.callbacks.onPeriodicUpdate` (nur dieser
  Callback, `onFrameUpdate` bleibt ungesetzt) arbeitet sie auf dem Hauptthread ab, denn
  `mset` ist nicht threadsicher. Szenenwechsel laufen zweiphasig, sonst stuerzt 5.02 ab
  (`pitfalls.md`). Ergebnis als JSON, Fehler mit vollem Traceback, Zeitueberschreitung als
  klare Antwort (der Job laeuft dann trotzdem noch zu Ende, so dokumentieren).
- `actions.py`: benannte Aktionen mit Parameterpruefung (Werkzeugfamilien unten). Kein freier
  `exec`, ausser der Haken **Allow Python execution** im Fenster ist gesetzt (Standard aus,
  nicht persistent, gilt nur fuer diesen Start).
- Fenster (`mset.UIWindow`): Status, Port, Zaehler, der Haken, Beenden. Ein Plugin lebt nur,
  solange es ein Fenster hat; `onShutdownPlugin` raeumt den Server ab.
- Token: beim Start erzeugt und mit Port und PID nach `~\.toolbag-mcp\plugin.json`
  geschrieben, **nie unter
  `%LOCALAPPDATA%`**: die Claude-Desktop-App ist ein Store-Paket, Schreibzugriffe ihrer Prozesse
  landen umgeleitet in `Packages\…\LocalCache` (Gedaechtnis Unreal Tools
  `claude-app-appdata-umleitung`); Toolbag aus dem Explorer schriebe woanders hin, als der
  Server liest. Im README fuer Nutzer solcher Clients erwaehnen.

### `toolbag_mcp/` (MCP-Server, laeuft aussen)

- Offizielles Python-SDK `mcp` (2.3.0: `from mcp.server import MCPServer`; `FastMCP` heisst dort
  nicht mehr so, also 2.x nehmen oder `mcp<2` pinnen), Transport stdio, nie `print()` auf stdout.
  Je Aktion ein Werkzeug mit typisierten Parametern und Docstring; Fehler aus Toolbag als
  Werkzeugfehler mit Traceback; Ressourcen `toolbag://scene`, `toolbag://materials`,
  `toolbag://baker`.
- **Fokus-Handhabung** (Mehrwert gegenueber allen Fremdprojekten): Toolbag arbeitet seinen
  Callback nur ab, solange sein Fenster vorne ist („Toolbag only performs updates regularly when
  the app has focus", offizielle Doku). Der Server holt Toolbag fuer die Dauer eines Aufrufs nach
  vorn, alle zwei Sekunden erneut, und gibt den Fokus danach zurueck. Vorlage `tb.py`:
  `_find_toolbag` ueber den Prozessnamen `toolbag.exe` (nicht den Titel, Browser-Tabs tragen
  denselben), `_to_front` mit `AttachThreadInput` und Rueckfall `WScript.Shell.AppActivate`.
  Windows-only, abschaltbar.
- Kommandos: `toolbag-mcp` (Server), `toolbag-mcp install|uninstall|verify` (Plugin-Ordner als
  Junction oder Kopie nach `%LOCALAPPDATA%\Marmoset Toolbag 5\plugins\Toolbag MCP\`, Quittung,
  Hinweis auf Edit → Plugins → Refresh), `toolbag-mcp doctor` (Port, Token, Toolbag-Prozess,
  fremde Plugins mit `onPeriodicUpdate`).
- Start fuer Nutzer: `uvx toolbag-mcp` oder `uv run`; Registrierung fuer Claude Code
  (Benutzer-Umfang, grosses `timeout` fuer Bakes), Claude Desktop und Cursor im README.
- Timeouts je Werkzeug: Lesen 30 s, Import und Bake 600 s.

## Werkzeugfamilien („so viel wie moeglich"), Reihenfolge ist Baureihenfolge

1. **Diagnose:** `status` (Version, GPU, Szenenpfad, Haken), `capabilities` (`dir(mset)`, fuer
   Versionsunterschiede), Preferences lesen.
2. **Szene:** new, load, save, save_as, path; Objekte listen (Typ, Hierarchie, Sichtbarkeit,
   Bounds, uid), finden, Auswahl lesen und setzen, Sichtbarkeit setzen, Transform lesen und
   setzen, umbenennen, duplizieren, loeschen (`destroy`), gruppieren, Modell importieren
   (`importModel`), Szene exportieren (FBX, OBJ, glTF, USD), Kamera framen.
3. **Materialien:** listen, inspizieren (Subroutinen, `getFieldNames`, Werte, Texturpfade, sRGB,
   zugewiesene Objekte), anlegen, duplizieren, loeschen, zuweisen, Subroutine setzen, Feld
   setzen (Textur per Pfad, Zahl, Bool, Farbe), Cutout-Griff (Transparency auf Cutout, *Use
   Albedo Alpha* aus, Kanal R, Textur erst leeren und dann neu setzen, damit Toolbag die Datei
   neu liest), Material importieren und exportieren.
4. **Baker:** Bake-Projekte listen und inspizieren (Gruppen, Low und High, Cage min/max,
   Einstellungen, Maps), anlegen, Gruppe anlegen, Quick-Load, Einstellungen setzen, Maps an und
   aus mit Optionen (z. B. Flip Y), Cage setzen oder schaetzen, backen, backen nach Pfad mit
   Groesse, Samples und Maps, Map-Pfade (`getMapPath`), Texture Sets und UDIM, Presets,
   **Low-Geometrie tauschen** (Vorlage `replace_low`: FBX importieren, Lage vergleichen,
   `target.mesh = src.mesh`, Import loeschen; Gruppe, Cage und Material bleiben), Rueckseiten
   zeigen (`cullBackFaces`).
5. **Licht, Sky, Fog, Kamera:** listen, anlegen (ueber Duplikat eines vorhandenen, wenn kein
   Konstruktor greift; so loest es auch das Fremdprojekt), aendern, Sky laden und setzen, Fog,
   Kamera (Objektiv, Tiefenschaerfe, Post, Tonemapper, Belichtung).
6. **Render:** Kamera in Bild (`renderCamera`), Bilder und Videos ueber Render-Objekte,
   Render-Einstellungen.
7. **`execute_python`:** mit Namensraum, der zwischen Aufrufen bleibt, und `result`-Variable wie
   die alte Bruecke; ohne Haken im Plugin kommt eine klare Ablehnung zurueck.

## Fallen, die Code und Doku kennen muessen (belegt an 5.0.2, `X:\eg\Tools\toolbag-bridge\LIESMICH-api.md`)

- **Nie `SubMeshObject.visible` setzen:** setzt ein Flag, nach dem Toolbag die ganze Szene
  koernig und Teile leer backt; am Mesh-Objekt fuehrte es beim Laden zum Absturz. Sichtbarkeit
  nur an Gruppen- und High/Low-Knoten; die Aktion muss das erzwingen.
- **Nur ein Plugin darf `onPeriodicUpdate` setzen.** Zwei Plugins mit eigenem Callback legen
  sich gegenseitig lahm. Das neue Plugin laeuft darum nie neben „Claude Bridge" oder „Skew Masks".
- **Fokus-Einfrieren,** siehe oben: ohne Vordergrund kein Timeout, einfach keine Antwort.
- **`Material.getSubroutine` mit unbekanntem Namen wirft** (`IndexError` als `SystemError`); ein
  Material ohne Transparenz liefert `None`, erst `setSubroutine("transparency", "Cutout")`.
- **`MeshObject.mesh` lesen, dann speichern** schreibt Low-Meshes ohne Attribut 7 (Cage-Richtung);
  vor dem Auslesen speichern lassen, nicht danach.
- **Haengt die Bruecke, hilft kein Neustart des Plugins:** der Port lauscht weiter, nimmt nichts
  an; Toolbag ganz neu starten.
- **Der Quick Loader paart ueber den Namensstamm** (`<stamm>_low` / `<stamm>_high`); passt er
  nicht, entsteht eine leere Gruppe und ein leerer Bake (16-KB-PNGs).
- `bake()` aus Python ist nie interaktiv, blockiert den Hauptthread und ist schnell (2048², 20
  Teile unter 1 s); gleiche Eingabe ergibt bytegleiche Ausgabe.
- Texturen haben keinen Reload: leeren und neu setzen. `Texture.path` ist nur lesbar.
- Kein `mset.Timer`, kein `exportModel`; Export heisst `exportSceneFBX` und so weiter.
- Die Doku nennt den Plugin-Ordner `data/plugins`, real ist es `data\plugin`; Nutzer-Plugins
  liegen unter `%LOCALAPPDATA%\Marmoset Toolbag 5\plugins`, nicht unter Dokumente.

## Uebernahmeliste aus `X:\eg\Tools\toolbag-bridge\`

| Quelle | Wird zu | Was raus muss |
|---|---|---|
| `plugin/__main__.py` | `plugin/__main__.py` | Skew- und Fix-AO-Knopf, deutsche Labels; dazu Token, Haken, Dispatch auf `actions` |
| `tb.py` (`_find_toolbag`, `_to_front`, `_listening`, `call`) | `toolbag_mcp/focus.py`, `toolbag_mcp/client.py` | `SystemExit` bei fehlendem Port wird ein Fehlerobjekt |
| `plugin/tbhelp.py` (`overview`, `show`, `isolate`, `cage`, `settings`, `maps`, `bake`, `bake_to`, `export_lows`, `replace_low`, `set_alpha_map`, `backfaces`) | `plugin/actions.py`, Familien 3 und 4 | `fix_ao`, `bake_detail`; deutsche Schluessel in den Antworten werden englisch |
| `LIESMICH.md`, `LIESMICH-api.md` | `docs/pitfalls.md` (englisch) | Projektbezug |

Nicht uebernehmen: `skew.py`, `skew_ui.py`, `kanten_maske.py`, `tbscene.py`, `ao_fix.py`,
`auto_skew.py`, `bake_check.py`, `standalone/`. Vor dem Kopieren jede Datei auf Firmenpfade und
Namen pruefen.

## Meilensteine und Abnahme

- **M0, erledigt am 29.10.2026:** Projekt angelegt (Startpaket, privates Repo), in beiden
  Projektlisten eingetragen, dieser Bauplan hierher gezogen.
- **M1, erledigt am 29.10.2026:** Plugin-Kern, Familien 1 bis 4, Server, Fokus, `install`,
  `doctor`, Registrierung im Benutzer-Umfang. Abnahme bestanden: der Ketten-Ablauf (status,
  Uebersicht, Quick Load, Cage, Bake, Alpha Map als Cutout, Rueckseiten, Low tauschen,
  Probe-Bake, Speichern und Laden) lief ueber die Aktionen an einer neutralen Testszene,
  „Claude Bridge" war aus. Belege und Abweichungen von der Referenz: `pitfalls.md`.
- **M2, erledigt am 29.10.2026:** Familien 5 bis 7 (dazu die generischen Griffe
  `object.attributes`, `object.set_attributes`, `object.create` fuer jedes Objekt), fuenf
  Ressourcen, `tests/fake_mset.py` mit Tests der Aktionen und des Plugin-Kerns ueber HTTP,
  `docs/pitfalls.md`, `doctor` erkennt Doppelinstanzen und fremde Portbelegung. Abnahme an
  Toolbag: Licht, Kamera mit Tiefenschaerfe, Sky laden, Fog, Render-Einstellungen, Bild
  rendern, Timeline.
- **M3, vorbereitet am 29.10.2026:** Wheel mit Plugin (`uv build`, Installation aus dem
  Wheel geprueft), README englisch (Install, Registrierung je Client, Werkzeuge, Sicherheit),
  CHANGELOG, CI (Lint, Tests, Build). Offen bis zur Ansage des Nutzers: LICENSE, Version
  0.1.0, Release privat zu oeffentlich, PyPI (`docs/entwicklung.md`, Release-Ablauf).
- **Danach in Unreal Tools** (`X:\eg\Tools\notes\STATUS-toolbag.md`): Junction „Claude Bridge" raus,
  Skew-Fenster und `fix_ao` als Erweiterung an das neue Plugin oder ueber den freigegebenen
  Python-Weg, `tb.py` als duenner Client oder weg, Skill `ketten-plaettchen-maske` auf die
  MCP-Werkzeuge umschreiben, `toolbag-bridge/LIESMICH.md` und `docs/entscheidungen-toolbag.md`
  nachziehen.

## Lesevorlagen (nicht installieren)

- `guangwenz/marmoset-mcp`: 1 Stern, letzter Commit 08.04.2026, keine LICENSE-Datei, kein
  Schutz, `Access-Control-Allow-Origin: *`, bricht mit `mcp` 2.x. Lesenswert: die 23 Werkzeuge
  (Szene, Lichter, Kamera, Sky, Fog, Render) und der Licht-Trick per Duplikat.
- `dcc-mcp/dcc-mcp-marmoset` (MIT, Push 25.10.2026): Token je Start, 1-MiB-Grenze, kein freier
  Code, 14 feste Werkzeuge; kann weder backen noch Masken setzen noch Meshes tauschen; setzt
  `onPeriodicUpdate` und `onFrameUpdate`. Lesenswert: Installer mit Quittung, Statusfenster.
- Offizielle Beispiele: `C:\Program Files\Marmoset\Toolbag 5\data\plugin\Examples\` (Baking,
  Creating Materials from Images mit den Feldnamen je Subroutine, Creating UIs); teils veraltet
  (`multipleTextureSets` und `edgePadding = "Custom"` gibt es in 5.x nicht mehr).

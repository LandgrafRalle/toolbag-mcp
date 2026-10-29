<!-- Budget 10 KB. Dateikarte, Aufrufe und der Test-Ablauf an Toolbag, fuer den Nutzer und Claude; die oeffentliche Anleitung ist README.md (englisch). Voll? Nach docs/<thema>.md auslagern, nicht anhaengen. -->
# Entwicklung: Dateikarte, Aufrufe, Test-Ablauf

Diese Datei beantwortet **welche Datei was ist, wie man das Projekt startet und wie man es
an Toolbag testet**. Was gerade offen ist, steht in `notes/STATUS.md`; fuer Nutzer ist
`README.md` (englisch) da.

---

## Dateikarte

| Datei | Was sie tut |
|---|---|
| `plugin/__main__.py` | Das Toolbag-Plugin: HTTP-Server im Nebenthread, Warteschlange auf `onPeriodicUpdate`, Fenster mit Port, Python-Haken, Zaehler und Stop; schreibt Port und Token nach `~\.toolbag-mcp\plugin.json`; Szenenwechsel und Beenden zweiphasig |
| `plugin/tbmcp_actions.py` | Die benannten Aktionen (`status`, `scene.*`, `object.*`, `selection.*`, `material.*`, `baker.*`, `light.*`, `sky.*`, `fog.*`, `camera.*`, `render.*`, `timeline.*`, `python.exec`, `toolbag.quit`) mit Parameterpruefung und den eingebauten Schutzregeln; `object.attributes` und `object.set_attributes` greifen jedes Attribut jedes Objekts |
| `toolbag_mcp/server.py` | Der MCP-Server: je Aktion ein Werkzeug mit Typen, Beschreibung, Annotation und Ausgabeschema, fuenf Ressourcen, Transport stdio |
| `toolbag_mcp/client.py` | HTTP-Client zum Plugin; liest Port und Token bei jedem Aufruf aus `plugin.json` |
| `toolbag_mcp/focus.py` | Holt Toolbag fuer die Dauer eines Aufrufs nach vorn und gibt den Fokus zurueck (Windows) |
| `toolbag_mcp/install.py` | Junction oder Kopie des Plugins in Toolbags Plugin-Ordner, `verify`, Suche nach fremden Plugins mit Update-Callback; findet das Plugin im Checkout und im installierten Wheel |
| `toolbag_mcp/cli.py` | Kommando `toolbag-mcp`: `serve` (Standard), `install`, `uninstall`, `verify`, `doctor`, `call`, `actions`, `register` |
| `tests/` | pytest ohne Toolbag: `fake_mset.py` baut `mset` nach (mit den belegten Fallen aus 5.02), `test_actions.py` prueft die Aktionen dagegen, `test_plugin_main.py` den Plugin-Kern ueber echtes HTTP, `test_server.py` die MCP-Schicht in-process, `test_registry.py` Katalog und Referenzen; `make_test_assets.py` erzeugt mit Blender die neutralen Testdaten |
| `docs/bauplan.md` | Zielbild, Architektur, Werkzeugfamilien, Meilensteine mit Abnahme |
| `docs/pitfalls.md` | Belegte Fallen der Toolbag-API und des Plugin-Betriebs (englisch) |
| `CHANGELOG.md` | Was je Version dazukam (englisch, Keep a Changelog) |
| `.github/workflows/ci.yml` | CI bei jedem Push: `uv sync --locked`, Ruff, pytest, `uv build` auf Windows und Ubuntu mit Python 3.10 und 3.12 |
| `pyproject.toml`, `uv.lock` | Paket (hatchling; `plugin/` wandert als `toolbag_mcp/plugin` ins Wheel), Abhaengigkeit `mcp` 2.x, Dev-Werkzeuge pytest und ruff |

---

## Starten und bauen

| Ich will | Aufruf |
|---|---|
| Abhaengigkeiten einrichten | `uv sync` |
| Plugin in Toolbag einhaengen | `uv run toolbag-mcp install`, danach in Toolbag *Edit, Plugins, Refresh* und *Plugins, Toolbag MCP* |
| Plugin ohne Menue starten | `"C:\Program Files\Marmoset\Toolbag 5\toolbag.exe" "C:\Dev\toolbag-mcp\plugin\__main__.py"` |
| Alles pruefen | `uv run toolbag-mcp doctor` |
| Eine Aktion direkt aufrufen | `uv run toolbag-mcp call scene.info`, `uv run toolbag-mcp call baker.bake_to "{\"path\": \"C:/tmp/t.png\"}"` |
| Aktionen mit Parametern auflisten | `uv run toolbag-mcp actions` |
| Den MCP-Server fuer einen Client | `uv run toolbag-mcp` (stdio); Registrierung: `uv run toolbag-mcp register` |
| Tests und Lint | `uv run pytest`, `uv run ruff check .` |
| Paket bauen und aus dem Wheel pruefen | `uv build`, dann in einer Wegwerf-Umgebung `uv pip install dist\*.whl` und `toolbag-mcp install --plugins-dir <ordner>` |
| Testdaten neu erzeugen | `blender --background --factory-startup --python tests/make_test_assets.py -- <ordner>` |

---

## Test-Ablauf an Toolbag

1. Keine Toolbag-Instanz offen (`tasklist`): die zweite kann den Port nicht binden und
   meldet das nur in ihrem Fenster.
2. Toolbag mit dem Plugin als Skriptargument starten (Tabelle oben). Nach etwa drei Sekunden
   liegt `~\.toolbag-mcp\plugin.json`. Geaenderter Plugin-Code gilt erst nach einem Neustart
   von Toolbag, das Modul bleibt sonst geladen.
3. `uv run toolbag-mcp doctor`, dann die Aufrufe mit `call`. Jeder Aufruf holt Toolbag nach
   vorn und gibt den Fokus danach zurueck. Testdaten liegen in `~\.toolbag-mcp\test\`
   (neutral, aus `tests/make_test_assets.py`), Bake- und Render-Ausgaben darunter.
4. Beenden mit `uv run toolbag-mcp call toolbag.quit`; Toolbag schliesst ohne Nachfrage und
   raeumt `plugin.json` weg.

⚠️ Aus der Claude-Desktop-App heraus ist `%LOCALAPPDATA%` umgeleitet (Gedaechtnis `C:\Dev`,
`localappdata-ist-fuer-claude-virtualisiert`): `install` und jeder Blick in Toolbags
Plugin-Ordner oder Log gehen dann ueber einen WMI-Prozess von aussen, Testdaten liegen darum
unter `~\.toolbag-mcp\test\` und nicht unter `%TEMP%`.

⚠️ Nie zusammen mit „Claude Bridge" oder „Skew Masks" starten; `doctor` nennt sie. Nie eine
Szene aus fremden Projekten als Testszene speichern, immer eine Kopie.

---

## Voraussetzungen

- Marmoset Toolbag 5.0x, geprueft mit 5.0.2.2; 4.0x und macOS ungetestet.
- Python 3.10 oder neuer mit `uv` fuer den Server. Das Plugin laeuft in Toolbags eigenem
  Python 3.12 ohne Zusatzpakete.
- Toolbags Fenster darf waehrend eines Aufrufs nicht hinter einem modalen Dialog stecken.

---

## Was ausserhalb dieses Repos liegt

| Ort | Was | Warum dort |
|---|---|---|
| `%LOCALAPPDATA%\Marmoset Toolbag 5\plugins\Toolbag MCP` | Junction auf `plugin/`, angelegt von `toolbag-mcp install` | Toolbag laedt Nutzer-Plugins nur von dort |
| `~\.toolbag-mcp\plugin.json` | Port, Token und Prozess-ID des laufenden Plugins; verschwindet beim Stop | nie unter `%LOCALAPPDATA%`, Store-Apps sehen dort eine umgeleitete Kopie |
| `~\.toolbag-mcp\config.json` | optional `{"port": N}` fuer einen anderen Port (`install --port N`) | gleicher Grund |
| `~\.toolbag-mcp\test\` | Testdaten der Abnahme: `part_bake.fbx`, `part_low_v2.fbx`, `mask.png`, `test_scene.tbscene`, Bake- und Render-Ausgaben | neutrale Daten, nicht im Repo |
| `~/.claude.json` | Registrierung `toolbag` fuer Claude Code (Benutzer-Umfang) | Konfiguration des Clients, nicht des Projekts |

---

## Release-Ablauf (sobald Lizenz und Autorenname entschieden sind)

1. `LICENSE` anlegen, `license` und `authors` in `pyproject.toml` eintragen, README-Abschnitt
   „License" ersetzen.
2. Version in `pyproject.toml` und `toolbag_mcp/__init__.py` auf `0.1.0`, CHANGELOG-Abschnitt
   „Unreleased" nach `[0.1.0] - Datum` umhaengen.
3. `uv run pytest`, `uv build`, Commit, Tag `v0.1.0`, Push, GitHub-Release mit den Dateien aus
   `dist/`.
4. Repo oeffentlich schalten: vorher die deutschen internen Dateien (`CLAUDE.md`,
   `docs/bauplan.md`, `notes/STATUS.md`, diese Datei) auf Firmenpfade pruefen und entscheiden,
   ob sie bleiben.
5. Optional `uv publish` (PyPI-Konto noetig); danach `register` und README auf
   `uvx toolbag-mcp` ohne Git-Zusatz.

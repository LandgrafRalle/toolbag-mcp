<!-- Budget 10 KB. Voll? Dann zieht ein Kapitel nach docs/<thema>.md um und hier bleibt
     eine Zeile mit Verweis zurueck, samt Ausloeser, WANN man sie liest. -->
# Toolbag-MCP

MCP-Server fuer Marmoset Toolbag 5. Ein kleines Plugin in Toolbag nimmt Auftraege ueber einen
lokalen Port an und fuehrt sie auf Toolbags Hauptthread aus; ein Python-Server aussen bietet
sie einem MCP-Client (Claude Code, Claude Desktop, Cursor) als typisierte Werkzeuge an: Szene
laden, untersuchen und exportieren, Objekte und Auswahl, Materialien samt Texturen und
Cutout-Masken, Bake-Projekte anlegen, einstellen und backen, Toolbag beenden. Freier
Python-Code laeuft nur, wenn der Nutzer es im Plugin-Fenster erlaubt.

Diese Datei beantwortet **welche Datei was ist und wie man das Projekt startet**.
Was gerade offen ist, steht in `notes/STATUS.md`. Zum ersten Release (Meilenstein M3) wird
sie englisch und zur Anleitung fuer fremde Nutzer.

**Stand:** Meilenstein M1 (29.10.2026): Plugin, Server, die Werkzeugfamilien Diagnose,
Szene, Materialien und Baker (61 Werkzeuge, 3 Ressourcen), Installer und `doctor`. Abnahme
am Ketten-Ablauf an einer Testszene bestanden. Was als Naechstes kommt: `docs/bauplan.md`,
Abschnitt Meilensteine.

---

## Dateikarte

| Datei | Was sie tut |
|---|---|
| `plugin/__main__.py` | Das Toolbag-Plugin: HTTP-Server im Nebenthread, Warteschlange auf `onPeriodicUpdate`, Fenster mit Port, Python-Haken, Zaehler und Stop; schreibt Port und Token nach `~\.toolbag-mcp\plugin.json` |
| `plugin/tbmcp_actions.py` | Die benannten Aktionen (`status`, `scene.*`, `object.*`, `selection.*`, `material.*`, `baker.*`, `python.exec`, `toolbag.quit`) mit Parameterpruefung und den eingebauten Schutzregeln |
| `toolbag_mcp/server.py` | Der MCP-Server: je Aktion ein Werkzeug mit Typen, Beschreibung und Annotation, drei Ressourcen, Transport stdio |
| `toolbag_mcp/client.py` | HTTP-Client zum Plugin; liest Port und Token bei jedem Aufruf aus `plugin.json` |
| `toolbag_mcp/focus.py` | Holt Toolbag fuer die Dauer eines Aufrufs nach vorn und gibt den Fokus zurueck (Windows) |
| `toolbag_mcp/install.py` | Junction oder Kopie des Plugins in Toolbags Plugin-Ordner, `verify`, Suche nach fremden Plugins mit Update-Callback |
| `toolbag_mcp/cli.py` | Kommando `toolbag-mcp`: `serve` (Standard), `install`, `uninstall`, `verify`, `doctor`, `call`, `actions`, `register` |
| `tests/` | pytest gegen ein nachgebautes `mset` (Registry, Referenzen, Schutzregeln, Map-Aliasse, zweiphasige Szenen-Operationen); `make_test_assets.py` erzeugt mit Blender die neutralen Testdaten |
| `docs/bauplan.md` | Zielbild, Architektur, Werkzeugfamilien, Meilensteine mit Abnahme |
| `docs/pitfalls.md` | Belegte Fallen der Toolbag-API und des Plugin-Betriebs (englisch); lesen vor jedem Code gegen `mset` |
| `pyproject.toml`, `uv.lock` | Paket (hatchling, `plugin/` wandert mit ins Wheel), Abhaengigkeit `mcp` 2.x, Dev-Werkzeuge pytest und ruff |

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
| Den MCP-Server fuer einen Client | `uv run toolbag-mcp` (stdio); wie man ihn registriert: `uv run toolbag-mcp register` |
| Tests und Lint | `uv run pytest`, `uv run ruff check .` |
| Testdaten neu erzeugen | `blender --background --factory-startup --python tests/make_test_assets.py -- <ordner>` |

⚠️ Nie zwei Toolbag-Instanzen mit dem Plugin laufen lassen: die zweite kann den Port nicht
binden und meldet das im Fenster. Beenden ueber das Werkzeug `toolbag_quit` oder den Knopf
*Stop*. Nie zusammen mit einem anderen Plugin starten, das `onPeriodicUpdate` setzt;
`doctor` nennt die installierten.

⚠️ `install` aus einer normalen Shell ausfuehren. Aus einer Store-App heraus (etwa der
Claude-Desktop-App) landet die Junction in deren umgeleiteter Kopie von `%LOCALAPPDATA%`,
und Toolbag sieht sie nie (`docs/pitfalls.md`).

---

## Voraussetzungen

- Marmoset Toolbag 5.0x, geprueft mit 5.0.2.2; 4.0x nach Moeglichkeit, ungetestet; macOS
  ungetestet (die Fokus-Handhabung ist Windows-only, der Rest sollte laufen).
- Python 3.10 oder neuer mit `uv` fuer den Server. Das Plugin laeuft in Toolbags eigenem
  Python 3.12 und braucht keine Zusatzpakete.
- Toolbags Fenster darf waehrend eines Aufrufs nicht hinter einem modalen Dialog stecken.

---

## Was ausserhalb dieses Repos liegt

| Ort | Was | Warum dort |
|---|---|---|
| `%LOCALAPPDATA%\Marmoset Toolbag 5\plugins\Toolbag MCP` | Junction auf `plugin/`, angelegt von `toolbag-mcp install` | Toolbag laedt Nutzer-Plugins nur von dort |
| `~\.toolbag-mcp\plugin.json` | Port, Token und Prozess-ID des laufenden Plugins; verschwindet beim Stop | nie unter `%LOCALAPPDATA%`, Store-Apps sehen dort eine umgeleitete Kopie |
| `~\.toolbag-mcp\config.json` | optional `{"port": N}` fuer einen anderen Port (`install --port N`) | gleicher Grund |
| `~\.toolbag-mcp\test\` | Testdaten der Abnahme: `part_bake.fbx`, `part_low_v2.fbx`, `mask.png`, `test_scene.tbscene`, Bake-Ausgaben | neutrale Daten, erzeugt mit `tests/make_test_assets.py`; nicht im Repo |

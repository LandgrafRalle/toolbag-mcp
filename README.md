<!-- Budget 10 KB. Voll? Dann zieht ein Kapitel nach docs/<thema>.md um und hier bleibt
     eine Zeile mit Verweis zurueck, samt Ausloeser, WANN man sie liest. -->
# Toolbag-MCP

MCP-Server fuer Marmoset Toolbag 5. Ein kleines Plugin in Toolbag nimmt Auftraege ueber einen
lokalen Port an und fuehrt sie auf Toolbags Hauptthread aus; ein Python-Server aussen bietet
sie einem MCP-Client (Claude Code, Claude Desktop, Cursor) als typisierte Werkzeuge an: Szene
laden und untersuchen, Materialien setzen, Bake-Projekte bauen und backen, Licht und Kamera,
Rendern. Freier Python-Code laeuft nur, wenn der Nutzer es im Plugin-Fenster erlaubt.

Diese Datei beantwortet **welche Datei was ist und wie man das Projekt startet**.
Was gerade offen ist, steht in `notes/STATUS.md`. Zum ersten Release (Meilenstein M3) wird
sie englisch und zur Anleitung fuer fremde Nutzer.

**Stand:** Meilenstein M0 (29.10.2026), Projekt angelegt, noch kein Code. Was gebaut wird und
in welcher Reihenfolge: `docs/bauplan.md`.

---

## Dateikarte

| Datei | Was sie tut |
|---|---|
| `CLAUDE.md` | Wegweiser und Randbedingungen fuer Claude |
| `docs/bauplan.md` | Zielbild, Architektur, Werkzeugfamilien, Fallen, Meilensteine mit Abnahme |
| `notes/STATUS.md` | Offene Punkte |
| `plugin/` | kommt mit M1: das Toolbag-Plugin, laeuft in Toolbag, nur Standardbibliothek |
| `toolbag_mcp/` | kommt mit M1: der MCP-Server und das Kommando `toolbag-mcp` |

---

## Starten und bauen

Noch nichts startbar. Die geplanten Aufrufe (`uvx toolbag-mcp`, `toolbag-mcp install`,
`toolbag-mcp doctor`) stehen im Bauplan und kommen mit M1 hierher.

---

## Voraussetzungen

- Marmoset Toolbag 5.0x (lokal 5.0.2.2); 4.0x nach Moeglichkeit, macOS ungetestet.
- Python 3.12 oder neuer mit `uv` fuer den Server. Das Plugin laeuft in Toolbags eigenem
  Python 3.12, das weder pip noch numpy hat.
- Windows fuer die Fokus-Handhabung: Toolbag arbeitet Auftraege nur ab, solange sein Fenster
  vorne ist.

---

## Was ausserhalb dieses Repos liegt

| Ort | Was | Warum dort |
|---|---|---|
| `X:\eg\Tools\toolbag-bridge\` | die private Vorgaenger-Bruecke: Vorlage fuer Plugin, Fokus-Handhabung und Baker-Griffe | gehoert zu Unreal Tools und traegt Firmenkontext; wird nur gelesen, Uebernahmeliste im Bauplan |
| `%LOCALAPPDATA%\Marmoset Toolbag 5\plugins\` | Toolbags Plugin-Ordner, Ziel von `toolbag-mcp install` | Toolbag laedt Nutzer-Plugins nur von dort |
| `~\.toolbag-mcp\` | Token des laufenden Plugins | nie unter `%LOCALAPPDATA%`, Grund im Bauplan (Abschnitt Plugin) |

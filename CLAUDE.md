<!-- Budget 4 KB. Voll? Dann hat ein Thema eine eigene Datei unter docs/ verdient,
     auslagern, nicht kuerzen. Randbedingungen bleiben IMMER stehen, auch wenn sie das
     Budget reissen (DOKU-STANDARD.md, Regel 7: Sicherheit schlaegt Budget). -->
# Toolbag-MCP

MCP-Server fuer **Marmoset Toolbag 5**: ein Plugin in Toolbag nimmt Auftraege an, ein
Python-Server aussen bietet sie MCP-Clients als 90 feste Werkzeuge an (Szene, Materialien,
Baker, Licht, Kamera, Render). Freier Python-Code nur mit Haken im Plugin-Fenster.
Nachfolger der privaten Bruecke in Unreal Tools. Soll **oeffentlich auf GitHub** erscheinen,
ist noch privat. Stand 29.10.2026: v0.1.0 getaggt und als GitHub-Release angelegt (MIT,
Autor LandgrafRalle); offen sind der Zeitpunkt der Veroeffentlichung und PyPI.

## Wo steht was

Diese Datei ist nur der Wegweiser, hier wird kein Inhalt wiederholt.

| Quelle | Zustaendig fuer |
|---|---|
| `docs/bauplan.md` | Zielbild, Architektur, Werkzeugfamilien, Meilensteine mit Abnahme. **Lesen, bevor Code entsteht oder ein Werkzeug dazukommt** |
| `docs/pitfalls.md` | Belegte Fallen der Toolbag-API und des Plugin-Betriebs (englisch). **Lesen, bevor Code gegen `mset` entsteht oder ein Toolbag-Verhalten raetselhaft ist** |
| `docs/entwicklung.md` | Dateikarte, Aufrufe, Test-Ablauf an Toolbag, Release-Ablauf, was ausserhalb des Repos liegt. **Lesen, bevor Toolbag zum Testen gestartet wird** |
| `README.md` | Englische Anleitung fuer Nutzer (Install, Registrierung, Werkzeuge, Sicherheit); geht mit dem Paket hinaus |
| `notes/STATUS.md` | Was gerade **offen** ist, vor der Arbeit lesen. Erledigtes wird geloescht, die Historie steht in Git |
| `CHANGELOG.md` | Was je Version dazukam; beim Release den Abschnitt „Unreleased" umhaengen |
| Projektgedaechtnis | Vorlieben des Nutzers; nichts Offenes, keine Entscheidung zum Repo (die gehoert nach `docs/`) |
| `~/.claude/DOKU-STANDARD.md` | Wie hier dokumentiert wird. Lesen, bevor eine Doku-Datei angelegt, aufgeteilt oder umgebaut wird |

## Randbedingungen

- **Wird oeffentlich.** Nichts aus dem Firmenkontext ins Repo: keine Goliath-Namen, keine
  `X:\GameAssets`-Pfade, keine Skew-Masken, kein `fix_ao`; uebernommene Dateien vorher
  pruefen. Name ohne „Marmoset" (Marke von Marmoset LLC).
- **Lizenz MIT, Copyright-Inhaber LandgrafRalle** (entschieden 29.10.2026), kein Klarname
  im Repo. Oeffentlich schalten und PyPI nur auf Ansage des Nutzers.
- **Nur ein Plugin darf `onPeriodicUpdate` setzen:** nie zusammen mit „Claude Bridge" oder
  „Skew Masks" starten. Die Bruecke in Unreal Tools bleibt, bis der Unreal-Tools-Chat umsteigt.
- **Freier Python-Code nur mit Freigabe des Nutzers:** der Haken ist standardmaessig aus und
  nicht persistent; der Server darf ihn nie umgehen.
- **Testszenen sind Kopien,** nie in `X:\GameAssets` speichern. Testdaten liegen in
  `~\.toolbag-mcp\test\`, nicht unter `%TEMP%` (AppData ist fuer die Claude-App umgeleitet).
- **Toolbag zum Testen:** eine Instanz, mit dem Plugin als Skriptargument gestartet, mit
  `toolbag.quit` beendet; `install` und alles unter `%LOCALAPPDATA%` nur von aussen (WMI).
  Ablauf: `docs/entwicklung.md`.

## Doku-Pflege

Neues Wissen wird nach `~/.claude/DOKU-STANDARD.md` einsortiert, nicht hier angehaengt.
Struktur-Fragen ueber dieses Projekt hinaus gehoeren in einen Chat aus `C:\Dev`.

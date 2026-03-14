# Contributing

## Branch-Strategie

| Branch | Zweck |
|---|---|
| `main` | Stabiler, deploybarer Stand. Direkte Commits **nicht erlaubt**. |
| `feat/<thema>` | Neue Features (z.B. `feat/zone-selection`) |
| `fix/<thema>` | Bugfixes (z.B. `fix/picker-clickable`) |
| `chore/<thema>` | Wartung, Abhängigkeiten, CI (z.B. `chore/update-deps`) |
| `docs/<thema>` | Reine Dokumentationsänderungen |

## Workflow

```
main
 └── feat/mein-feature   ← branch off main
      └── Pull Request → main
```

1. Von `main` abzweigen: `git checkout -b feat/mein-feature`
2. Änderungen committen (siehe Commit-Format)
3. Pull Request gegen `main` öffnen
4. Mindestens einen Review abwarten
5. Squash-Merge in `main`

## Commit-Format

```
<typ>: <kurze Beschreibung im Imperativ>

[optionaler Body]
```

| Typ | Wann |
|---|---|
| `feat` | Neue Funktionalität |
| `fix` | Bugfix |
| `chore` | Wartung, Deps, Build |
| `docs` | Nur Dokumentation |
| `test` | Tests hinzufügen oder korrigieren |
| `refactor` | Kein Feature, kein Bug |

**Beispiele:**
```
feat: add zone selection to booking screen
fix: handle duplicate plate error on second cycle
chore: bump opencv to 4.9
```

## Pull Request Checkliste

- [ ] Branch von aktuellem `main` abgezweigt
- [ ] Titel folgt dem Commit-Format (`typ: beschreibung`)
- [ ] `docker compose up` + manueller Test auf dem Emulator
- [ ] `python3 -m pytest tests/ -v` läuft durch
- [ ] Keine Secrets, `.env`-Dateien oder APKs im Commit

## Was nicht in `main` gehört

- Direkte Commits (außer initiale README/Setup-Commits)
- Secrets oder Credentials irgendwo in der History
- APK-Dateien (`apk/` ist gitignored — so lassen)
- Ungetestete Änderungen an `_run_cycle` oder der ADB-Automation

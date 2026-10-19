# Préparation du vrai web Digger — aucune bascule effectuée

Base main vérifiée : `f63cea5de7109b1a52fe879b0a24109e3da6210f`
(merge de la PR #2, même arbre applicatif que `48dc6b6`). Les ajouts de démarrage
sont sur `feature/digger-web-publication`, sans modification de main ni push.

## Chemins et données conservés

- Worktree préparé : `/home/jean/digger/instance/releases/digger-web`.
- Entrée WSGI : `digger_web:application`.
- Interpréteur existant : `/home/jean/digger/instance/demo/venv/bin/python`.
- DB existante : `/home/jean/digger/instance/demo/digger.sqlite`.
- Médias existants : `/home/jean/digger/instance/demo/storage/{tracks,meta}`.
- Signature/cookie existants conservés ; aucun secret copié dans cette release.
- Le dépôt initial et ses modifications restent intacts.

L'inventaire de schéma a été lu avec SQLite `mode=ro&immutable=1`, sans WAL/journal
non vide. Les migrations 001/002/003 et leurs checksums correspondent à main :
**aucune migration requise pour cette DB**. La DB historique `instance/liquid.db`
n'existe pas ici ; ne pas passer directement à `app:app` avec `config.py`.

Le bootstrap n'initialise/migre aucune DB et refuse un registre incomplet.
Le mode démo historique conserve ses restrictions et son comportement par défaut.
La vraie entrée utilise les templates normaux, laisse passer OAuth et garde la
radio et les covers publiques fermées dans Flask comme dans Nginx. Les previews
MP3/cover des contributeurs et administrateurs restent privées et accessibles.
Aucun worker n'est lancé, aucun document juridique n'est activé.

## Prérequis OAuth à résoudre avant bascule

Le fichier `/home/jean/digger/.env` n'existe pas à l'inspection (contenu non lu).
Aucune valeur de secret ni environnement du manager systemd n'a été inspecté.
La disponibilité de GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET pour le service reste
**non vérifiée**. La nouvelle entrée refuse de démarrer s'ils sont absents.

Nico doit confirmer leur fourniture par l'environnement du service ou la
configuration opérateur prévue, ainsi que l'URI de redirection autorisée
`https://dig.hqwi.re/auth/callback`. Ne pas mettre de credentials dans Git,
une commande partagée, le descriptif de PR ou les journaux.
Le fichier de configuration n'est jamais créé/modifié par notre code.
Les paramètres YouTube/Spotify sont conservés s'ils sont fournis ; Spotify reste
désactivé par défaut. Aucun appel fournisseur réel n'a été fait dans les tests.

## Unité proposée, non installée

`digger-demo.service` est une copie préparée de l'unité utilisateur actuelle avec
uniquement le nouveau WorkingDirectory, l'entrée Gunicorn et les chemins runtime.
Le diff exact est dans `digger-demo.service.diff` ; l'unité initiale sans secrets
est conservée dans `digger-demo.service.before` pour la procédure de retour.
Le service actuel reste actif sur son ancienne entrée ; aucun daemon-reload ni
restart n'a été fait. Aucun changement Nginx n'est nécessaire.

## Procédure future, après validation de la configuration OAuth

Ces commandes sont des instructions pour l'opérateur, elles n'ont pas été exécutées.

```bash
cd /home/jean/digger/instance/releases/digger-web
install -m 0644 publication/digger-demo.service /home/jean/.config/systemd/user/digger-demo.service
systemctl --user daemon-reload
systemctl --user restart digger-demo.service
systemctl --user is-active digger-demo.service
systemctl --user show digger-demo.service -p WorkingDirectory -p ExecStart -p MainPID
ss -ltn | rg '127\.0\.0\.1:17000'
```

Attendus HTTPS après démarrage : accueil 200 avec assets Digger et lecture
fermée ; `/login/google` 302 vers Google (sans afficher URL/tokens dans les logs),
`/submissions` et `/admin` 401 sans session ; `/hls/stream.m3u8` et
`/media/cover/1` 404. Vérifier dans Safari connexion et callback, propositions
YouTube/Spotify et saisie manuelle, décision admin, upload MP3+cover et previews
privées. Confirmer le blocage juridique final. Ne pas redémarrer Linsta/nicee.

Retour opérateur si nécessaire, sans réinitialisation DB :

```bash
install -m 0644 publication/digger-demo.service.before /home/jean/.config/systemd/user/digger-demo.service
systemctl --user daemon-reload
systemctl --user restart digger-demo.service
```

## Tests isolés

Résultat : 416 tests collectés, 415 réussis et 1 HTTP privé opt-in ignoré en
55,413 s. Les 6 tests de publication ont ensuite été réexécutés avec succès,
notamment pour confirmer le callback HTTPS réel derrière les headers Nginx.
Les tests Node, le contrôle de syntaxe JavaScript, `git diff --check` et
`systemd-analyze --user verify publication/digger-demo.service` passent.

Utiliser `PYTHONPATH=.:tests` et le Python existant avec `-B`.
Les tests de publication emploient une DB, des médias et des paramètres factices
temporaires : vrai template, callback HTTPS derrière proxy, upload privé, revue,
blocage légal, fermeture HLS/covers et refus de démarrer sans OAuth.
Les tests Node couvrent aussi l'absence de toute connexion HLS quand le direct
est désactivé. Aucune validation OAuth réelle ou endurance audio n'est revendiquée.

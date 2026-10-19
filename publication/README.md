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
La vraie entrée utilise les templates normaux, permet le login local et garde la
radio et les covers publiques fermées dans Flask comme dans Nginx. Les previews
MP3/cover des contributeurs et administrateurs restent privées et accessibles.
Aucun worker n'est lancé, aucun document juridique n'est activé.

## Preview avec comptes locaux, OAuth facultatif

La preview démarre sans Google. L'unité préparée impose
`DIGGER_GOOGLE_OAUTH_ENABLED=0`. Aucun `.env` ni credential Google générique
n'est chargé : les paramètres d'un autre service ne sont jamais réutilisés.
Le futur OAuth Digger sera séparé, créé par l'exploitant avec le compte dev Digger.
Il nécessitera une activation explicite et `DIGGER_GOOGLE_CLIENT_ID` /
`DIGGER_GOOGLE_CLIENT_SECRET` dédiés. Sans configuration complète, la preview
reste disponible, le bouton Google est masqué et les routes OAuth répondent 503.

`/login/local` utilise le POST JSON `/login` existant, avec CSRF et les contrôles
de compte vérifié/rôle. Les inscriptions publiques restent désactivées ; les
comptes de test sont créés par l'opérateur. Le hash utilise
`werkzeug.security.generate_password_hash`, vérifié par `check_password_hash`.
Aucun mot de passe de test n'est consigné dans Git ou dans ce document.
Les paramètres YouTube/Spotify peuvent être fournis séparément ; Spotify est
désactivé par défaut et la saisie manuelle demeure disponible.

Compte de test créé sur demande : `nico-test@example.invalid`, nom `nico-test`,
rôle `contributor`, compte vérifié, aucun lien Google et aucun privilège admin.
Une sauvegarde SQLite a précédé l'unique INSERT dans `users` ; aucun compte
existant n'a été modifié. Seul un hash Werkzeug est enregistré en DB. Le nouveau
mot de passe a été remis dans un fichier privé 0600 hors Git, sans affichage.
Cette création de compte n'est pas une migration de schéma.

## Unité proposée, non installée

`digger-demo.service` est une copie préparée de l'unité utilisateur actuelle avec
uniquement le nouveau WorkingDirectory, l'entrée Gunicorn et les chemins runtime.
Le diff exact est dans `digger-demo.service.diff` ; l'unité initiale sans secrets
est conservée dans `digger-demo.service.before` pour la procédure de retour.
Le service actuel reste actif sur son ancienne entrée ; aucun daemon-reload ni
restart n'a été fait. Aucun changement Nginx n'est nécessaire.

## Procédure future, après validation opérateur de la preview

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
fermée ; `/login/local` 200 ; `/login/google` et `/auth/callback` 503,
`/submissions` et `/admin` 401 sans session ; `/hls/stream.m3u8` et
`/media/cover/1` 404. Vérifier dans Safari connexion locale, propositions
YouTube/Spotify et saisie manuelle, décision admin, upload MP3+cover et previews
privées. Confirmer le blocage juridique final. Ne pas redémarrer Linsta/nicee.

Retour opérateur si nécessaire, sans réinitialisation DB :

```bash
install -m 0644 publication/digger-demo.service.before /home/jean/.config/systemd/user/digger-demo.service
systemctl --user daemon-reload
systemctl --user restart digger-demo.service
```

## Tests isolés

Résultat après adaptation aux comptes locaux : 420 tests collectés,
419 réussis et 1 HTTP privé opt-in ignoré en 54,130 s. Les 51 tests ciblés
(publication, sécurité Flask et démo historique) avaient également réussi.
Les tests Node, le contrôle de syntaxe JavaScript, `git diff --check` et
`systemd-analyze --user verify publication/digger-demo.service` passent.

Utiliser `PYTHONPATH=.:tests` et le Python existant avec `-B`.
Les tests de publication emploient une DB, des médias et des paramètres factices
temporaires : vrai template, login local, comportement OAuth optionnel, upload privé, revue,
blocage légal, fermeture HLS/covers et démarrage normal sans OAuth.
Les tests Node couvrent aussi l'absence de toute connexion HLS quand le direct
est désactivé. Aucune validation OAuth réelle ou endurance audio n'est revendiquée.

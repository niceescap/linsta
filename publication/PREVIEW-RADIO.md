# Raccordement radio PREVIEW — préparé, non activé

Base : `96490a7` (web publié), branche `feature/digger-preview-radio`.
Release isolée : `/home/jean/digger/instance/releases/digger-preview`.
Aucune modification du code de la release active `digger-web`, de Nginx,
des unités installées, des DB réelles ou des fichiers radio existants.
Aucun démarrage de la radio réelle, aucune clé privée/access.key consultée.

## Catalogue existant et séparation

Racine explicitement utilisée par le web et l'unité radio :
`/home/jean/digger/instance/demo/private-radio`.
SQLite `catalogue.sqlite` reste la source de vérité, distincte de la DB web
`/home/jean/digger/instance/demo/digger.sqlite`. Aucun import, migration ou
changement de statut juridique. Les pages web ouvrent le catalogue en `mode=ro`.

L'admin liste toutes les lignes `private_tracks`, avec statut actif/désactivé/
retiré et mention d'essai privé. Les covers ID3 JPEG/PNG sont décodées et servies
sans recopier de fichier sur disque ; les titres sans cover utilisent le logo.
Les MP3 restent inchangés. Les accès médias utilisent taille/SHA-256 et fichiers
réguliers sans symlink, via `checked_open` existant. Les MP3 individuels restent
réservés aux admins ; les covers et le flux preview exigent une session vérifiée.

## Routes ajoutées (cookies de session existants)

| Méthode | Route | Protection |
| --- | --- | --- |
| GET | `/admin/preview-radio` | Admin vérifié |
| POST | `/admin/preview-radio/start` | Admin vérifié + CSRF |
| POST | `/admin/preview-radio/stop` | Admin vérifié + CSRF |
| GET/HEAD | `/preview-radio/tracks/<id>/audio` | Admin vérifié, plages d'octets |
| GET/HEAD | `/preview-radio/tracks/<id>/cover` | Compte vérifié |
| GET/HEAD | `/preview-radio/hls/stream.m3u8` | Compte vérifié + session moteur saine |
| GET/HEAD | `/preview-radio/hls/seg_<numéro>.ts` | Idem, segment référencé |
| GET/HEAD | `/preview-radio/now-playing` | Compte vérifié |

Réponses privées no-store, Vary Cookie, nosniff. Aucun téléchargement DB/clé,
aucun chemin libre ou unité systemd fourni par une requête. Les anciennes routes
`/hls/`, `/media/cover/` et `/now-playing` gardent leurs restrictions publiques.
Google reste facultatif/désactivé, le login local et le verrou juridique final
sont conservés. Aucun compte ni rôle n'est créé ou modifié.

## Moteur et protections

`preview_radio_runner` réutilise `catalogue_lock`, `tracks`, `load_worker` et
`play_loop`, avec `Engine(continuous=True)` existant. Processus indépendant de
Flask : aucun nouveau port, aucun serveur HTTP privé à exposer. Le verrou exclut
un second moteur et les écritures administratives privées pendant l'écoute.
Le runner ne lit pas access.key ; l'ancien laboratoire SSH reste inchangé.

Seule l'unité fixe `digger-preview-radio.service` peut être démarrée/arrêtée par
les actions web. Les appels systemctl utilisent une liste d'arguments, sans shell,
avec délais bornés. systemd sérialise les start concurrents d'une même unité ; le
verrou reste une seconde protection contre un autre moteur lancé manuellement.

Session bornée à 3600 secondes (moteur et RuntimeMaxSec), Restart=no, FFmpeg à un
thread comme dans le mode privé actuel, limites mémoire/CPU/tâches, arrêt SIGTERM
avec fermeture des enfants et journal private_plays. Aucun démarrage automatique
au boot : l'unité ne doit pas être enable pour cette preview.

Un marqueur `preview-run.json` est créé uniquement lors d'un vrai lancement
ultérieur, sous le verrou ; il contient l'InvocationID systemd et l'heure du run.
Le web exige une unité active, le même InvocationID, un marqueur running, une
playlist et des segments de cette session avec progression récente (<20 s).
Les fichiers du 16 octobre, d'une session précédente ou d'un moteur arrêté ne
peuvent donc pas être proposés comme un direct actif. Après arrêt, les fichiers
peuvent rester sur disque mais sont refusés par les routes. Le marqueur est passé
à stopped dans le finally. Les playlists utilisent les séquences epoch_us déjà
présentes dans le moteur ; aucune modification du worker HLS historique.

Sur l'accueil : PREVIEW / TEST visible, visiteur invité à se connecter. Après
connexion, le bouton est disponible ; il n'ouvre le HLS que sur un geste utilisateur
et quand l'état privé indique une radio prête. Safari utilise HLS natif et les
cookies du même domaine. L'arrêt ou la perte d'autorisation arrête le player et
annule sa reprise. Il peut rester un délai lié aux requêtes en cours/poll 5 s ;
la fermeture n'est pas présentée comme une purge instantanée de tous les buffers.

## Activation future — commandes NON exécutées

Les unités sont seulement préparées et vérifiées statiquement. Après validation :

```bash
cd /home/jean/digger/instance/releases/digger-preview
install -m 0644 publication/digger-preview-radio.service /home/jean/.config/systemd/user/digger-preview-radio.service
install -m 0644 publication/digger-demo.service /home/jean/.config/systemd/user/digger-demo.service
systemctl --user daemon-reload
systemctl --user restart digger-demo.service
systemctl --user is-active digger-demo.service
```

Cette séquence charge l'unité radio mais NE la démarre PAS. Le web reste sur
127.0.0.1:17000, avec les mêmes DB/stockage/session. Nginx reste inchangé :
`/preview-radio/` emprunte le proxy existant, sans ouvrir `/hls/` public.

Ensuite, connecté avec le compte admin existant, aller sur
`https://dig.hqwi.re/admin/preview-radio`, contrôler les 26 lignes et tester une
écoute individuelle. Cliquer « Démarrer PREVIEW », attendre l'état HLS prêt,
puis aller sur l'accueil et cliquer Play. Un contributeur de test peut écouter
le flux depuis l'accueil, mais ne peut ni piloter le moteur ni télécharger les MP3
individuels. Le compte nico-test reste contributeur.

Arrêter avec le bouton admin. En contrôle opérateur, l'unique commande d'arrêt est
`systemctl --user stop digger-preview-radio.service`.
Vérifier le marqueur stopped, l'historique terminé et l'absence d'enfants FFmpeg
propres à cette unité. Aucun recours à pkill/global kill, aucun service Nicee,
Linsta ou FGL concerné. Une première écoute réelle doit rester courte ; aucune
stabilité 24/7 n'est démontrée par les tests synthétiques.

Pour revenir à la release web précédemment publiée, arrêter d'abord uniquement
l'unité radio, puis installer `publication/digger-demo.service.before` sur l'unité
web, daemon-reload et restart du seul web. Cette copie before correspond au
service actif avant ce raccordement, pas à l'ancienne démo limitée.

## Tests isolés

Résultats : 437 tests collectés, 436 réussis et 1 HTTP privé opt-in ignoré en
65,822 s. Les 48 tests ciblés ont réussi ; le nouveau scénario FFmpeg réel seul
a réussi en 11,500 s. Les trois suites Node et la vérification statique des deux
unités ont réussi. Ces résultats ne constituent pas une endurance 24/7.

```bash
PYTHONPATH=.:tests python -B -m unittest discover -s tests -q
node tests/test_radio_player.js
node tests/test_preview_player.js
node tests/test_local_login.js
systemd-analyze --user verify publication/digger-demo.service publication/digger-preview-radio.service
git diff --check
```

Les contrôles web utilisent des utilisateurs factices, une DB web temporaire,
un catalogue privé temporaire de 26 titres et un contrôleur systemd simulé.
Le scénario réel crée seulement un MP3 synthétique et un runner enfant temporaire :
progression HLS, second lancement refusé, SIGTERM, marker stopped, verrou libéré,
private_plays clos, source inchangée et absence de ses enfants résiduels.
Aucun catalogue ou service réel n'est utilisé par ces tests. Les tests JS simulent
les API navigateur ; une écoute Safari réelle restera à effectuer après activation.

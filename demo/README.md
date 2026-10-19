# Préparation de la démonstration Digger Radio

État actuel : environnement initialisé après autorisation, sur
`feature/digger-stage2-metadata`, base de travail `6f0d976`. Voir le
[rapport d'initialisation](INITIALIZATION.md) pour les opérations réalisées.
Aucun serveur, commit, push ou déploiement. Les étapes de lancement ci-dessous
restent des propositions et ne constituent pas une autorisation de démarrage.
Le premier démarrage temporaire a depuis réussi et a été arrêté normalement.
Voir [la procédure HTTPS et exploitation](OPERATIONS.md) pour la configuration
Nginx à remettre à Nico et l'unité utilisateur préparée, non installée.

## Audit de l'existant

- `app.py`, objet WSGI `app` : accueil/lecteur, comptes, administration,
  propositions URL-first et dépôt/revue finale MP3 + cover. L'exécution directe
  utilise le serveur Flask ; elle ne convient pas au lancement démo proposé.
- `config.py` charge `.env`, impose actuellement les chemins `instance/liquid.db`
  et `storage/{tracks,meta,hls}`, avec un bind Flask `0.0.0.0:5000`.
  Il reste inchangé et n'est jamais exécuté par l'entrée démo.
- `db.py` : `get_db` active les clés étrangères et les fonctions de contrôle des
  droits. `init_db` applique `schema.sql`, puis les migrations 001 (propositions),
  002 (métadonnées), 003 (médias/autorisations). Les migrations ont un registre
  de checksums ; aucune migration n'est automatique au démarrage web.
- `worker.py` : un worker unique choisit les pistes SQLite `approved`, alterne
  jingle/autres genres, décode en PCM 44,1 kHz stéréo puis encode AAC 128 kbit/s.
  FFmpeg produit `stream.m3u8` et `seg_*.ts` (segments 4 s, fenêtre de 6 segments),
  et le worker écrit `now.json` et `plays`. Sans piste, il encode du silence.
  Il nettoie ses fichiers HLS au démarrage et relance son pipeline après erreur.
- Le lecteur utilise `/hls/stream.m3u8` et `/now-playing`. Flask ne sert pas HLS.
  hls.js est actuellement chargé depuis un CDN : dépendance Internet du lecteur.
- Le document de droits est encore un brouillon : le registre des versions
  juridiquement validées est vide. Une nouvelle contribution ne peut donc pas
  recevoir d'approbation finale. Ce blocage reste intégralement en place.

## Modifications préparées et chemins

Seuls de nouveaux fichiers `demo/` et `tests/test_demo_environment.py` sont ajoutés.
Aucun changement au code historique, à `.env`, aux secrets, médias ou services.

| Élément | Chemin prévu |
| --- | --- |
| Configuration privée réelle (créée, 0600) | `/home/jean/.config/digger-demo/settings.json` |
| Modèle sans secret utilisable | `demo/settings.example.json` |
| Racine runtime démo | `/home/jean/digger/instance/demo/` |
| Base indépendante | `/home/jean/digger/instance/demo/digger.sqlite` |
| MP3 démo | `/home/jean/digger/instance/demo/storage/tracks/` |
| Covers démo | `/home/jean/digger/instance/demo/storage/meta/` |
| HLS démo, inactif | `/home/jean/digger/instance/demo/storage/hls/` |
| Verrou du lancement web | `/home/jean/digger/instance/demo/web.lock` |
| Venv indépendant | `/home/jean/digger/instance/demo/venv/` |
| Inventaire des versions installées | `/home/jean/digger/instance/demo/installed-packages.txt` |
| Entrée WSGI démo | `demo.web:application` |
| Proposition Nginx, non installée | `demo/nginx.conf.example` |
| HTTPS final après Certbot, non installé | `demo/nginx.https.conf.example` |

`instance/` est déjà ignoré par Git. Les chemins runtime sont fixes, sans
possibilité de les rediriger par les réglages privés vers les données historiques.
Le lanceur refuse les liens symboliques dans les chemins de données, une base
multi-liens ou appartenant à un autre utilisateur. Les réglages privés doivent
être hors dépôt, appartenir à l'utilisateur et être mode 0600, sans autre lien.
`DIGGER_DEMO_SETTINGS` peut désigner un autre fichier privé externe.

La démo injecte un module de configuration propre avant l'import de l'application.
Elle ne charge ni `config.py` ni `.env`, retire les identifiants hérités YouTube et
Spotify et impose Spotify désactivé. Les formulaires et métadonnées manuelles
restent utilisables ; les extractions externes peuvent être indisponibles.
Inscription publique, vérification email et Google OAuth sont fermés dans cette
instance ; aucun envoi SMTP n'est proposé. `/demo/login` permet la connexion
par mot de passe avec CSRF ; `/admin` conserve les contrôles de rôle existants.
L’accueil démo utilise son propre template public, sans lien Google ni informations de compte.

## Commandes de gestion

Le fichier privé existe désormais : ne pas le remplacer ni le recopier depuis le
modèle. Une clé aléatoire propre à la démo a été créée sans affichage. Le modèle
reste une ressource documentaire, sans secret utilisable.

Depuis `/home/jean/digger`, avec une seule session de travail :

```bash
# Déjà effectué dans le venv démo uniquement ; ne pas répéter inutilement.
instance/demo/venv/bin/python -m pip install --no-cache-dir -r demo/requirements.txt

# Déjà effectué : refuse désormais la base existante. Ne pas supprimer la base.
instance/demo/venv/bin/python -B -m demo.manage init

# Saisie interactive ; compte vérifié réservé au test, dans cette base seulement.
instance/demo/venv/bin/python -B -m demo.manage create-admin

# Premier lancement à autoriser séparément, au premier plan.
instance/demo/venv/bin/python -B -m demo.manage serve
```

Le lancement affiche le PID du master. Depuis un autre terminal, après avoir
confirmé que ce PID correspond toujours à CE lancement :

```bash
kill -TERM <PID_MASTER_DEMO_AFFICHE>
```

SIGTERM demande l'arrêt avec un délai de 15 s pour les requêtes en cours.
Ctrl+C dans le terminal du lancement permet aussi d'arrêter la démo (arrêt rapide
Gunicorn). Ne jamais employer `pkill`, `killall`, ni viser un service radio.
Le verrou est libéré à la fermeture du master et des processus qui l'ont hérité.
Aucun daemon, reloader, nohup, cron ou service systemd n'est installé ou activé.

Gunicorn 26.2.0 est installé dans le venv démo. Le lancement temporaire a confirmé : bind
`127.0.0.1:17000` uniquement, un worker synchrone, un thread, timeout 30 s,
recyclage après environ 500 requêtes, priorité nice +10, 256 descripteurs maximum
et 768 Mio d'espace virtuel **par processus** (master + worker). Ce n'est pas un
quota global de RAM/CPU. Un seul worker réduit la charge mais sérialise les requêtes.
Les uploads démo sont limités à 10 Mio MP3, 4 Mio cover et 16 Mio par requête,
600 s audio, 4 mégapixels. Les coûts de décodage et de hash restent présents.

## Nginx et accès Internet — validation Nico obligatoire

Suivre [la procédure Nico HTTP ACME webroot puis Certbot certonly](NGINX-CERTBOT.md) :
`nginx.conf.example` sert les challenges depuis `/var/www/certbot`, sans proxy
applicatif ni certificat ; tous les autres chemins restent fermés. Certbot
`certonly --webroot` émet seulement le certificat. Nico installe ensuite le HTTPS.
`nginx.https.conf.example` propose ensuite la redirection HTTP vers HTTPS, puis TLS
public avec proxy vers `127.0.0.1:17000`. Aucune Basic Auth globale :
les espaces privés restent protégés par la session applicative, le rôle et les
contrôles de propriété. Voir [le bilan des routes](PUBLIC-SECURITY.md).
Les forwarded headers sont remplacés par le proxy. Nico doit adapter les chemins
TLS créés par Certbot et vérifier la configuration intégrée avec `nginx -t` avant
recharge. Le port 17000 reste en loopback. Les cookies Secure exigent HTTPS.
La configuration finale limite `/login` par IP ; vérifier les IP réelles et les
règles héritées si un CDN/proxy est présent. Aucun certificat ni configuration
Nginx réelle n’est modifié dans cette préparation.

`/hls/` renvoie volontairement 404. Aucun alias public vers les répertoires MP3,
covers ou instance n'est proposé. Les logs d'accès de ce vhost sont désactivés
pour éviter de journaliser des tokens et des paramètres ; la politique de logs
d'erreur doit être confirmée avec Nico. Nginx bufferise les uploads : prévoir
également l'espace et les permissions de son stockage temporaire.

## Activation HLS future — non préparée comme commande exécutable

Ne pas lancer `python worker.py` : il importerait la configuration historique.
Le futur lanceur HLS devra installer la même configuration isolée, prendre un
verrou distinct, limiter FFmpeg et transmettre SIGTERM au worker, qui arrête ses
enfants. Le worker peut fonctionner sans port supplémentaire.

Avant cela, il faut un catalogue démo distinct avec des droits vérifiés, et une
revue explicite de son admission. Aucun MP3 historique ne doit être copié, lié
ou téléchargé automatiquement. Ne pas insérer des pistes `approved` pour éviter
le contrôle juridique, ni modifier le registre des droits pour tester le lecteur.
La validation juridique d'un document et l'approbation finale restent nécessaires
pour les nouvelles contributions ; elles ne sont pas autorisées par ce guide.

Le sélecteur actuel ne recontrôle pas les droits fichier par fichier : il fait
confiance au statut SQLite `approved`. La seule isolation des chemins n'ajoute
pas un contrôle juridique au worker. Un audit du catalogue et de l'intégrité des
médias est donc préalable à tout essai HLS autorisé.

Après accord, remplacer uniquement le bloc Nginx HLS fermé par une exposition
restreinte à `stream.m3u8` et aux `seg_[0-9]+.ts` du répertoire démo, avec types
MIME HLS/MPEG-TS, cache désactivé, sans listing, sans fichiers temporaires ni
`now.json`, sans liens symboliques, après une nouvelle revue spécifique des droits et de l’accès public.
Les droits de traversée/lecture du compte Nginx devront être accordés précisément
par Nico ; ne pas rendre toute la racine privée accessible au serveur web.

## Vérifications et limites

Les six tests initiaux couvraient chemins/configuration isolés et suppression des
identifiants hérités, pages et contrôle admin, connexion admin avec CSRF,
fermeture inscription/OAuth, blocage juridique/HLS, refus d'un chemin symbolique.
Résultat initial : **6/6 en 1,386 s**, sans socket, avec SQLite temporaire dans `/tmp`.
La nouvelle vérification dans le venv démo est **10/10 en 1,029 s** : elle ajoute
le refus de réinitialisation, le terminal obligatoire pour créer un admin et les
deux tests de consolidation stockage/nettoyage. Commande utilisée, plafonnée à 60 secondes :

```bash
PYTHONPATH=.:tests timeout --signal=TERM --kill-after=2s 58s \
  instance/demo/venv/bin/python -B -m unittest -v test_demo_environment \
  test_audio_workflow.AudioWorkflowTests.test_storage_directory_permission_failure_returns_503_without_deposit \
  test_media_storage.MediaStorageTests.test_cleanup_permission_failure_is_reported_and_can_be_retried
```

Aucune suite complète exécutée. Le bind réel, les réponses HTTP locales et
l'arrêt du master/worker avec libération du verrou ont été vérifiés lors du test
temporaire autorisé. Consommation en charge, service persistant, Nginx/TLS et HLS
restent à vérifier lors d'étapes séparément autorisées. Une isolation de configuration
n'est pas un sandbox OS : le compte `jean` conserve ses permissions système.
Une garantie contre du code compromis nécessiterait un utilisateur/conteneur
avec restrictions de fichiers, à concevoir et autoriser séparément.

Le compte démo a été créé par son propriétaire. Les actions restantes sont :
installation et démarrage du service utilisateur après accord,
configuration/protection Nginx et ouverture du domaine après accord.
HLS, certificats, droits d'accès système, commit et push sont des actions séparées.

Références officielles utilisées pour les réglages proposés :
[Gunicorn settings](https://gunicorn.org/reference/settings/),
[Gunicorn signals](https://gunicorn.org/signals/),
[Nginx TLS](https://nginx.org/en/docs/http/ngx_http_ssl_module.html).

# Radio HLS strictement privée — préparation

Le moteur existe dans `worker.py` : décodage MP3 en PCM stéréo 44,1 kHz puis un
encodeur AAC continu, segments HLS de 4 s, fenêtre de six segments, surveillance
de progression et arrêt de FFmpeg. La programmation publique existante tire
aléatoirement les pistes SQLite approuvées en alternant jingles/autres genres.
Elle reste inchangée ; `Engine` accepte seulement une option additive `threads`
pour limiter FFmpeg dans cet essai, sans changer son comportement par défaut.

`private_radio.py` réutilise ce moteur, l’état `now.json`, le nettoyage HLS et le
gestionnaire SIGTERM. Seul l’ordonnancement de test est distinct : ordre du
catalogue SQLite, puis retour au premier morceau. Les MP3 doivent être validés
techniquement, leurs empreintes vérifiées et leur usage privé confirmé.

## Isolation et droits

Tout le runtime privé est sous :

`/home/jean/digger/instance/demo/private-radio/`

- MP3 FGL autorisés : directement dans ce répertoire, noms d’origine conservés,
  y compris Unicode et espaces ; 1–30 pistes acceptées à l’import initial.
- `playlist.json` : manifeste de préparation copié du modèle
  `private_radio_playlist.example.json`, avec les vrais titres/artistes.
- `catalogue.sqlite` : **seule source de vérité de la playlist importée**, ordre,
  hashes, tailles, durées, confirmation privée et historique `private_plays`.
- `hls/` : seuls segments, playlist et état de cet essai.
- `access.key` : mot de passe privé généré à l’initialisation, mode 0600,
  jamais affiché par les commandes. Ne pas le copier dans Git, une URL ou des logs.
- `radio.lock` : exclusion d’une deuxième instance de cet essai.

Répertoires 0700, fichiers runtime 0600. Aucun lien symbolique ni MP3 multi-liens
accepté. Aucun fichier historique lu, copié ou modifié. L’init refuse d’écraser un
catalogue/secret ; l’import refuse de remplacer une playlist existante. La gestion
décrite ci-dessous modifie uniquement les champs d’affichage et de programmation.

Ce catalogue dédié ne contient **aucune table de contributions ni piste publique**.
La confirmation d’utilisation privée FGL ne devient pas un consentement au contrat
de production et ne contourne aucune validation des nouvelles contributions.
`draft-media-v1`, les consentements et le verrou final restent inchangés.
Le flux privé ne doit pas être utilisé pour diffuser des dépôts en attente de droits.

## Préparation puis lancement manuel

Le répertoire/runtime est déjà initialisé ; ne pas répéter `init`.
Le manifeste exemple contient des noms de remplacement, pas des morceaux réels.
Déposer les fichiers autorisés sous le répertoire indiqué, copier le modèle en
`instance/demo/private-radio/playlist.json` et adapter ses entrées. Aucun chemin
externe ou URL n’est accepté. Aucun téléchargement ou client YouTube/Spotify.

Uniquement pour un premier import dans un catalogue vide, depuis `/home/jean/digger` :

```bash
instance/demo/venv/bin/python -B -m private_radio import \
  --playlist instance/demo/private-radio/playlist.json \
  --confirm-private-test-rights

# Au premier plan seulement ; arrêt automatique après 300 s, Ctrl+C pour arrêter avant.
instance/demo/venv/bin/python -B -m private_radio run --seconds 300
```

Le flag d’import confirme que les fichiers exacts sont autorisés pour cet essai
privé. Il faut d’abord disposer de ces fichiers ; aucun substitut historique.
L’import vérifie MP3 réel, durée <= 30 min, taille <= 50 Mio et lie chaque entrée
à son SHA-256. Le runtime revérifie les fichiers avant chaque lecture et s’arrête
si leur identité/contenu change ou si le décodage échoue.

La durée doit être entre 1 et 3600 s. Aucun daemon, unité systemd, tâche cron ni
modification du service web existant. Pas de besoin de redémarrer `digger-demo`.
Un encodeur et au plus un décodeur FFmpeg, threads limités, entrée décodée en temps
réel, nice +10, 128 descripteurs et 768 Mio d’espace virtuel **par processus**.
Ce ne sont pas des quotas CPU/RAM globaux ; durée bornée et arrêt à superviser.

L’interface écoute exclusivement sur `127.0.0.1:17001` avec authentification HTTP
Basic dédiée **à cet essai local**, sans changer le site public sans Basic Auth.
Le port doit être libre ; un conflit arrête le lancement, sans tuer de processus.
Ne jamais publier ce port ni ajouter un proxy Nginx vers ce serveur.

## Vérification et accès distant privé

Pendant le lancement, dans un deuxième terminal, sous le même compte :

```bash
instance/demo/venv/bin/python -B -m private_radio probe
```

Le probe lit le secret local sans l’afficher, interroge uniquement le loopback,
attend une progression de playlist pendant au plus 12 s, télécharge un segment
protégé en mémoire et vérifie la présence des métadonnées courantes.
Attendre environ 8–12 s après démarrage pour la première playlist.

Les routes `/`, `/now-playing`, `/hls/stream.m3u8` et `/hls/seg_*.ts` requièrent
l’authentification. Les MP3 source, SQLite, clé, JSON brut et fichiers temporaires
ne sont jamais servis. Les métadonnées HTTP sont limitées au titre, artiste,
durée, identifiant privé, heure de début et message. Aucune clé dans les URLs.

Depuis le poste de l’administrateur, un tunnel SSH peut être ouvert manuellement :

```bash
ssh -N -L 17001:127.0.0.1:17001 jean@noe
```

Puis ouvrir `http://127.0.0.1:17001/`, utilisateur `digger-test`. Consulter le mot
de passe uniquement dans un outil privé sécurisé, hors transcription/logs ; il
n’est pas affiché par notre CLI. Le lecteur HTML fonctionne avec HLS natif ; les
autres lecteurs peuvent nécessiter un client HLS compatible avec cette auth.
Aucun script tiers/CDN n’est chargé. Ne pas mettre de credentials dans une URL.

Le site public `dig.hqwi.re` et son `/hls/` restent fermés au live. Le web public
continue de présenter un état radio fixe. Le loopback n’est pas un cloisonnement
entre utilisateurs du serveur : la clé protège l’accès local et SSH protège le
transport distant ; le compte propriétaire conserve son accès aux fichiers.

## Vérifications automatisées

Tests séquentiels bornés : confirmation explicite des droits, import non remplaçant,
ordre/hashes, refus de symlinks/URLs/fichiers modifiés, auth obligatoire sur tous
les endpoints privés, absence d’accès aux sources/bases/clés, et vrai moteur HLS
avec sons synthétiques locaux générés par FFmpeg (pas de réseau).
Le test réel effectue trois lectures 1,2,1, contrôle l’historique et vérifie avec
FFprobe un segment AAC stéréo 44,1 kHz. Il arrête et attend les enfants FFmpeg.
Le timer du test de pipeline est borné à 25 s ; une suite n’est jamais parallèle.

```bash
PYTHONPATH=.:tests timeout --signal=TERM --kill-after=2s 58s \
  instance/demo/venv/bin/python -B -m unittest -v test_private_radio \
  test_legacy_upload_retirement.LegacyUploadRetirementTests.test_worker_still_selects_only_existing_approved_legacy_tracks
```

État du catalogue à la préparation de l’administration : 26 morceaux importés,
dont Flex confirmé avec accord du collaborateur, quatre fichiers exclus.
Le manifeste et l’audit privés sont sous `instance/demo/private-radio/`.
Ils sont des instantanés de l’import initial ; SQLite reste la source de vérité
après correction des métadonnées. Ne pas réimporter le manifeste pour annuler
des modifications. Aucun flux FGL réel n’est lancé par cette préparation.
Les tests synthétiques ont validé les contrôles de catalogue/authentification,
le refus des fichiers bruts et symlinks, la compatibilité du worker par défaut,
la boucle réelle 1,2,1 et le codec HLS. Un test de bout en bout supplémentaire
a confirmé le serveur privé loopback et la progression avec authentification
en 14,311 s ; il a été arrêté proprement, et 17001 a été confirmé libre ensuite.
Les exécutions ont été séquentielles, chacune bornée à 60 s. Les sous-tests de
progression tolèrent désormais jusqu’à 12 s pour observer un nouveau segment,
afin de ne pas confondre démarrage/transition et arrêt du flux sur un serveur partagé.

Pour répéter uniquement le test réseau temporaire autorisé :

```bash
DIGGER_PRIVATE_HTTP_TEST=1 PYTHONPATH=.:tests \
  timeout --signal=TERM --kill-after=2s 58s \
  instance/demo/venv/bin/python -B -m unittest -v \
  test_private_radio.PrivateRadioTests.test_bounded_private_http_runner_with_synthetic_media
```

Ce test prend le port 17001 temporairement ; ne pas le lancer pendant une écoute
privée réelle. Il ne nécessite pas de redémarrage du service web, et ne doit pas
être lancé en parallèle d’une autre suite.

## Administration du catalogue depuis un Mac

Cette interface est une application séparée, **jamais enregistrée dans `app.py`**.
Elle écoute uniquement sur `127.0.0.1:17002`, sans proxy Nginx, sans service
persistant, sans dépendance nouvelle et sans redémarrage de `digger-demo.service`.
Elle utilise la clé privée `access.key` et l’utilisateur `digger-test` : toute
personne possédant cette clé peut administrer ce catalogue. La conserver uniquement
pour les administrateurs de cet essai, ne pas la partager avec les artistes.
La session du site public n’accorde aucun accès à cette interface distincte.

Depuis le serveur, sous le compte propriétaire, vérifier que le port 17002 est
libre (`ss -ltn 'sport = :17002'`). Si occupé, ne rien arrêter.

```bash
cd /home/jean/digger
instance/demo/venv/bin/python -B -m private_radio_admin --seconds 3600
```

Lancement au premier plan, sans debug ni rechargement ; arrêt par Ctrl+C ou après
une heure au maximum. Un seul serveur, requêtes limitées à 8 Kio, sockets bornées
à 3 secondes, aucun log d’accès. Aucun MP3 n’est décodé lors de la consultation.

Lors du premier lancement, une migration transactionnelle et idempotente ajoute
`enabled`, `removed_at`, `revision` et `private_catalogue_events` au seul
`instance/demo/private-radio/catalogue.sqlite` (version de schéma 1). Elle refuse
de démarrer pendant un essai radio actif. Pour la préparer sans lancer de serveur :

```bash
instance/demo/venv/bin/python -B -m private_radio_admin --migrate-only
```

Cette migration a été appliquée pendant la préparation : 26 entrées conservées,
quatre MP3 non importés inventoriés, historique et clé inchangés. Aucun morceau
n’a été corrigé, désactivé ou retiré durant cette préparation ; aucun serveur
d’administration n’a été lancé. Le rendu authentifié a été vérifié en mémoire.

Sur le Mac (adapter `noe` à l’hôte SSH habituel) :

```bash
ssh -o ExitOnForwardFailure=yes -N \
  -L 127.0.0.1:17002:127.0.0.1:17002 jean@noe
```

Ouvrir **http://127.0.0.1:17002/** dans le navigateur, puis s’authentifier avec
`digger-test` et la clé consultée dans un outil privé sécurisé. Ne pas afficher
la clé dans une commande ou une URL. Fermer le tunnel par Ctrl+C après usage.
Ne jamais publier le port, ajouter ce serveur au vhost public ou le lancer sur
`0.0.0.0`. Aucun certificat nécessaire dans le tunnel SSH chiffré.

L’interface affiche titre, artiste, durée et statut (actif, désactivé ou retiré),
permet les corrections d’affichage et l’activation/désactivation. Le retrait
requiert une case de confirmation et conserve la ligne, les MP3 et l’historique.
Les fichiers MP3 non importés apparaissent séparément, sans bouton d’import ni
lien audio. Les pistes retirées restent visibles avec leur statut et ne sont pas
présentées comme de nouveaux fichiers à importer.

### Concurrence et sécurité

- Le moteur tient `radio.lock` pendant toute son exécution, avant même de charger
  la playlist. L’import initial, la migration et chaque écriture administrative
  utilisent le même verrou exclusif, sans attente : pendant la radio, la lecture
  de l’interface reste possible mais les écritures retournent HTTP 409.
- Procédure : arrêter **son propre essai privé** par Ctrl+C, modifier le catalogue,
  puis relancer la radio. Aucun arrêt de moteur via le navigateur et aucune
  interruption ou modification à chaud de la piste en cours.
- Les pistes désactivées/retirées sont exclues au prochain lancement. Si toutes
  sont exclues, le moteur refuse de démarrer. Les empreintes et validations
  techniques restent vérifiées sur les pistes retenues.
- Chaque édition vérifie une révision pour refuser les formulaires périmés ;
  journal avant/après et changement sont atomiques dans SQLite. Le journal ne
  contient aucune clé ni donnée du compte public. Il atteste une action du
  détenteur de la clé partagée, sans identifier individuellement la personne.
- Auth obligatoire sur toutes les URL, POST uniquement pour modifier, jeton CSRF
  aléatoire par processus, contrôle Host/Origin, échappement HTML et en-têtes CSP,
  anti-frame, no-store. Aucun fichier source, clé, base, preuve ou HLS servi.
- Aucune nouvelle permission juridique, validation publique, contribution,
  modification de MP3 ou modification de la base web n’est effectuée.

Tests rapides dédiés (temporaires uniquement, sans sockets ni vrais médias) :

```bash
PYTHONPATH=.:tests timeout --signal=TERM --kill-after=2s 45s \
  instance/demo/venv/bin/python -B -m unittest -v test_private_radio_admin
```

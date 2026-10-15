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

- MP3 FGL autorisés : directement dans ce répertoire, noms simples `01.mp3`,
  `02.mp3`, etc. Environ dix recommandés, 1–20 acceptés.
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
catalogue/secret ; l’import refuse de remplacer une playlist existante.

Ce catalogue dédié ne contient **aucune table de contributions ni piste publique**.
La confirmation d’utilisation privée FGL ne devient pas un consentement au contrat
de production et ne contourne aucune validation des nouvelles contributions.
`draft-media-v1`, les consentements et le verrou final restent inchangés.
Le flux privé ne doit pas être utilisé pour diffuser des dépôts en attente de droits.

## Préparation puis lancement manuel

Le répertoire/runtime vide est préparé par l’agent ; ne pas répéter `init`.
Le manifeste exemple contient des noms de remplacement, pas des morceaux réels.
Déposer les fichiers autorisés sous le répertoire indiqué, copier le modèle en
`instance/demo/private-radio/playlist.json` et adapter ses entrées. Aucun chemin
externe ou URL n’est accepté. Aucun téléchargement ou client YouTube/Spotify.

Depuis `/home/jean/digger`, après dépôt des MP3 :

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

Tests séquentiels bornés : confirmation explicite des droits, catalogue immuable,
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

Aucun flux FGL réel ne sera lancé avant réception et import des MP3 autorisés.

État de préparation : répertoire 0700 et runtime initialisés, catalogue vide,
manifeste de dix emplacements prêt sous `instance/demo/private-radio/playlist.json`.
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

# Audit du projet Alive Radio

## Branche et état Git

Branche active : `feature/digger-radio-worker-hls`, alignée sur `origin/feature/digger-radio-worker-hls`. Le dépôt était propre au moment de l’inspection. HEAD : `0b7fc19` (« Prevent HLS segment sequence collision across rapid restarts »).

## Architecture

- `app.py` : application Flask, pages, comptes, authentification, dépôts, validation admin, média admin et métadonnées du titre en cours.
- `worker.py` : sélection et lecture continue des morceaux approuvés, génération HLS avec FFmpeg.
- `db.py` et `schema.sql` : accès SQLite et tables utilisateurs, invitations, genres, pistes et diffusions. SQLite est la source de vérité.
- `config.py` : chemins de base de données et stockage, réglages Flask, SMTP, URL publique et OAuth chargés depuis l’environnement.
- `email_utils.py` : création et envoi des jetons de vérification par courriel.
- `og.py` : récupération et mise en cache des images associées aux URL source.
- `templates/index.html` : accueil, lecteur, connexion Google et formulaire de dépôt.
- `templates/admin.html` : file de modération et actions admin.
- `static/` : logo et image de partage.

Les fichiers suivis inspectés ne comprennent pas de configuration de serveur frontal, de service de déploiement, ni de documentation de lancement.

## Radio et HLS

Le worker sélectionne aléatoirement une piste `approved` dans SQLite et tente d’alterner entre le genre `jingle` et les autres genres. Si la sélection ne donne pas de résultat utilisable, il parcourt le catalogue approuvé. Il décode chaque MP3 par FFmpeg en PCM signé 16 bits, stéréo, 44,1 kHz, puis envoie ce PCM à un encodeur FFmpeg permanent qui produit de l’AAC HLS. Les segments durent 4 secondes, la playlist conserve 6 segments, et FFmpeg supprime les anciens segments. Les noms utilisent la source de numérotation `epoch_us` pour éviter les collisions de séquence au redémarrage.

Le worker écrit atomiquement `storage/hls/now.json`, journalise début et fin de lecture dans SQLite (`plays`), et vérifie que l’encodeur et les segments progressent. Il interrompt et relance le pipeline après une panne, avec une pause de 3 secondes. Les titres dont le fichier manque ou dont le décodage échoue sont exclus temporairement ; le catalogue est réessayé périodiquement. Sans piste disponible, le worker alimente l’encodeur avec du PCM silencieux. Le code précise qu’une seule instance du worker doit tourner. Au démarrage, `clear_hls_dir()` retire les fichiers qu’il gère dans le répertoire HLS (playlist, état et segments `seg_*.ts`) et laisse les autres fichiers intacts.

Le lecteur de `index.html` charge `/hls/stream.m3u8`, utilise le HLS natif ou `hls.js` chargé depuis jsDelivr, et interroge `/now-playing` toutes les 5 secondes. L’application Flask ne déclare pas de route HLS : un serveur ou proxy externe doit exposer `storage/hls/`, mais cette configuration n’apparaît pas dans les fichiers suivis inspectés.

## Authentification, soumission, validation et audio

L’authentification comprend une inscription par mot de passe avec vérification par courriel (jeton signé, durée maximale de 24 heures), une connexion par mot de passe et Google OAuth/OIDC. Des adresses e-mail inscrites en dur dans `app.py` sont promues admins lors d’une connexion Google. Les cookies de session sont HTTP-only, SameSite Lax, permanents pour 30 jours et Secure lorsque le mode debug est désactivé. `ProxyFix` fait confiance à un saut pour les en-têtes X-Forwarded.

Un utilisateur connecté peut déposer un fichier dont le nom se termine par `.mp3`, avec titre et genre obligatoires, artiste et URL source facultatifs. Le fichier reçoit un nom UUID et est enregistré sous `storage/tracks/`. L’application mesure sa taille et tente d’extraire sa durée avec Mutagen, puis crée dans SQLite une piste au statut `pending`. Une URL source déclenche une tentative facultative de récupération de couverture dans `storage/meta/`.

La page admin liste les pistes, d’abord les `pending`, et permet leur écoute via `/media/<id>` (route réservée aux admins). Un admin peut approuver ou rejeter avec motif, réintégrer une piste rejetée, actualiser sa couverture et supprimer une piste rejetée. Les décisions de validation enregistrent le réviseur et l’heure. La suppression retire aussi les entrées `plays` et tente d’effacer le MP3 et la couverture du disque. Seules les pistes `approved` peuvent être diffusées.

## Déjà implémenté

- SQLite avec utilisateurs, invitations, rôles contributor/admin, genres, pistes, statuts de modération et historique de diffusion.
- Vérification d’adresse e-mail, connexion par mot de passe et Google OAuth.
- Dépôt de MP3, extraction facultative de durée, file de modération, écoute admin, rejet, réintégration et suppression.
- Récupération facultative de couverture et affichage des métadonnées du morceau en cours.
- Worker HLS continu avec silence de secours, contrôle de progression, exclusion temporaire des pistes défaillantes et reprise du pipeline.

## Faiblesses, incohérences et points à vérifier

- **Upload** : contrôle d’extension `.mp3`, mais pas de validation approfondie du contenu ; aucune limite de taille n’est déclarée dans le code inspecté. Une erreur de traitement Mutagen n’empêche pas l’acceptation. Le fichier est écrit avant l’insertion SQLite, donc un échec DB peut laisser un fichier orphelin.
- **Téléchargement de couverture** : `og.py` suit les redirections et télécharge des pages/images provenant d’URL utilisateur. Aucune validation visible des domaines, adresses IP ou destinations réseau ; le risque SSRF mérite une vérification dédiée.
- **Transitions admin** : approve/reject ne vérifient pas le statut précédent et peuvent inverser un état. `reintegrate` sélectionne le statut mais ne l’utilise pas. La suppression est définitive et n’est exposée dans l’interface que pour les pistes rejetées.
- **Sécurité des formulaires** : aucune protection CSRF ni limite de débit n’est visible dans le code inspecté.
- **Interface d’authentification** : la page publique propose Google, alors que l’API de connexion par mot de passe existe ; les formulaires visibles n’exposent ni inscription ni connexion par mot de passe. Le nom affiché peut être nul, et l’accueil n’a pas de repli e-mail dans le contexte utilisateur.
- **Configuration et exploitation** : la liste d’admins est codée dans l’application. `APP_BASE_URL` a une valeur locale par défaut (`http://localhost:5050`). Le service réel doit exposer correctement playlist et segments HLS et ne lancer qu’une instance du worker ; aucun fichier de déploiement correspondant n’a été inspecté.
- **État du lecteur** : la position est calculée à partir de l’heure de départ et de la durée DB ; elle peut être inexacte en cas d’interruption, de transition ou de durée inconnue. Le worker peut publier un état « flux indisponible » après une panne. `hls.js` retente les erreurs fatales ; aucun mécanisme de reprise explicite équivalent n’est visible pour le HLS natif.
- **Documentation et vérification** : aucun test, guide d’exploitation ou fichier de service n’a été repéré dans les fichiers suivis. Aucun test n’a été exécuté durant l’audit.

## Plan recommandé

1. Documenter et vérifier le déploiement réel de Flask, du worker, du stockage et du serveur qui expose HLS.
2. Ajouter des tests ciblés pour le dépôt, les transitions admin, les fichiers absents/illisibles, le silence et les redémarrages HLS.
3. Renforcer la validation du contenu et de la taille des uploads, examiner le risque SSRF de la récupération d’images, puis fiabiliser le nettoyage des fichiers et les transitions de statut.
4. Harmoniser les parcours d’inscription et de connexion avec l’interface.
5. Documenter les commandes de lancement et les exigences d’instance unique.

Audit réalisé en lecture seule : aucune modification applicative ou de configuration n’a été effectuée pendant l’inspection.

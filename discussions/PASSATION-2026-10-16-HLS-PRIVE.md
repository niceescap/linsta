# Passation Digger Radio — 16 octobre 2026 — arrêt des essais

## Instruction de clôture prioritaire

L’utilisateur a demandé d’arrêter immédiatement les essais audio et tout développement
supplémentaire en raison de la pression disque/mémoire. Aucun nouveau test FFmpeg,
essai FGL, service, redémarrage, changement Nginx/Linsta ou push ne doit être lancé
dans cette session. Conserver les modifications et **ne pas commiter les corrections
HLS avant validation réelle complète**. Cette passation reste elle aussi non commitée.

## Projet et Git

- Espace : `/home/jean/digger` ; lire `AGENTS.md` avant reprise.
- Branche : `feature/digger-stage2-metadata`, jamais `main`.
- HEAD : `1e4117c feat: add SSH-only private radio catalogue administration`.
- Précédents : `f4322e6` (30 pistes/noms Unicode), `2337a90` (radio privée isolée).
- Base Stage 2 connue : `6f0d976` ; lots 6D/6E présents selon les vérifications antérieures.
- Aucun commit, push ou merge durant la session HLS interrompue.

État Git à la clôture, avant ajout de cette passation :

```text
 M private_radio.py
 M submission_audio_routes.py
 M templates/admin_submission_detail.html
 M templates/submission_audio.html
 M tests/test_audio_workflow.py
 M tests/test_flask_submission_review.py
 M tests/test_private_radio.py
 M worker.py
?? demo/
?? discussions/private-hls-reliability.md
?? documents/
?? templates/submission_conditions_v03.html
?? tests/test_demo_environment.py
?? tests/test_private_hls_continuity.py
```

Le présent fichier s’ajoute aux fichiers non suivis. Les modifications de
`submission_audio_routes.py`, des templates publics et des tests audio/review,
ainsi que les dossiers `demo/`, `documents/`, le template V0.3 et
`tests/test_demo_environment.py`, **préexistaient à la mission HLS** : ne pas les
écraser, nettoyer, réinitialiser ni embarquer involontairement dans un commit HLS.
Le dernier `git diff --check` exécuté avant la clôture ne signalait pas d’erreur.

## Données et catalogue strictement privé

Racine dédiée : `/home/jean/digger/instance/demo/private-radio/`, hors Git.

- `catalogue.sqlite` : source de vérité privée, indépendante de la base web.
- 26 morceaux importés, environ 2 h 02 min, ordre initial stable.
- `playlist.json` et `catalogue-audit.json` : instantanés de l’import initial.
  Ne pas les réimporter pour rétablir des métadonnées après édition administrateur.
- `hls/` : seuls médias et état du flux privé ; `radio.lock` exclut les essais
  simultanés et les écritures administratives pendant la lecture.
- `access.key` : clé privée existante, jamais affichée, modifiée ou copiée dans Git.
- `LISTEN-MAC.md` : procédure privée déjà préparée, hors Git.
- Répertoire 0700, état privé 0600 selon les vérifications précédentes.

Le contrôle final du catalogue a utilisé SQLite en lecture seule : 26 lignes,
références fichiers/SHA-256/tailles identiques à l’audit initial,
`PRAGMA quick_check = ok`. Les trois lectures historiques sont restées intactes ;
la dernière (piste 2) va de 00:30:40 à 00:37:18 UTC le 16 octobre.
Aucun fichier source n’a été modifié, supprimé ou réimporté par la mission HLS.
Les corrections d’affichage éventuellement effectuées par l’administrateur ne
sont pas annulées : la vérification comparait les références/empreintes/tailles,
pas les titres et artistes modifiables.

Droits confirmés par l’administrateur pour cet essai FGL strictement privé,
y compris **Flex (Original Mix)**, collaboration avec Tristan Casara dont
l’accord privé a été confirmé. Quatre fichiers présents mais non importés :
les deux DJ mixes BELUGA, l’enregistrement de 9,7 secondes et la variante YouTube
de It’s You. Origine/droits du répertoire des mixes et de la variante YouTube
non établis ici ; aucune ouverture publique autorisée.

Deux écarts ID3 connus : Nw obs2 / titre Nw obs1, et Right on / titre Rise up.
Dix-neuf pistes étaient sans tag artiste à l’import ; aucune attribution inventée.
Ces informations ne remplacent pas les éventuelles éditions SQLite ultérieures.

## Fonctionnalités opérationnelles et accès

### Web démo

- Application indépendante Gunicorn : `demo.web:application`, un worker, sans debug,
  sur `127.0.0.1:17000`, service utilisateur `digger-demo.service`.
- Base web indépendante : `instance/demo/digger.sqlite` ; ne pas réinitialiser.
- Venv : `instance/demo/venv/` ; Python 3.12, FFmpeg système déjà disponible.
- Paramètres privés existants : `/home/jean/.config/digger-demo/settings.json`.
  Ne jamais afficher/modifier les secrets ni `.env`.
- HTTPS `https://dig.hqwi.re` précédemment opérationnel via Nginx IPv4/IPv6.
  HTTPS non retesté à la clôture : aucune nouvelle sollicitation réseau demandée.
- Accueil public, administration applicative avec rôles, dépôt et consentements
  existants. Contrat V0.3 informatif, mention bêta, `draft-media-v1` et verrou
  juridique de validation finale préservés. Inscription/OAuth ne doivent pas
  être ouverts sans leur contrôle dédié. HLS public fermé.

### Administration du catalogue privé

Interface séparée `private_radio_admin.py` et `private_admin_templates/`,
commitée dans `1e4117c`, jamais enregistrée dans l’application publique.
Liste titre/artiste/durée/statut, métadonnées d’affichage sans modification MP3,
activation/désactivation et retrait logique confirmé (fichier et historique
conservés), inventaire séparé des fichiers non importés.

Migration privée version 1 déjà appliquée : `enabled`, `removed_at`, `revision`,
journal `private_catalogue_events`. Pas de modification des tables web.
Pendant la radio : consultation possible, écritures refusées HTTP 409 via le même
verrou. Formulaires périmés refusés, CSRF/Host/Origin contrôlés, HTML échappé,
aucune route de téléchargement de MP3, clé ou SQLite.

Accès Mac à l’administration existante : tunnel SSH
`ssh -o ExitOnForwardFailure=yes -N -L 127.0.0.1:17002:127.0.0.1:17002 jean@noe`,
puis `http://127.0.0.1:17002/`. Adapter `noe` à l’hôte SSH habituel.
Utilisateur `digger-test`, clé `access.key` consultée uniquement dans un outil
privé sécurisé. Toute personne détenant cette clé peut administrer ce catalogue ;
ne pas la partager avec les artistes ni la placer dans une URL/commande/log.

Cette administration est un processus manuel borné à une heure ; le tunnel ne
la démarre pas. Son éventuelle relance devra relever d’une prochaine instruction,
pas de cette session clôturée. Guide : `discussions/private-radio.md`.

### Radio privée

Écoute antérieure réussie depuis Safari via tunnel SSH vers `127.0.0.1:17001` et
la même authentification privée. Boucle SQLite ordonnée, AAC/HLS, métadonnées,
sonde locale authentifiée. Ne pas confondre ce succès d’écoute avec une validation
d’endurance : un arrêt est survenu pendant le deuxième morceau et la correction
actuelle n’est pas validée en conditions réelles.
**Flux actuellement arrêté ; ne pas relancer avec le code en cours sans revue.**

## Diagnostic HLS : faits et incertitudes

Erreur : `private_radio_worker.PipelineError: playlist absente ou segments HLS sans progression`.
Seule ligne FFmpeg conservée par l’utilisateur : `[mpegts ...] frame size not set`.
Ces deux lignes seules ne prouvent pas la cause exacte de l’incident.

Faits collectés :

- Derniers segments espacés normalement d’environ 4 s jusqu’à 00:36:53 UTC ;
  arrêt à 00:37:18, proche de la fin du deuxième morceau (375,36 s).
- PTS réguliers sur les deux premiers MP3, pas de saut temporel anormal détecté.
- Reproduction avec trois WAV synthétiques de 3 s : environ 23 s au total ;
  FFmpeg vivants mais attentes noyau `folio_wait_bit_common` / `__wait_on_buffer`
  observées au démarrage et en fin de piste.
- Démarrage d’un autre FFmpeg synthétique dépassant 15 s ; strace borné également
  arrêté à sa limite de 10 s durant le chargement de bibliothèques.
- PSI disque global très élevé (pics >80 % de temps bloqué), pression mémoire
  également élevée (>20 % par moments), faible charge CPU ; environ 4 Gio RAM,
  pas de swap et moins de 600 Mio disponibles lors des dernières mesures.
- Aucun consommateur responsable identifié ; aucun processus d’autrui touché.

Le mécanisme de fragilité démontré est le couplage entre l’attente du décodeur /
des I/O du thread principal et l’alimentation de l’encodeur : sans PCM, un encodeur
vivant ne publie plus de segments. L’observation étaye l’hypothèse d’une famine
PCM liée aux I/O ; elle ne certifie pas rétrospectivement la cause de cet incident.
Un serveur sous cette pression ne permet pas de promettre une musique sans trou.

## Corrections HLS EN COURS, non commitées

Fichiers propres à cette mission :

- `worker.py` : option additive `Engine(continuous=True)` ; thread PCM indépendant,
  FIFO limitée à 16 blocs (3,2 s de PCM max dans la FIFO, plus un bloc en cours),
  contre-pression sans suppression d’audio, horloge absolue sans dérive cumulative,
  silence temporaire signalé, erreurs propagées et nettoyage du thread.
  Suppression de `-re` uniquement dans ce mode (une seule horloge), fin de tampon
  écoulée avant retour de `play()`. Pipes d’écriture non bloquants et gestion
  d’EAGAIN/écritures partielles. Seul le thread PCM observe HLS pour éviter les
  mises à jour concurrentes et lectures disque redondantes.
- **HEALTH_TIMEOUT et STARTUP_TIMEOUT restent tous deux à 20 s.** Une source PCM
  absente ou un décodeur inactif sont surveillés séparément avec une limite de
  60 s, puis erreur/arrêt ; pas de silence permanent ni de boucle de redémarrages.
  Ce nouveau délai d’entrée exige encore la validation réelle ci-dessous.
- `private_radio.py` : active cette option seulement pour le moteur privé.
- `tests/test_private_hls_continuity.py` (nouveau) : régressions déterministes.
- `tests/test_private_radio.py` : scénario réel synthétique de transition bloquée,
  boucle 1,2,1, métadonnées, codec et arrêt, avec délai global borné.
- `discussions/private-hls-reliability.md` (nouveau) : diagnostic, détails techniques,
  limites et commandes de validation future ; ce n’est pas une instruction de
  relance immédiate.

Le FIFO ne grossit pas avec l’écoute ; sa limite ne borne pas le délai total
Safari + fenêtre HLS + buffers FFmpeg + réseau SSH. La correction peut maintenir
HLS au prix de silence temporaire pendant une attente : elle ne supprime pas les
contraintes physiques de la machine. Les écritures non bloquantes touchent aussi
le code partagé du worker ; préserver sa compatibilité historique lors de la revue,
sans redémarrer les services existants pour la tester.

## Tests : état exact

Exécutions séquentielles et bornées, aucune suite lourde complète :

- **22 tests réussis** : `test_private_hls_continuity` (11) et
  `test_private_radio_admin` (11), dernière exécution 1,120 s.
- **1 test ciblé historique réussi** :
  `test_legacy_upload_retirement.LegacyUploadRetirementTests.test_worker_still_selects_only_existing_approved_legacy_tracks`,
  2,265 s. Il était déjà lancé avant la demande d’arrêt, son résultat a seulement
  été récupéré lors de la clôture. Aucun FFmpeg dans ce test.
- Les tests déterministes couvrent une attente source simulée de 25 s, un encodeur
  figé détecté à 20 s, arrêt du silence à 60 s, horloge sans dérive, FIFO pleine,
  écritures partielles/EAGAIN, vidage avant transition, propagation d’erreurs,
  arrêt/réinitialisation et sécurité/intégrité du catalogue.
- Le premier scénario réel du nouveau mode a **dépassé la limite globale de 58 s**
  pendant une nouvelle pression disque élevée : **validation réelle incomplète**.
  Plusieurs ajustements (horloge absolue, unique horloge sans `-re`, garde de source
  inactive, observateur HLS unique) ont été faits ensuite : la version finale n’a
  pas fait l’objet d’un nouvel essai FFmpeg, conformément à l’arrêt demandé.
- Un essai initial de référence avait aussi échoué avant le test : génération
  FFmpeg du son synthétique dépassant 15 s.
- Aucun essai FGL pendant cette mission, aucun nouveau `private_plays` réel ajouté.
- Aucun commit HLS : les tests déterministes ne remplacent pas un succès réel.

## Services et processus à la clôture

Dernière inspection légère en lecture seule du compte `jean` et des ports :

- **Aucun processus `ffmpeg` sous `jean`**, donc aucun résidu de nos essais.
- Aucun écouteur sur 17001, aucun moteur radio privé actif.
- `digger-demo.service` : **active**, master Gunicorn PID 740044 / worker 740051,
  écoute sur `127.0.0.1:17000`. Pas de redémarrage durant cette mission.
- Administration privée : PID 750764, commande
  `instance/demo/venv/bin/python -B -m private_radio_admin --seconds 3600`,
  écoute sur `127.0.0.1:17002`, déjà lancée avant la mission. Laissée intacte ;
  sa limite temporelle peut la faire expirer après cette photographie.
- D’autres processus Codex/SSH existaient sous le compte : aucun arrêt ni aucune
  attribution arbitraire à notre essai. Aucun processus d’un autre compte inspecté
  en détail ou modifié. Ne pas terminer des sessions sans identifier leur propriétaire.
- Aucun changement Nginx, Linsta, services de Nico, base historique, médias
  historiques, consentements, verrou juridique ou lingering.
- Ne pas déduire de cette inspection que tous les services de Nico ont été
  diagnostiqués : leur santé n’a pas été testée ; la pression globale demeure
  une limite externe, sans action corrective système autorisée.

## Priorités de la prochaine session

1. Relire cette passation, `AGENTS.md`, le diagnostic et l’état Git ; préserver les
   changements préexistants. Ne pas supposer que les processus/ports sont identiques.
2. **Étudier un serveur dédié Digger Radio** avec l’utilisateur et Nico : budget,
   mémoire et I/O nécessaires, capacité disque et sauvegardes, accès SSH, isolation,
   supervision, coût et procédure de migration. Ne rien acheter, déplacer ou déployer
   sans nouvelle autorisation. Le serveur partagé ne doit pas servir de banc de
   charge ni faire l’objet de changements système improvisés.
3. Avant tout audio, confirmer un retour durable à des ressources suffisantes
   avec Nico. Identifier les contraintes I/O/mémoire sans intervenir sur ses
   applications ; ne pas simplement augmenter les délais.
4. Revoir le correctif en cours, notamment l’horloge, le retard FIFO réel, le délai
   d’entrée de 60 s, les transitions/métadonnées, l’arrêt sur source bloquée,
   l’arrêt propre et l’absence de processus enfants résiduels.
5. Seulement ensuite, lancer un unique test synthétique réel borné et capturer
   sans secret la progression HLS, les attentes des enfants et les ressources.
   Conserver le watchdog HLS réel à 20 s. Exiger la réussite et le nettoyage avant
   tout commit des fichiers de cette mission.
6. Si ces conditions sont réunies, nouvel essai FGL **court et privé** sur les fichiers
   déjà autorisés, après contrôle port/verrou ; ne pas lancer les 26 morceaux en
   endurance d’emblée. Pas de réimport, aucune modification MP3/catalogue, pas
   d’interruption d’un essai actif. Confirmer ensuite l’intégrité et les résidus.
7. Commit local limité aux fichiers HLS de la mission seulement après succès réel,
   sans secrets, bases, MP3, covers ou runtime. Aucun push sans autorisation.

La session s’arrête après écriture de cette passation. Aucune reprise automatique
des tests, du développement ou des services n’est autorisée par ce document.

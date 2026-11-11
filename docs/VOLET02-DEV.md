# Volet 02 — radio musicale privée DEV

## Architecture et exploitation

- Entrée web : `dev_web:application`, seulement `127.0.0.1:17010`.
- Même moteur `private_radio.play_loop` / `worker.Engine`, lancé par
  `dev_launcher radio --mode continuous`. Aucun second moteur ni nouveau service systemd.
- `DevController` observe le verrou et le marqueur de cette instance ; le web ne
  démarre pas le moteur et l'absence d'auditeur ne l'arrête pas.
- HLS commun : `/preview-radio/hls/stream.m3u8` et ses segments authentifiés.
- État : `/preview-radio/now-playing?at=<date HLS audible>` ; tout accès exige la
  session existante, avec SQLite comme source des rôles et métadonnées du catalogue.
- Les commandes d'administration et le parcours d'ajout MP3/WAV sont conservés.
  Le moteur réserve et réactualise le prochain morceau depuis ce même catalogue.
- Aucun accès à dig.hqwi.re, aucune modification de la BÊTA ou de la production.

## Catalogue

`dev_import_private_catalogue.py --apply` réalise une copie privée vérifiée depuis
`/home/jean/digger/instance/demo/private-radio` (source ouverte en lecture seule).
Les empreintes, tailles, validations privées et métadonnées sont conservées ; les
identifiants DEV sont distincts. Ni clé d'accès, ni session, ni compte source copié.
Le script refuse une collision de fichier et déduplique les lignes par SHA-256.
Il n'est pas un synchroniseur permanent avec la BÊTA ; les futurs ajouts se font
normalement dans l'administration DEV, sans deuxième catalogue ni réimport manuel.

Inventaire après copie : **26 morceaux musicaux actifs**, 26 MP3 présents/validés,
0 WAV, 0 fichier manquant, 0 doublon d'empreinte parmi les actifs.
Les trois MP3 synthétiques préexistants sont conservés, mais `enabled=0` afin de
les retirer de la rotation musicale. Aucun droit ni statut juridique n'est promu.
Total SQLite : 29 morceaux, dont 3 fixtures inactives. Aucun titre réel en attente
ou retiré dans la source au moment du relevé.
Six morceaux disposent d'une pochette intégrée ; vingt n'en ont pas et affichent
le logo de secours existant. Aucun visuel d'artiste inventé.
Détails privés : `instance/dev/catalogue-import.json`, `volet02-inventory.json`.

## Jingles et niveaux

Trois MP3 validés copiés dans `instance/dev/private-radio/jingles/JINGLLE` :
Selected Digger 01 (7,224 s), Welcome Digger 02 (5,640 s), listening Digger 03 (5,640 s).
La règle existante reste inchangée : un jingle entre deux morceaux, tirage parmi
les valides sans répétition immédiate lorsqu'il y a plusieurs choix. Les jingles
restent hors de `private_tracks` et sont encodés par le même encodeur HLS.

Les 29 sources musicales/jingles ont été décodées intégralement et mesurées :
- Musique : -15,9 à -9,7 LUFS intégrés.
- Jingles : -16,3 / -16,9 / -18,4 LUFS ; pics -4,5 / -4,6 / -3,7 dBFS.

`dev_audio_gain.MeasuredGain` applique un gain constant mesuré vers -18 LUFS,
plafonné pour garder 1,5 dB de marge sur le pic échantillonné. Pas de compression,
pas de réécriture des fichiers et pas de changement du volume utilisateur.
Les mesures sont liées au SHA-256 ; un nouvel ajout admin est mesuré une fois.
Cette analyse initiale d'un nouvel ajout peut ajouter un délai de démarrage du
premier décodage. Le tampon PCM/HLS existant continue pendant cette mesure.
Le crochet `gain_resolver` de `worker.Engine` est inactif par défaut ; seul
`dev_radio --mode continuous` active le gain DEV. Les autres environnements et
le mode PREVIEW historique gardent leur comportement.
Mesures privées : `instance/dev/volet02-levels.json`.

## Synchronisation et historique

`dev_live_data.py`, activé seulement par `EDITORIAL_LIVE_DATA` dans l'entrée DEV,
croise les événements réellement envoyés à l'encodeur PCM, l'origine
EXT-X-PROGRAM-DATE-TIME et la fin des segments effectivement publiés.
Le navigateur transmet sa position HLS audible ; une donnée future ne devient
jamais un passage historique. Sans horloge navigateur, l'API indique explicitement
la frontière HLS publiée et le lecteur ne prétend pas afficher la position entendue.
Chromium annonçant HLS natif sans horloge Date utilise hls.js ; Safari conserve
le HLS natif lorsqu'il fournit `getStartDate`. Aucune position n'est inventée.

Les huit derniers éléments terminés sont présentés dans l'interface, avec heure
locale dérivée du temps HLS, pochette musicale réelle si disponible et fiche privée.
Les jingles sont identifiés comme tels, sans faux artiste ni fausse pochette.
À suivre affiche uniquement le prochain morceau effectivement réservé, sans
prétendre connaître à l'avance le jingle aléatoire encore non sélectionné.
L'historique couvre la session moteur courante (fenêtre de 128 événements) ; il
n'est pas une archive durable entre redémarrages. Les anciennes lignes de
`private_plays` ne sont pas recyclées en faux horaires d'antenne.

## Vérifications

Tests ciblés : routes PREVIEW (24), métadonnées HLS publiées/audibles (23, dont
cas PREVIEW réutilisés), interface/accès (25), moteur continu (6, dont HLS réel
synthétique), gain mesuré (3), outils DEV (7), copie privée/idempotence et jingles.
Les suites JS historiques restent utilisées pour les régressions du lecteur.

Vérification musicale réelle : `tests/dev_real_live_browser.cjs` lit le véritable
serveur et le véritable HLS DEV dans Chromium, sans simulation des médias. Une
session de test signée pour un compte DEV vérifié existant reste en mémoire ;
aucun mot de passe ou rôle n'est modifié, aucun cookie n'est imprimé/enregistré.
Le contrôle couvre décodage AAC, plusieurs morceaux, jingle, synchronisation,
historique, navigation, interruption réseau et refus anonymes.
Les preuves audio capturées et rapports sont sous `instance/dev/volet02-validation`
(privés, exclus de Git). Ce contrôle automatique ne constitue pas une écoute
subjective humaine ni une recette Safari/iPhone physique.

## Accès Mac

Tunnel existant, ou dans un Terminal Mac laissé ouvert :

```sh
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:17010:127.0.0.1:17010 jean@163.172.154.84
```

Ouvrir `http://127.0.0.1:17010/connexion`, utiliser le compte DEV existant, revenir
sur Radio puis activer le son si Safari bloque l'autoplay. Pause ne commande que
le navigateur ; reprise rejoint le direct. La navigation éditoriale conserve
l'audio unique. La connexion/déconnexion peut toujours interrompre l'écoute.

## Réversibilité

Aucune suppression de fichiers ni de catalogue. Les médias source n'ont pas été
modifiés. Arrêter uniquement le superviseur DEV identifié si nécessaire, jamais
un PID non vérifié ni un service BÊTA. Le crochet de gain reste optionnel ; le
raccordement enrichi peut être désactivé dans l'entrée DEV après arrêt autorisé
pour revenir aux métadonnées historiques. Ne pas utiliser reset/clean global.
Aucun commit ou push automatique ; aucun déploiement en production.

### Résultat réel constaté le 22 octobre 2026

Rapport navigateur : **passed**, 00:08:04–00:13:47 UTC ; deux morceaux musicaux
réels (`Gravity - Deep soulful Final mix`, `Move your weight V2`) séparés par
`Selected Digger 01`, dans le HLS commun normalisé. Les trois jingles sont intégrés
et ont été décodés/mesurés à la source ; un a été observé durant ce contrôle HLS
normalisé. Les deux autres ont aussi été émis par la rotation précédente, sans
prétendre les avoir tous écoutés physiquement dans Safari.
89 segments réels contigus capturés et décodés, 166 contrôles de synchronisation,
4 vérifications d’historique ; les 2 éléments terminés s’affichent après leur fin.
Même encodeur 1770843 ; surplus aux frontières PCM 0,160 et 0,176 s ; aucun manque
PCM signalé. La navigation conserve l'audio ; une interruption réseau de 5 s
récupère automatiquement. Les routes privées refusent l'anonyme (401).
Les 6 vraies pochettes ont été décodées comme images valides via les routes privées ;
les 20 autres renvoient explicitement le logo de secours existant.
Les transitions directes morceau→morceau restent couvertes par les essais moteur
réels synthétiques ; avec les jingles disponibles, la programmation réelle les
intercale conformément à sa règle, qui n'a pas été désactivée pour le test.
Le moteur reste actif après fermeture du navigateur de test, prêt pour Safari.

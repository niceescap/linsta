# Safari — horloge audible, pause et compteurs (22 octobre 2026)

## État réel

Le cas Safari Mac reste NON VALIDÉ. Aucun export du Mac n'a encore été reçu.
Les captures décrites identifient le basculement d'affichage, mais ne permettent
pas de déterminer si getStartDate() est invalide, absent ou décalé. Ne pas
annoncer que Chromium ou WebKit Linux valide Safari/AVFoundation.

## Cause confirmée dans le code

refresh() omettait ?at en pause : le serveur renvoyait le bord publié du flux.
Pendant la lecture, clock() exigeait la date native ; displayedData() masquait
le bord publié ou les positions hors des bornes de l'élément. Les métadonnées
visibles en pause n'étaient donc pas une preuve du morceau réellement entendu.
progress() dépendait de cette même date et affichait un temps restant à droite.
La pause appelait detach(), audio.load(), puis recréait la session à la reprise.
Le précédent élargissement de la fenêtre serveur ne résout pas une date native
absente ou fausse. Aucune calibration approximative n'a été ajoutée.

## Corrections ciblées

- static/editorial/player.js : pause du seul élément existant, sans détachement ;
  gel de la date audible et des métadonnées prouvées. La reprise rejoint le
  direct avec la même session/source ; délai du watchdog réarmé pour le chargement.
- Les réponses antérieures à pause/reprise sont invalidées par epoch. Un échec
  de métadonnées ne recharge pas le média ; 401/403 restent immédiatement bloquants.
- displayedData() ne confond plus pause et autorisation d'afficher le bord publié.
  Sans horloge prouvée, attente honnête même en pause. Titre/artiste/pochette des
  deux lecteurs proviennent toujours du même objet ; aucune fausse donnée.
- dev_live_data.py : réservation confirmée indépendante de l'horloge cliente ;
  si celle-ci est invalide, À suivre conserve le prochain morceau vérifié au bord
  publié, avec next_metadata_basis explicite. Aucun élément futur non publié
  n'est présenté comme courant ; aucun changement de programmation ou d'éligibilité.
- Compteur : elapsed = date audible - playback_started_at ; gel pendant la pause.
  Durée totale finie du morceau à droite (data-duration), jamais la durée HLS.
  Barre non interactive. Durées inconnues/horloge non fiable restent --:--.
- templates/editorial/shell.html : durée totale et bouton d'export d'horloge ciblé.

## Vérifications

26 tests API LIVE + 26 interface/accès réussis. Régressions JS : lecture native
avec date simulée valide/invalide, pause/gel/reprise au direct, transitions
morceau/jingle/morceau, compteurs et durées, mêmes surfaces, 1 source/0 load.
Ce sont des régressions complémentaires, pas une écoute Safari réelle.
Test réel Chromium/hls.js : 18,616 s, pause 5,5 s, titre/artiste/compteur figés,
reprise et navigation, position comparée à timeline.json, durée source vérifiée,
1 attachement/0 détachement, source inchangée. Pas de nouvelle transition réelle
complète dans ce contrôle court. Preuve privée :
instance/dev/safari-clock-pause/report.json. Aucun nouveau long contrôle général.
HTTP réel : ?at=nan conserve une réservation confirmée tout en laissant le titre
courant en attente ; médias/métadonnées anonymes 401. Syntaxe/diff/permissions OK.
Moteur, fichiers audio, catalogue, BÊTA/production inchangés. Web DEV rechargé
seulement. Partage Nico et son échéance initiale inchangés, aucun accès prolongé.

## Diagnostic exact à obtenir sur le Mac

Avec le tunnel DEV habituel, ouvrir :
http://127.0.0.1:17010/?radioDiagnostics=1
Recharger Cmd-Maj-R. Version attendue : safari-clock-pause-2026-10-22.
Activer le son et attendre 10 s, ouvrir Diagnostic radio DEV, cliquer
Exporter l’horloge Safari. Transmettre le JSON digger-radio-horloge.json.
Puis pause 5 s : vérifier le gel. Reprise : vérifier retour au direct sans coupure.
L'export ciblé contient nativeOrigin/type/erreur, currentTime, date calculée,
paramètre at envoyé, statut HTTP, bornes et base de la réponse. Il ne contient
ni cookie, ni jeton invité, ni secret/session de compte.

PROCHAINE ACTION : analyser cet export avant de choisir un correctif de l'horloge
native. Ne pas supprimer la vérification du morceau entendu pour masquer le défaut.

## Protection contre la sentinelle Safari — 11 novembre 2026

Diagnostic Mac rapporté : currentTime=Number.MAX_VALUE pendant playing, origine
native valide, réponse clock-outside-emitted-timeline. Ce relevé seul ne prouve
ni un gel sonore ni la permanence de l'anomalie. La cause interne AVFoundation
n'est pas établie. Le lecteur pouvait également propager une borne seekable
indéfinie via end-12 : cette hypothèse doit être vérifiée sur le Mac.

Version JS : safari-sentinel-2026-11-11. Positions et bornes extrêmes rejetées ;
position contrôlée contre seekable ou buffered conservé. Aucun remplacement
par la fin du buffer, le bord publié ou une horloge extrapolée. Le seek initial
et la reprise explicite utilisent seulement une plage valide, même source.
Une horloge illisible suspend seulement le watchdog de non-progression ; les
erreurs média et les contrôles d'accès restent actifs. À suivre est conservé.
Titre/artiste/pochette/compteurs se rétablissent ensemble lorsque la position
redevient exploitable ; sinon l'attente reste explicite.

Régression Node : MAX_VALUE, bornes invalides, 23 stalled sur 92 secondes,
aucune reconnexion, retour d'horloge, morceaux/jingle/morceau et pause/reprise.
Ce test simulé ne valide pas Safari Mac. Exporter après 15 secondes puis après
60 secondes d'écoute : clockProbe.position, invalidMediaSeconds,
invalidMediaSamples, recoveredClocks, lastSeek, buffered/seekable et bornes HLS.
Noter séparément si le son est continu ou réellement interrompu.

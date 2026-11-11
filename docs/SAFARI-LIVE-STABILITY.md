# Correctif Safari — stabilité du direct privé DEV

## Cause reproduite

Le lecteur liait `stalled` à `retry()`. Trois secondes après cette notification,
`retry()` appelait `detach()` : pause, destruction du tampon/instance HLS,
suppression de src et `audio.load()`, puis réattachement. Or `stalled` décrit le
chargement réseau ; il n'implique pas que le son a cessé de progresser.

Reproduction avant correction : vrai flux DEV AAC/HLS, adaptateur HLS natif
sélectionné, aucun mock de play/pause/currentTime/décodage. En 46,395 s :
**6 appels load() et 6 réinitialisations**, aucun échec HTTP observé.
Exemple : stalled à 4,971 s, readyState=4, currentTime=3,655 ; emptied et nouvelle
source à 7,977 s. Même séquence vers 11,565/14,570 s puis 18,158/21,160 s.
Cela reproduit les coupures périodiques malgré un tampon prêt et un flux sain.
Preuve privée : `instance/dev/safari-before/report.json`.

Moteur : même encodeur DEV 1770843, segments autour de 4 s (3,994–4,017), fenêtre
6 segments (~24 s), pas de famine PCM ou de redémarrage pendant le diagnostic.
Le serveur web historique n'avait pas de journal d'accès conservé (stderr sur un
pipe) : aucune trace rétrospective du Safari du Mac n'est inventée. Les tests
capturent les réponses HTTP et événements média contemporains.

## Correctif ciblé

- stalled est enregistré sans détruire la source. Le navigateur garde son tampon.
- Le watchdog attend 15 s sans progression effective du temps média avant une
  récupération. Un retour de progression annule tout redémarrage différé.
- Reconnexion sur vraie erreur média, avec temporisation croissante bornée.
- Le saut au direct est effectué une fois à l'ouverture des métadonnées, avec
  marge native de trois segments (~12 s), pas à chaque notification.
- Un échec temporaire de métadonnées ne coupe plus un audio sain. Les refus
  401/403 coupent toujours immédiatement l'écoute. Un arrêt moteur confirmé aussi.
- Les vignettes « À suivre » et les éléments d'historique ne sont plus recréés à
  chaque poll inchangé, afin d'éviter des requêtes d'images inutiles.
- Une seule source/audio/instance HLS ; aucun changement au moteur ou aux médias.

## Vérification et limites de Safari

Un essai réel prolongé du chemin natif est lancé par `tests/dev_native_soak.cjs`.
La sélection de l'adaptateur est forcée pour le test Chromium natif, mais aucun
média, horloge, play/pause, événement ou transport réseau n'est simulé. Ce chemin
reproduit la boucle avant correction et vérifie son absence après correction.
Le test hls.js réel reste complémentaire, notamment pour l'horloge audible et les
métadonnées. Les tests DOM simulés ne sont pas présentés comme une écoute réelle.

Safari/AVFoundation macOS n'est pas exécutable sur ce serveur Linux. Un WebKit
Linux isolé a été essayé, sans installation système ; son backend GStreamer HLS,
désactivé par défaut, cesse de progresser après quelques secondes et n'est pas
équivalent au backend Safari Mac. Cet essai est **non concluant**, pas une
certification Safari. Le résultat du contrôle natif réel doit rester distingué
de la recette physique sur le Mac.

Références primaires :
- [Événements média Apple](https://developer.apple.com/jp/documentation/webkitjs/htmlmediaelement)
- [Inspection des médias WebKit](https://webkit.org/blog/8923/debugging-media-in-web-inspector/)
- [Activation HLS GStreamer dans WebKit](https://github.com/WebKit/WebKit/blob/main/Source/WebCore/platform/graphics/gstreamer/GStreamerRegistryScanner.cpp)

## Recette Mac de 15 minutes

1. Garder le tunnel SSH privé habituel et se connecter avec le compte DEV existant.
2. Ouvrir `http://127.0.0.1:17010/?radioDiagnostics=1`, puis Cmd-Maj-R pour charger
   le nouveau lecteur. Activer le son une seule fois si nécessaire.
3. Écouter au moins 15 minutes ; naviguer vers À propos puis revenir à Radio.
   Le panneau « Diagnostic radio DEV » reste disponible dans la coque persistante.
4. Vérifier que currentTime progresse et que le nombre de détachements reste à 0
   hors pause volontaire. Des notifications stalled seules ne signifient pas une coupure.
5. Cliquer « Exporter le diagnostic » à la fin, ou immédiatement en cas de coupure.
   L'export comprend horodatages, tampon, événements média et état du lecteur,
   sans cookies, mot de passe, jeton CSRF ou clé d'accès.

L'API locale `window.diggerRadioDiagnostics()` permet aussi la lecture dans la
console Safari. Version du correctif : `safari-stability-2026-10-22`.
Aucun compte ou droit créé/modifié, aucune ouverture publique, BÊTA/production
inchangées, aucun commit ou push automatique.

## Résultats constatés

Avant : 46,395 s de HLS natif réel, 6 réinitialisations et 6 load().
Après : **900,664 s (15 min 0,664 s)** de lecture native réelle Chromium,
0 réinitialisation, 0 load(), 0 erreur média/HTTP/JS ; temps média en progression
à chaque échantillon (aucun échantillon figé sur 2 s). Sept éléments musicaux et
jingles détectés ; navigation aller-retour conservant le même élément audio.
113 notifications stalled ont été tolérées sans détruire la source. Les deux
waiting/playing initiaux sont liés au démarrage ; aucun cycle ensuite.
Tampon en avance d'au moins 4,696 s dans les échantillons.
Preuve : `instance/dev/safari-native-fixed/report.json`.

Contrôle hls.js **réel, non simulé** : morceau → jingle → morceau,
28 contrôles d'horloge audible et 224 vérifications d'historique, navigation et
récupération après interruption réseau de 5 s réussies.
Preuve : `instance/dev/safari-fix-hlsjs/report.json`.

Compléments ciblés : 23 tests de données LIVE et 26 tests interface/accès OK ;
test navigateur DOM (stalled, indisponibilité metadata, loadedmetadata répété,
auth expirée) OK ; régression déterministe de throttling des événements en onglet
arrière-plan, timer devenu obsolète, vraie source figée et 401 immédiat OK.
Le watchdog rééchantillonne également currentTime directement, sans dépendre
uniquement des timeupdate susceptibles d'être ralentis en arrière-plan.
Ces régressions simulées complètent le contrôle réel sans s'y substituer.
Les seuls ajouts après le démarrage du contrôle prolongé concernent les compteurs
d'export et cette protection complémentaire de rééchantillonnage, testée séparément.
La certification Safari/AVFoundation sur le Mac reste à effectuer avec l'export.


## Correctif métadonnées — 22 octobre 2026

Défaut reproduit sur le flux réel, avant rechargement web : une position native
8 s avant le début de la playlist glissante (6 segments, ~24 s) rendait
`available:true`, `metadata_basis:unavailable`, titre Synchronisation du direct,
artiste et prochain morceau nuls. Pour la même position, le correctif renvoie
Pull On Me / FGL, prochain Turn It Around V2, `metadata_basis:audible-clock`.
Ce constat prouve le défaut de fenêtre ; faute d'export provenant du Mac, il ne
prouve pas que cette cause explique tous les cas Safari possibles.

`dev_live_data.enrich` résout désormais toute position encore couverte par la
chronologie PCM de la session, jusqu'au dernier média publié, même si le segment
a quitté la playlist téléchargeable. Positions futures, antérieures à la session
retenue ou d'une ancienne session toujours refusées. Les frontières des éléments
émis sont renvoyées dans `playback_ended_at`. Aucun moteur ni média modifié.

Le lecteur conserve le chemin natif Safari. Aucune heure murale approximative
ne remplace getStartDate/currentTime. Les dates invalides restent explicitement
en attente et sont retentées. Les limites de chaque élément empêchent de garder
l'ancien titre pendant la réponse suivante. Une marge native de 0,6 s tient
compte de l'arrondi à la seconde d'AVFoundation :
https://github.com/WebKit/WebKit/blob/main/Source/WebCore/platform/graphics/avfoundation/objc/MediaPlayerPrivateAVFoundationObjC.mm
Le principal et le mini-lecteur partagent les mêmes données, titre, artiste et
pochette ; les jingles restent identifiés comme tels. Pas de detach/load/retry
lors d'un problème de métadonnées. Version diagnostic safari-metadata-2026-10-22.

Tests ciblés : 25 tests API LIVE, régressions JS de récupération et d'horloge
native/DOM réussies. Le contrôle réel Chromium/hls.js et le lien HTTPS utilisent
les réponses et le décodage réels ; rapports privés metadata-chromium et
mobile-demo/browser-report.json. Les régressions déterministes ne remplacent pas
le test Safari. WebKit Linux/GStreamer testé sans substitution d'horloge :
getStartDate invalide, 0 réponse audible-clock ; il ne valide pas AVFoundation.

Recette Mac : ouvrir le lien DEV habituel, recharger avec Cmd-Maj-R, activer le
son, vérifier titre/artiste/pochette sur les deux lecteurs et le prochain titre.
Observer morceau → jingle → morceau. Pour diagnostiquer un échec, ajouter
?radioDiagnostics=1 à l'URL après connexion (sans modifier le fragment du lien
invité initial), puis exporter. audibleAt, metadata.basis/status et bornes du
flux permettent de distinguer horloge absente et position hors session.

Résultat réel de ce correctif : 252,599 s, Turn It Around V2 → jingle → Jade,
128 réponses API recoupées avec la chronologie PCM et 243 échantillons DOM
(titre/artiste) vérifiés ; 1 attachement, 0 détachement, aucune erreur HTTP/média.
Un callback du harnais lisait encore une réponse pendant la fermeture finale :
protection de fermeture corrigée, puis contrôle réel ciblé 12,275 s/6 réponses
avec assertions DOM et sortie de commande réussie, sans répéter le long contrôle.
Lien HTTPS invité : 40,579 s, métadonnées affichées concordantes sur les deux
lecteurs, audio réel/navigation continus, HLS anonyme 401 et administration 403.

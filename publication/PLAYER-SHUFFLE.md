# PREVIEW : player simple, timing lu et shuffle

Préparation uniquement sur feature/digger-player-shuffle, base 75be859.
Aucune unité, aucun service, aucune DB réelle ni aucun secret modifié.

Le module d'analyse audio, sa référence HTML, ses styles et son test dédié sont
supprimés. Le player garde titre, artiste, cover, progression, temps, Play/Pause et
À suivre, dans cet ordre. Pas de bouton shuffle.

## Shuffle privé

ShuffleCycle mélange les tracks actifs déjà validés par private_radio.tracks.
Chaque track est consommé exactement une fois par cycle. Un nouveau mélange est
préparé dès que le suivant exige le cycle suivant. Si son premier track est le
dernier du cycle précédent, on échange sa place avec une autre position choisie
aléatoirement. Catalogue mono-morceau : répétition inévitable et explicitement
acceptée. Catalogue de deux morceaux : alternance. L'ordre n'est pas persisté ;
un redémarrage repart sur un nouveau mélange. Pas de garantie entre deux processus.

current et next_track sont fixés avant le décodage. advance() promeut exactement
next_track, puis détermine son suivant. Le futur emplacement du jingle est avant
advance(), sans refaire de tirage : aucun jingle n'est diffusé par cette version.
Le catalogue reste un snapshot sous le verrou existant, comme précédemment.

## Progression alignée sur le contenu lu

Le chemin historique public reste inchangé. Pour le moteur privé seulement :
- FFmpeg ajoute EXT-X-PROGRAM-DATE-TIME, sans changer codecs, segments ou watchdogs.
- Le premier buffer PCM d'un morceau porte son identité et son suivant déjà choisi.
- À sa consommation par la pompe, on mémorise son offset en secondes de PCM
  réellement transmis (silences compris), pas l'heure de lancement du décodeur.
- L'origine est le premier PROGRAM-DATE-TIME de la nouvelle playlist. Le repère
  du morceau est origine + offset PCM. Les derniers 128 repères sont conservés
  atomiquement dans hls/timeline.json pour résoudre un auditeur en retard.

Le navigateur utilise hls.js playingDate ou Safari getStartDate()+currentTime.
Il demande /preview-radio/now-playing?at=<date-de-la-position-lue> ; le serveur
renvoie seulement le morceau entendu et son vrai suivant, jamais le journal ou
la file. Les champs historiques restent compatibles sans paramètre at.
La réponse ajoute timing_available et, si résolue, playback_started_at.

Temps écoulé = position lue - playback_started_at, borné à [0, durée].
Restant = durée - écoulé. Le navigateur met à jour sur timeupdate et rafraîchit
les métadonnées toutes les secondes en PREVIEW (5 s pour le player historique).
La position est figée par le média pendant une pause ou un buffering ; aucune
horloge Date.now() ne fait artificiellement avancer le compteur. La transition
réinitialise le compteur avec le repère du nouveau morceau. Les métadonnées peuvent
se mettre à jour avec un délai de polling d'au plus environ une seconde hors réseau.

Sans correspondance temporelle fiable (ancien moteur, navigateur sans date HLS,
repère absent/ancien), les temps écoulé/restant sont --:-- et la barre est vide ;
la lecture reste possible. La durée connue reste affichée. L'arrêt efface les temps.

La précision est celle du HLS/AAC, pas de l'échantillon acoustique : découpage des
trames AAC, priming et délais de sortie matériels restent possibles. Safari réel
et Chromium réel sont à valider avant déploiement ; aucune promesse 24/7.

Références :
https://hlsjs.video-dev.org/api-docs/hls.js.hls.playingdate
https://html.spec.whatwg.org/multipage/media.html#dom-media-getstartdate

## Vérification

Les tests utilisent des DB temporaires et de l'audio FFmpeg synthétique. Ils couvrent
les cycles shuffle, 1/2/26 tracks, next engagé, la consommation PCM, les repères HLS,
le changement de morceau entendu malgré l'avance du moteur, les horloges invalides,
la pause/reprise et l'arrêt. Aucun déploiement effectué.

Résultat final : 445 tests Python en 66,148 s (444 réussis, 1 ignoré).
Les scripts Node test_preview_player.js, test_radio_player.js et test_local_login.js
passent ; syntaxe JS et git diff --check sans erreur. Validation navigateur réel
et endurance non effectuées.

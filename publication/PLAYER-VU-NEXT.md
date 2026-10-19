# Player PREVIEW : niveau audio et morceau suivant

Branche de préparation `feature/digger-player-vu-next`, base `0ac1cd7`.
Aucune installation d'unité, migration ou activation n'est réalisée par cette modification.

Le moteur privé conserve sa liste ordonnée par `position`, chargée sous son verrou
existant. Au début de chaque morceau, il écrit le suivant de cette même liste dans
`hls/now.json`. Il boucle au premier après le dernier ; avec un seul morceau, celui-ci
est aussi le suivant. L'arrêt écrit des champs nuls. Une erreur de validation continue
à arrêter le moteur selon les protections existantes.

La route authentifiée `/preview-radio/now-playing` conserve ses champs actuels.
Exemple avant :

```json
{"status":"running","available":true,"message":"Radio PREVIEW / TEST en cours.","track_id":1,"title":"Track 1","artist":"Artist","duration":180,"elapsed":12,"cover_url":"/preview-radio/tracks/1/cover"}
```

Après, les mêmes champs avec :

```json
{"next_track_id":2,"next_title":"Track 2","next_artist":"Artist","next_cover_url":"/preview-radio/tracks/2/cover"}
```

Ces quatre nouveaux champs valent `null` si aucun suivant valide n'est publié,
notamment avec un ancien moteur ou une radio arrêtée. La route vérifie l'éligibilité
privée du morceau suivant, sans publier la liste du catalogue. Aucun statut juridique
ni aucune autorisation n'est modifié.

Le navigateur rafraîchit toujours les métadonnées toutes les cinq secondes. Le prochain
morceau devient le courant quand le moteur publie la transition. Aucun morceau n'est
deviné côté navigateur. Ce repérage conserve la latence HLS existante : il ne prétend
pas synchroniser les pochettes à l'échantillon effectivement entendu.

Le VU initialise AudioContext uniquement dans le clic Play. Il analyse une capture
`captureStream`/`mozCaptureStream` de l'élément audio via MediaStreamAudioSourceNode,
ChannelSplitter et AnalyserNode (RMS, échelle -60 à 0 dBFS, environ 30 mises à jour/s).
Le volume/mute du lecteur est pris en compte. La branche d'analyse est muette ; la
sortie audible de l'élément n'est jamais reroutée. Pause, attente, erreur et arrêt
remettent les barres à zéro. Aucun signal synthétique n'est généré.

Sans capture ou Web Audio, le VU annonce son indisponibilité et la lecture continue.
C'est notamment le repli prévu pour Safari HLS natif sans capture : pas de promesse
que le VU fonctionne sur Safari réel. WebKit documente encore des limites pour
MediaElementAudioSourceNode et HLS natif : https://bugs.webkit.org/show_bug.cgi?id=306493
La lecture conserve les chemins HLS natif et hls.js existants.

Vérification : suite Python isolée, tests Node du VU, player PREVIEW, player historique
et login local. Les tests FFmpeg utilisent uniquement de l'audio synthétique temporaire.
Avant activation, vérifier Safari réel et Chromium : lecture, pause/reprise, perte du
flux, transition, et précision visuelle des canaux. Aucun test d'endurance 24/7 effectué.

Résultats de préparation : 438 tests Python en 68,992 s, 437 réussis et 1 ignoré ;
le test supplémentaire du catalogue mono-morceau passe séparément.
Les quatre scripts Node (audio_meter, preview_player, radio_player, local_login)
passent, ainsi que les contrôles de syntaxe JS et `git diff --check`.

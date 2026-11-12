# Jingles PREVIEW

Les MP3 de `jingles/JINGLLE` sous la racine privée sont inspectés sans import DB.
Seuls les MP3 locaux réguliers, sans symlink, validés avec le validateur audio
existant sont éligibles. Empreinte et taille sont revérifiées avant lecture.
Les autres fichiers, notamment .DS_Store, sont ignorés ; les MP3 invalides sont
listés comme invalides dans l'admin. Dossier absent/inaccessible : aucun jingle.
Le moteur prend un snapshot au démarrage, comme pour le catalogue.

Après un track terminé, un JinglePicker indépendant choisit un jingle valide,
sans répétition immédiate lorsque plusieurs sont disponibles. Le même encodeur
et la même pompe PCM continuent. Puis ShuffleCycle.advance() promeut exactement
le prochain track déjà annoncé. Aucun jingle n'est ajouté à private_tracks ou
private_plays et aucun tirage de track supplémentaire n'a lieu pour le jingle.
Un fichier changé/absent est ignoré ; une erreur de décodage inattendue reste
fail-closed, comme pour les morceaux. L'arrêt ne lance pas de nouveau jingle.

Pendant le jingle, now.json et les repères PCM publient type=jingle,
track_id=null, title=Digger Radio, artist=Station ID, durée réelle du MP3 et
next_track_id inchangé. L'API privée résout le repère entendu comme pour les
tracks, ajoute la cover Digger et les métadonnées du prochain morceau. Elle ne
publie ni chemin de fichier ni liste de lecture. Le player reconnaît type=jingle
sans exiger un track_id et conserve À suivre. Droits et autorisations inchangés.

/admin/preview-radio affiche les noms, durées et validité détectés. Pas d'action
upload, modification DB ou diffusion publique ajoutée.

Tests : fichiers temporaires, choix sans répétition, zéro/un jingle, MP3 invalide,
.DS_Store, symlink, HTML admin et transitions API/JS. Un vrai pipeline FFmpeg
synthétique vérifie track → jingle → track → jingle → track avec le même PID
encodeur, les repères PCM et le suivant inchangé. La suite existante vérifie le
mode sans jingles et la continuité HLS.

Pré-déploiement : 451 tests Python en 84,335 s, 450 réussis et 1 ignoré.
Trois suites JavaScript et git diff --check réussis.

# Catalogue PREVIEW éditorial

Branche feature/digger-editorial-catalogue ; base 6e00d33.
Aucune DB contributeur/juridique, aucun secret ni Nginx n'est concerné.

## Migration autorisée

Sauvegarde cohérente SQLite effectuée via Connection.backup avant modification :
/home/jean/digger/instance/demo/private-radio/backups/editorial-v2-20261020T005133Z/
- catalogue.sqlite : integrity_check=ok, user_version=1, 26 tracks, 20 lectures, 0 événement.
- baseline.json : champs des tracks et SHA-256 des 26 MP3.

La migration additive approuvée a été essayée sur une copie temporaire, puis
appliquée au vrai catalogue : user_version=2, genre vide non-null, year nullable
1000–9999, rejection_reason nullable (cinq codes autorisés), updated_at nullable.
Code réexécutable explicitement : preview_editorial.migrate(root). Aucune migration
automatique au démarrage du web ou du moteur. Vérification post-migration :
26 tracks et tous leurs anciens champs inchangés ; 20 lectures ; SHA-256 identiques.
La radio peut naturellement ajouter des lectures après ce constat.

## Gestion

/admin/preview-radio : lignes compactes, recherche titre/artiste insensible à la
casse, filtres Tous/Actifs/Retirés/Refusés, écoute privée et cover existantes.
Modifier ouvre le formulaire dans la ligne via :target, sans nouvelle dépendance JS.
Seuls titre/artiste/genre/année sont édités ; ni MP3 ni fournisseur ne sont écrits.
La cover reste celle intégrée au MP3 ou le logo Digger.

POST /admin/preview-radio/tracks/<id>/edit exige admin + CSRF et une révision valide.
Les confirmations GET /admin/preview-radio/tracks/<id>/confirm?action=remove|reject
ne modifient rien. Retirer/Refuser exige une confirmation explicite, et Refuser un
motif autorisé. Remettre est disponible pour les retirés ; un refus n'est pas levé
implicitement par cette action. Les corrections de métadonnées restent possibles
sur un track retiré/refusé. Toutes les actions journalisent avant/après, action,
track_id et date dans private_catalogue_events, dans la même transaction.

Statuts dérivés : refusé si motif ; sinon retiré si enabled=0 ou removed_at présent ;
sinon actif. enabled/removed_at sont synchronisés dans chaque transaction. Aucun
fichier supprimé, aucun track_id changé, aucune donnée réimportée.

## Lecture dynamique

Les actions éditoriales utilisent des transactions SQLite courtes et un contrôle
de révision, sans prendre le verrou de processus réservé au moteur. Le verrou
existant continue d'empêcher deux moteurs et les anciens imports concurrentiels.
Le lecteur relit l'éligibilité au plus toutes les 0,5 s pendant la lecture, et
explicitement aux frontières track/jingle et juste avant le prochain décodage.
Le courant ne change pas d'identité et n'est pas interrompu s'il est retiré/refusé.
L'API continue de le décrire et exclut immédiatement les prochains inéligibles.

ShuffleCycle.refresh retire les réservations inéligibles et conserve l'ordre des
réservations restantes ainsi que les IDs déjà tirés du cycle. Les tracks réactivés
ne sont ajoutés au cycle que s'ils n'y ont pas déjà été tirés. À cycle épuisé, un
nouveau mélange est créé, sans répétition immédiate si plusieurs choix existent.
Les métadonnées des choix conservés sont actualisées sans retirer un nouveau track.
Les repères PCM du courant et du jingle sont actualisés pour garder À suivre vrai.
Le choix et l'encodage des jingles ne changent pas.

Si aucun track ne reste éligible, le courant termine, puis le moteur s'arrête
proprement plutôt que de répéter un track exclu. Un track réactivé peut ensuite
être diffusé après Démarrer PREVIEW. Aucun re-démarrage automatique ajouté.

## Retour arrière

Conserver la sauvegarde et exporter les colonnes éditoriales + événements avant
un rollback de schéma. SQLite 3.45.1 permet DROP COLUMN sur ces quatre colonnes
puis user_version=1, dans une transaction validée sur copie. Cette opération ne
réécrit pas les anciennes lectures. Ne pas restaurer aveuglément l'ancienne DB :
cela supprimerait les lectures/événements apparus après la sauvegarde. Aucun
rollback ni DROP COLUMN automatique n'est exécuté par le code.

## Tests

DB temporaires : migration idempotente, préservation IDs/fichiers/historique,
contraintes année/motif, édition sous verrou moteur, statuts, révisions, audit,
auth/CSRF, confirmation, recherche/filtres, exclusion et remplacement du suivant.
FFmpeg synthétique : courant retiré et suivant refusé pendant la lecture, fin
naturelle du courant, même encodeur, jingle puis track de remplacement éligible.
Suite historique conservée pour HLS, jingles, Safari/hls.js et droits.

Résultats pré-déploiement : 459 tests Python en 94,874 s (458 réussis, 1 ignoré).
Trois suites Node passent ; git diff --check sans erreur.

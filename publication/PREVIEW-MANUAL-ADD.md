# Ajout manuel au catalogue PREVIEW

`/admin/preview-radio/new` ajoute directement un média au catalogue privé; il
ne crée ni submission ni dossier dans la base web. Les MP3/WAV sont limités
par `MEDIA_LIMITS`, identifiés et décodés entièrement par FFmpeg avant leur
publication. Les covers JPG/PNG sont décodées, normalisées sans métadonnées,
et stockées dans `private-radio/covers/`. SQLite reçoit le nom généré, le hash,
la durée, les métadonnées, le statut, les champs source et l’événement
`manual_admin_add` dans une transaction. Les fichiers publiés sont retirés si
la transaction échoue.

La confirmation demandée dans le formulaire concerne exclusivement l’écoute
privée PREVIEW/TEST. Elle ne vaut pas accord de diffusion publique ou validation
juridique. Le verrou final de validation juridique reste indépendant.

Le catalogue réel a été migré de `user_version=2` à `3` après une sauvegarde
SQLite cohérente et vérifiée. La sauvegarde et son manifeste sont sous
`instance/demo/private-radio/backups/manual-add-v3-20261020T012507Z/`.
La migration ajoute `cover_path`, `source_platform`, `source_url` et
`internal_note`; le contrôle SQL limite la plateforme aux valeurs prévues.
Les fichiers audio historiques restent dans leurs chemins actuels. Le worker
accepte les WAV décodables en plus des MP3 validés.

La source fournisseur reste dans ses colonnes dédiées. Les changements
éditoriaux et de cover mettent à jour la révision et écrivent avant/après dans
`private_catalogue_events`; remplacer une cover conserve l’ancien fichier afin
que l’historique ne pointe pas vers une ressource supprimée.

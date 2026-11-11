# Volet 01 — Editorial Underground — livraison DEV

Branche : `feature/volet01-editorial`, base `5e7c35e`.
Implémentation DEV uniquement ; aucun commit, push, déploiement ou contrôle de service.
Les changements préexistants (dont moteur, ancien lecteur et tests) sont préservés.

## Périmètre livré

- `editorial_web.py` : routes éditoriales et fiches privées, aucune écriture SQLite.
- `dev_web.py` : ajout final de l'installation éditoriale uniquement dans l'entrée DEV.
- `templates/editorial/{shell,content}.html` : accueil responsive, pages futures,
  présentation éditoriale, deux surfaces de commande pour un seul élément audio.
- `static/editorial/{editorial.css,player.js,navigation.js}` : design, états LIVE,
  autoplay et geste utilisateur, reprise au direct, navigation GET ciblée et historique.
- `static/editorial/digger-hero.png` : copie de la photographie originale fournie.
- `static/editorial/fonts/` : Barlow Condensed, Source Serif 4, Inter et licences OFL.
- `static/editorial/vendor/` : hls.js 1.7.3, copie locale et licence Apache 2.0.
- `tests/test_editorial_web.py`, `tests/export_editorial_preview.py`,
  `tests/editorial_browser.cjs`, `tests/editorial_audio_browser.cjs` : validation isolée.

Le moteur, ses services, les bases d'exploitation et les parcours de contribution
et d'administration ne sont pas modifiés. Les fiches restent authentifiées.
Les navigations de contribution/authentification/administration restent natives et
peuvent interrompre l'audio ; aucune interception de formulaire ou de route sensible.

## Données et limites assumées

Un seul prochain morceau réservé, aucune programmation inventée. Pas de badge 24/7,
aucun précédent/suivant, recherche temporelle ou fausse forme d'onde.
`private_plays` encadre le traitement par le moteur : ces dates ne démontrent pas
l'heure audible du passage HLS. L'historique affiche donc un état indisponible.
La progression n'apparaît que lorsque le temps audible est fourni par le navigateur.
Les pages Découvrir et Artistes sont explicitement en construction.
Le logo typographique blanc/orange utilise l'icône existante ; le logo officiel
conforme à la maquette, les photos des cartes éditoriales et des genres manquent.
Les panneaux restent typographiques, sans fausses photos d'artistes.
Les polices retenues sont locales, sous réserve de validation artistique.

## Vérifications réalisées

- Suite PREVIEW Flask : 24 tests OK.
- Suite éditoriale Flask (incluant les cas PREVIEW importés) : 22 tests OK.
- Isolation DEV : 9 tests OK ; outils DEV : 7 tests OK.
- Deux suites JS historiques : OK, HLS natif et hls.js simulés.
- Chromium : 320, 390, 768, 1024 et 1440 px sans débordement ; captures produites.
- Chromium avec médias simulés : autoplay bloqué, activation, reprise au bord direct,
  navigation/retour/liens directs, focus, audio unique, instance HLS unique,
  changement de volume, mute, reconnexion et expiration de session.
- Chromium avec vrai hls.js : AAC/HLS synthétique décodé, autoplay permis,
  continuité pendant la navigation et pause/reprise. Tous les HTTP sont interceptés ;
  aucun serveur et aucun moteur radio n'est lancé.
- Syntaxes Python/JS et `git diff --check` : OK.

Safari/iOS réel, écoute physique, validation artistique et recette utilisateur restent
à faire. Les tests ne constituent pas une approbation juridique ni un déploiement.

## Prévisualisation

Captures : `/tmp/digger-volet01-preview/home-{320,390,768,1024,1440}.png`.
Elles montrent un flux indisponible volontairement, sans fausse diffusion.
Pour reproduire les exports sans serveur :

```sh
.venv/bin/python -B tests/export_editorial_preview.py /tmp/digger-volet01-preview
DIGGER_PLAYWRIGHT=/tmp/digger-volet01-browser/node_modules/playwright node tests/editorial_browser.cjs
```

Le navigateur de test peut demander une autorisation d'exécution hors bac à sable.
La fixture audio du test réel est générée uniquement sous `/tmp` :

```sh
mkdir -p /tmp/digger-volet01-preview/audio
ffmpeg -hide_banner -loglevel error -f lavfi -i 'sine=frequency=330:sample_rate=44100:duration=24' -c:a aac -b:a 96k -f hls -hls_time 4 -hls_list_size 0 -hls_flags program_date_time -hls_playlist_type event /tmp/digger-volet01-preview/audio/stream.m3u8
DIGGER_PLAYWRIGHT=/tmp/digger-volet01-browser/node_modules/playwright node tests/editorial_audio_browser.cjs
```

Pour une recette interactive ultérieure, utiliser le lanceur web DEV existant,
uniquement après autorisation et vérification du port :
`.venv/bin/python -B -m dev_launcher web` (127.0.0.1:17010, tunnel SSH existant).
Ne pas redémarrer un processus déjà présent ni lancer de moteur pour regarder la page.
Un processus Python DEV déjà chargé ne prendra pas automatiquement les nouvelles routes.

## Retour arrière

Retirer seulement les trois lignes d'installation éditoriale ajoutées à la fin de
`dev_web.py` (commentaire, import, appel). Cela rétablit la sélection du template
historique au prochain lancement DEV autorisé. Les nouveaux fichiers peuvent rester
inertes. Aucun `git reset`, `git clean`, restauration globale ou rollback de base.
Ne pas toucher aux fichiers sources `digger-hero.png` et à la maquette originale.
Aucun commit automatique ; un éventuel commit nécessitera une autorisation distincte.

## Prochaine action

Valider les captures desktop/mobile, puis autoriser une recette interactive DEV
et Safari/iPhone sans intervention sur la radio BÊTA.

## Deuxième passe — finitions visuelles et ergonomiques

Sur la même branche, modifications limitées à `static/editorial/editorial.css`,
`templates/editorial/content.html` et aux assertions de `tests/editorial_browser.cjs`.
Le contrôleur audio, la navigation, le logo, les sources photo et les routes ne sont
pas modifiés. Aucun service ou moteur n'est lancé pour cette passe.

- À suivre vide : panneau ~95 px sur desktop, ~110 px sur mobile ; la rangée desktop
  complète se compacte aussi (~173 px à 1440 px), sans fausse programmation.
- Mini-lecteur : 64 px desktop, 62 px mobile hors éventuelle zone de sécurité.
  Espacement sous la page et marges de défilement réservent sa place ; les commandes
  conservent une cible tactile de 44 px. Titres longs tronqués sans débordement.
- Cartes : zones photographiques dédiées et décoratives `.editorial-photo`, prêtes
  à recevoir `--editorial-photo: url(...)` ; aucun visuel générique ajouté.
- Alignements, rayons, interlignages, marges et bordures harmonisés ; portrait et
  bandeau conservés. Le logo existant et son emplacement restent inchangés.
- Tests ciblés : suite Flask éditoriale (22 tests) et Chromium aux largeurs
  320/390/768/1024/1440 ; hauteur vide et mini-lecteur, absence de débordement,
  accès aux CTA et derniers liens par défilement, menu clavier, rendu des pochettes
  fournies par les métadonnées de fixture, continuité du lecteur et auth.
- Captures et mesures séparées de la première passe :
  `/tmp/digger-volet01-preview-pass02/home-1440.png`, `home-390.png`,
  `viewport-1440.png`, `viewport-390.png` et `layout.json`.
  Les captures montrent volontairement le flux indisponible, sans programmation.

Prochaine action : valider les nouvelles captures avant toute recette réelle.
Les photos éditoriales, le logo définitif et la validation Safari/iPhone réel
restent en attente. Aucun commit, push ou déploiement.

## Passe 03 — proportions et lisibilité

- `#content` passe de max-width 1536 px à 93 % de largeur, plafond 2560 px.
  Largeurs mesurées : 1786 px à 1920, 2381 px à 2560, 2560 px à 3440.
- Navigation 15–16 px, signature 11 px desktop, titres de cartes 30–34 px,
  descriptions 15 px, CTA 14–15 px ; cartes et espacements agrandis.
- Portrait, sources photo, contrôleur audio, navigation et droits inchangés.
- Tests : 22 tests Flask OK ; navigateur de 320 à 3440 px, largeur utile,
  tailles typographiques, absence de débordement et accès aux CTA par défilement.
- Le serveur DEV sert déjà les nouveaux styles (vérification HTTP), sans
  rechargement de processus nécessaire. Faire Cmd-Maj-R dans Safari.
- Captures : `/tmp/digger-volet01-preview-pass03/home-1440.png`, `home-1920.png`,
  `home-390.png` et versions `viewport-1440.png`, `viewport-390.png`.

Logo : deux extractions alpha via l'outil intégré imagegen ont été inspectées et
rejetées pour franges et pixels parasites. Aucun résultat généré n'est utilisé par
le site. Le symbole officiel existant reste affiché ; son carré blanc reste un
point non résolu. Il faut une source officielle transparente ou vectorielle pour
un résultat propre, sans redessin. Aucun traitement global des images n'a été fait.
Instruction d'édition : extraire uniquement le D orange et ses cinq barres à gauche
 de `static/digger-logo-horizontal.png`, conserver géométrie et ombrage, supprimer
texte/fond blanc/halo, PNG alpha sans pixels parasites. Source originale préservée.
Aucun commit, push, déploiement ou intervention sur les services radio.

## Accès et identification — DEV

`/connexion` présente les profils Auditeur, Artiste / Contributeur, Administrateur.
`/connexion/<profil>` utilise le formulaire JSON `/login` et le CSRF existants,
sans transmettre ni attribuer de rôle. `/login/local` redirige vers ce sélecteur
uniquement dans l'entrée DEV. Les autres entrées applicatives restent intactes.
Après authentification, `/mon-espace` utilise `g.current_user` relu en base pour
présenter le tableau de bord `/admin` ou `/submissions`. Aucun rôle auditeur créé ;
`/espace-auditeur` authentifié est explicitement en construction.
La déconnexion est le POST `/logout-form` existant avec CSRF.
Un seul accès Connexion (Mon espace après connexion) est visible à toutes largeurs.
La sélection et Mon espace participent à la coque persistante ; les formulaires
et la frontière d'authentification restent en navigation native pour renouveler
la session et le jeton CSRF. Leur franchissement peut interrompre l'audio.
Tests : suite éditoriale 25 tests OK (login réel sur fixtures, contributeur refusé
sur admin, admin autorisé, rétrogradation prise en compte, logout/CSRF), contrat JS
login existant et éditorial OK. Aucun moteur ou contrôle de service radio utilisé.

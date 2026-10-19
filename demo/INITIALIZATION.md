# Rapport d'initialisation contrôlée — 15 octobre 2026

Branche confirmée : `feature/digger-stage2-metadata`. HEAD inchangé : `6f0d976`.
`AGENTS.md` relu. Les fichiers de préparation `demo/` et le test démo étaient
déjà non suivis ; aucun fichier historique suivi n'a été modifié.

## Opérations réalisées

1. Vérification de tous les chemins de données et de l'absence de base existante.
   Aucun lien symbolique dans ces chemins ; toutes les destinations de données
   sont exclusivement sous `/home/jean/digger/instance/demo/`.
2. Création du runtime privé et d'un venv dédié Python 3.12.3 :
   `/home/jean/digger/instance/demo/venv/`. Le `.venv` existant n'a pas été modifié.
3. Installation des dépendances applicatives et Gunicorn dans ce seul venv.
   Le premier téléchargement a échoué dans le sandbox réseau ; la reprise
   autorisée hors sandbox a réussi. Aucune installation système ni cache pip.
4. Création exclusive de `/home/jean/.config/digger-demo/settings.json` : clé
   issue de `secrets.token_urlsafe(64)`, jamais affichée ni journalisée. Répertoire
   privé mode 0700, fichier mode 0600, propriété `jean`. Aucun secret existant
   remplacé et aucun fichier `.env` lu/modifié par la démo.
5. Initialisation explicite de la nouvelle base
   `/home/jean/digger/instance/demo/digger.sqlite`, réservée atomiquement avec
   `O_EXCL` : refus d'écrasement d'une base déjà présente. Base mode 0600.
6. Vérification SQLite en lecture seule : migrations 001, 002, 003 et checksums
   conformes aux ressources du dépôt, `integrity_check=ok`, aucune violation de
   clé étrangère. Les tables comptes, pistes, diffusions, propositions et preuves
   média sont vides. Seul le référentiel initial de genres et le registre des
   migrations sont présents.
7. MP3, covers et HLS : répertoires dédiés créés, encore vides, sous
   `instance/demo/storage/{tracks,meta,hls}/`. Temporaires d'installation sous
   `instance/demo/tmp/`. Aucun média copié, téléchargé ou lié.
8. Inventaire des versions installé dans `instance/demo/installed-packages.txt`,
   mode 0600 et ignoré par Git comme tout `instance/`.

## Compatibilité et contrôles

Versions principales : Python 3.12.3, Gunicorn 26.2.0, Flask 3.1.3,
Werkzeug 3.1.9, Authlib 1.9.1, Pillow 12.3.0, Mutagen 1.48.1.
`pip check` : aucun conflit. Gunicorn 26.2.0 est désormais fixé dans les
dépendances démo ; les versions transitives effectivement installées sont dans
l'inventaire privé mentionné ci-dessus.

Le parseur de configuration Gunicorn accepte tous les paramètres du lanceur,
dont `127.0.0.1:17000`, un worker synchrone, un thread, aucun daemon ni reload.
Ce contrôle n'a appelé ni `run()`, ni ouverture de socket, ni lancement de worker.
La compatibilité de configuration est vérifiée, pas le fonctionnement d'un
serveur réel ni sa consommation en charge.

Une seule exécution séquentielle, plafonnée à 60 secondes : **10 tests réussis
en 1,029 s**. Elle comprend les huit tests démo (isolation, droits, CSRF, admin,
refus des symlinks, refus de réinitialisation et refus de saisie non interactive)
et les deux tests de consolidation permission du stockage/nettoyage. Les tests
utilisent des bases et médias temporaires, jamais les données historiques.
Aucune suite complète lourde lancée.

Les protections juridiques restent inchangées : aucun document de production
validé, aucune contribution approuvée, aucun worker HLS démarré.

## Création du compte administrateur — à saisir par le propriétaire

Aucun compte ni mot de passe administrateur n'a été créé. Dans un terminal
interactif, depuis `/home/jean/digger` :

```bash
instance/demo/venv/bin/python -B -m demo.manage create-admin
```

Saisir l'email puis un mot de passe propre à la démo, d'au moins 16 caractères,
deux fois à l'invite masquée. Ne pas passer le mot de passe en argument,
variable d'environnement, pipe ou transcription. Le lanceur exige un terminal
interactif. Seul le hash est conservé en base ; l'email existant ne sera pas
écrasé. Ce compte est un compte de démonstration vérifié, sans envoi SMTP.

Lors d'un futur lancement autorisé derrière HTTPS, utiliser `/demo/login`,
puis `/admin` ; le compte ne remplace pas la protection d'accès globale Nginx.

## État final et prochaines étapes

- Runtime initialisé, sans processus applicatif persistant.
- Aucun démarrage Gunicorn ou FFmpeg/HLS, changement Nginx, base/média historique
  consulté ou modifié, intervention sur les processus tiers, commit ou push.
- Les sources nouvelles restent non commitées, conformément à la demande.
- Lancer la démo nécessite encore une autorisation explicite. Les instructions
  de démarrage et d'arrêt sont dans `demo/README.md`.
- Nico doit adapter et valider TLS et la configuration publique proposée,
  puis autoriser l'ouverture Internet. HLS reste une étape indépendante soumise
  à validation des droits et du catalogue démo.
- L'isolation des données est démontrée par la configuration et les contrôles de
  chemins ; ce n'est pas une isolation OS contre du code malveillant exécuté
  sous le même utilisateur. Aucun service système n'est installé ou activé.

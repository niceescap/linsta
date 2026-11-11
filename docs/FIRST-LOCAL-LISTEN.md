# Première écoute privée dev — 10 à 15 minutes

Préparation faite : venv `.venv` indépendant (Python 3.12, sans system-site-packages),
dépendances installées, `pip check` et validation Gunicorn sans démarrage réussis.
Trois MP3 synthétiques locaux (~20 s chacun) sont admis dans le catalogue privé v3.
Aucun serveur/moteur n'a été démarré. Ne pas relancer le seed sur ce catalogue rempli.

1. Dans un terminal SSH interactif sur le serveur :

```sh
cd /home/jean/digger-dev
.venv/bin/python -B -m dev_manage
```

Choisir un mot de passe neuf (16 caractères minimum), masqué et confirmé deux fois.
Compte : `admin@digger-dev.invalid`. Le mot de passe ne doit jamais être communiqué
à Codex, enregistré dans un fichier, une commande ou un journal. Seul le hash reste
localement dans la base dev ignorée par Git. Les doublons sont refusés.

2. Terminal SSH A sur le serveur (laisser ouvert) :

```sh
cd /home/jean/digger-dev
.venv/bin/python -B -m dev_launcher web
```

3. Terminal SSH B sur le serveur (session continue bornée extérieurement à 15 min) :

```sh
cd /home/jean/digger-dev
timeout --signal=TERM --kill-after=30s 15m .venv/bin/python -B -m dev_launcher radio --mode continuous
```

Le mode continuous reste sans limite interne d'une heure ; timeout borne seulement
cet essai. Ctrl-C dans B permet d'arrêter plus tôt. À l'expiration, le code 124 de
timeout est normal. Aucun redémarrage automatique après erreur.

4. Sur le Mac, troisième terminal (remplacer la cible par votre accès SSH habituel) :

```sh
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:17010:127.0.0.1:17010 jean@VOTRE_SERVEUR
```

Ouvrir `http://127.0.0.1:17010/login/local`, se connecter avec le compte dev,
puis revenir à l'accueil `/` et appuyer sur lecture. Conserver le port local 17010.
Attendre environ 10 secondes après le lancement radio pour la première playlist.
Les boutons démarrer/arrêter de l'administration restent désactivés ; utiliser B.

Pendant 10 à 15 min : vérifier plusieurs transitions, fermer puis rouvrir l'onglet,
vérifier le retour au live et l'absence d'arrêt du moteur dans B.
Terminer par Ctrl-C dans B puis A, et fermer le tunnel. Ne jamais utiliser systemctl.
Aucun accès par le site bêta, aucun port public, aucun catalogue réel n'est utilisé.

Validation préalable : 16 tests dev avec client Flask simulé (lecture HLS privée,
CSRF, connexion, déconnexion), tests JS et `gunicorn --check-config` réussis dans
le nouveau venv. L'écoute navigateur et le HLS de cette session réelle restent à
valider après lancement manuel ; aucun flux n'est actuellement généré.

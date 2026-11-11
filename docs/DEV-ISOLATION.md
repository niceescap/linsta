# Développement isolé

Entrée WSGI : `dev_web:application`, hôte exclusivement `127.0.0.1`, port 17010.
Ne pas utiliser `app.py`, `digger_web.py`, les lanceurs demo ou les unités systemd.
Aucun serveur ni moteur n'est lancé par cette préparation.

Préparation explicite hors ligne, dans un processus Python neuf :
`python -B -c 'import dev_runtime; dev_runtime.prepare()'`.
Elle refuse une racine existante et crée uniquement sous
`/home/jean/digger-dev/instance/dev` : bases neuves sans comptes, secret neuf,
répertoires privés et WAV synthétique non admis à la diffusion.
Aucun secret, compte, média ou catalogue de production n'est importé.
Les comptes de test devront être créés explicitement dans la base dev.

Le contrôleur web dev refuse toutes les commandes : aucun systemctl ou PID
externe pilotable. Le lanceur local se commande uniquement depuis son terminal.
La limite historique de 3600 secondes et les contrôles juridiques sont conservés.
Ne pas admettre le WAV comme morceau diffusable : préparer ultérieurement des
MP3 synthétiques, puis appliquer la validation privée explicite habituelle.

Le cookie distinct est HTTP-only et SameSite=Lax, sans Secure pour HTTP localhost.
OAuth, inscription et vérification email sont fermés ; HLS public reste fermé,
HLS preview et administration conservent authentification, rôles et CSRF.
Les chemins avec symlinks, hardlinks de fichiers ou droits non privés sont refusés.
Cette protection applicative ne constitue pas un cloisonnement OS : le compte
Unix partagé peut toujours accéder à la production avec d'autres commandes.

## Utilisation locale préparée, pas encore démarrée

Créer le compte avec un terminal interactif : `python -B -m dev_manage`.
Identifiant exclusivement dev : `admin@digger-dev.invalid`. Choisir un mot de passe
neuf (16 caractères minimum), saisi masqué deux fois. Aucun mot de passe en argument,
en variable d'environnement ou dans Git ; seul son hash reste dans la SQLite dev.
La commande refuse les doublons ; elle ne réinitialise jamais un compte existant.

Commandes prévues pour une prochaine étape autorisée :
`python -B -m dev_launcher web` et `python -B -m dev_launcher radio --seconds 60`.
Ne pas les lancer pendant la préparation. Chaque superviseur reste au premier plan,
verrouille son type de lancement et ne signale que son propre enfant Popen.
Arrêt par Ctrl-C dans son terminal ; aucune commande stop/PID/systemd n'est acceptée.
Les boutons web restent désactivés. Le contrôleur lit le verrou et le marqueur
du moteur local ; le lecteur peut lire le HLS authentifié pendant son activité.
La durée radio reste bornée à 3600 secondes. Le catalogue doit contenir des MP3
synthétiques validés explicitement avant écoute ; le WAV de fixture reste non admis.
Prévoir un Python/venv propre au dev avec les dépendances de requirements.txt et
Gunicorn (demo/requirements.txt), avant le premier lancement.
Pour un accès distant, tunnel SSH vers localhost:17010 ; aucun changement Nginx.

## Mode continu distinct (développement uniquement)

Commande : `python -B -m dev_launcher radio --mode continuous`.
La preview reste le mode par défaut et garde sa limite de 3600 secondes.
Le mode continu utilise le même catalogue actif, les mêmes validations et la
même horloge PCM ; aucun timer d'une heure et aucune unité systemd ne sont utilisés.
Il reste indépendant des connexions du navigateur. Ctrl-C dans le terminal du
lanceur arrête le moteur ; fermer le navigateur ne le commande jamais.

La supervision HLS existante arrête sur absence/progression figée (20 secondes),
encodeur mort, source durablement inactive ou décodeur défaillant. La première
 erreur fatale arrête et journalise le type d'erreur : aucun redémarrage automatique,
donc aucune boucle de relances après erreurs répétées. Corriger la cause avant
une nouvelle commande manuelle. Le marqueur devient `failed` ou `stopped`.
Le lanceur nettoie uniquement le groupe de processus qu'il a créé ; le moteur
surveille également la disparition de son parent. Un arrêt brutal de toute la
machine ne peut évidemment pas exécuter les traitements de fermeture.

La playlist conserve 6 segments, FFmpeg supprime les anciens (7 à 8 segments
transitoires possibles) ; les temporaires exacts abandonnés sont nettoyés au
prochain départ continu sous verrou, puis le nettoyage HLS habituel est appliqué.
Le lecteur rejoint le live à la connexion/reprise ; HLS.js limite aussi le retard.
Aucun changement dans le service preview ou RuntimeMaxSec en production.

Validation : horloge simulée au-delà de deux heures ; 16 lectures synthétiques
FFmpeg avec encodeur unique sur environ 40 secondes ; arrêt, erreurs, absence
de relance, fenêtre HLS et clients Flask simulés. Aucune écoute navigateur réelle
ni endurance de plusieurs heures réelles n'a encore été effectuée.

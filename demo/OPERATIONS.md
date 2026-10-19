# Ouverture HTTPS et gestion du web démo — proposition à valider

Branche `feature/digger-stage2-metadata`, HEAD `6f0d976`. Ce document et l'unité
`demo/digger-demo.service` sont préparés dans le workspace, sans installation,
activation ni démarrage d'un service. Aucun changement au Nginx du serveur.

## État vérifié

Le démarrage temporaire précédent a confirmé le bind exclusif `127.0.0.1:17000`,
un worker synchrone, les réponses 200 de l'accueil, connexion démo et état radio,
les refus anonymes 401 sur admin/propositions, le refus OAuth 403 et HLS 404.
Master et worker ont été arrêtés ; aucun processus n'est maintenu par ce guide.
Le compte administrateur réel a été créé par le propriétaire ; son mot de passe
n'a pas été utilisé lors de ce contrôle HTTP.

La revue des nouvelles sources confirme la configuration isolée, les cookies
Secure, HTTP-only et SameSite Lax, le CSRF existant, le secret hors Git et le
blocage juridique des nouvelles contributions. Les secrets fictifs des tests et
le placeholder JSON sont des données de test, pas des identifiants réels.

## Configuration à remettre à Nico

Suivre désormais [la procédure HTTP ACME webroot puis Certbot certonly](NGINX-CERTBOT.md).
`demo/nginx.conf.example` sert seulement les challenges depuis `/var/www/certbot`,
sans upstream applicatif ni certificat. `certonly --webroot` émet le certificat
sans configurer Nginx ; Nico installe ensuite le modèle HTTPS final. `demo/nginx.https.conf.example` est la version finale publique à adapter
et valider ensuite ; elle remplace le bootstrap, jamais en parallèle.
Ces fichiers s'incluent au niveau `http` et ne contiennent aucun secret.
Les points suivants décrivent uniquement la version HTTPS finale.

- Nom public : `dig.hqwi.re`, upstream : `http://127.0.0.1:17000`.
- Certificat et clé proposés : `/etc/letsencrypt/live/dig.hqwi.re/{fullchain,privkey}.pem`.
  Nico doit confirmer ou adapter ces chemins et ses options TLS/ACME.
- Aucune Basic Auth globale ; `auth_basic off` annule un éventuel héritage.
  L’accueil et le lecteur sont publics ; admin et espaces privés restent sous
  authentification applicative, contrôle du rôle et contrôle de propriété.
- `/login` est limité à 5 requêtes/minute/IP, burst de 3, dépassement HTTP 429.
  Nico vérifie le nom de zone dédié et la gestion d’IP réelle sur son infrastructure.
- Les forwarded headers sont remplacés pour correspondre au `ProxyFix` existant.
  Le cache proxy est désactivé ; les cookies restent Secure, HTTP-only, SameSite Lax.
- Hors challenges ACME HTTP, les pages et assets passent par l’application ; aucun accès Nginx
  aux répertoires privés ni besoin de modifier leurs permissions.
- HLS renvoie 404 sans contenu, même si le reste du site est ouvert ; les chemins
  cachés sont refusés. Ces réponses ne contiennent aucune ressource. Aucun alias média ni worker HLS.
- Uploads : 16 Mio maximum, cohérent avec Flask ; vérifier le stockage temporaire
  Nginx et les timeouts pour les connexions lentes. Le décodage garde ses limites.
- Les journaux d'accès de ces vhosts sont désactivés ; les erreurs héritent de la
  politique Nginx. Ne pas activer de logs de corps, Authorization ou cookies.
- Si le DNS publie aussi une IPv6, Nico doit prévoir les listeners IPv6 et la
  bonne destination AAAA ; rien ne suppose qu'ils existent sur cette machine.
- Si un autre proxy/CDN termine TLS, adapter cette proposition aux sauts réels
  sans ouvrir le backend ni faire confiance aux en-têtes arbitraires du client.
- Le bloc HTTP final sert toujours les challenges webroot et redirige les autres
  chemins vers HTTPS ; avant Certbot, le bootstrap renvoie 404 hors challenges. Ne pas lancer un client ACME standalone qui
  monopoliserait les ports 80/443 du serveur partagé.

## Maintenir Gunicorn : unité systemd utilisateur dédiée

Préférer l'unité **utilisateur** `digger-demo.service` à nohup, aux signaux par
recherche de nom ou à une seconde session CLI. Elle exécute le lanceur existant
au premier plan et ne lance aucun worker HLS. Aucun secret n'est dans l'unité.

Le master est suivi directement, car le lanceur fait `exec` de Gunicorn.
Un arrêt envoie SIGTERM au master ; les requêtes disposent du délai Gunicorn
de 15 s. Après 25 s au maximum, systemd nettoie les processus restants **du seul
cgroup démo** (`KillMode=mixed`). Les autres services ne sont pas visés.
Un `restart` arrête avant de relancer ; ne pas démarrer un second Gunicorn à la main.
Le verrou démo existant reste un garde-fou supplémentaire.

Reprise après échec : délai de 10 s, au plus trois démarrages en 120 s.
Un `stop` explicite ne déclenche pas de redémarrage automatique. Une application
qui quitte proprement n'est pas redémarrée par `Restart=on-failure`.

Limites proposées : un worker et un thread, CPU 50 % d'un cœur, RAM cible 512 Mio,
plafond de **l'ensemble du cgroup** 768 Mio, 16 tâches et 256 descripteurs.
La limite mémoire virtuelle du lanceur reste 768 Mio par processus.
Les quotas de cgroup utilisateur demandent des contrôleurs délégués : Nico doit
confirmer leur application effective. Ce n'est pas un sandbox de fichiers.
Nice=10 dans l'unité et le +10 du lanceur peuvent donner nice=19 : priorité
volontairement basse, à observer lors de la première mise en service.

Les commandes suivantes sont **à exécuter uniquement après accord**, sous `jean`,
avec le gestionnaire systemd utilisateur accessible. Ne pas employer `sudo
systemctl` pour les commandes utilisateur, ni toucher aux unités des autres comptes.

Première installation autorisée : copier l'unité dans
`/home/jean/.config/systemd/user/digger-demo.service` après vérification qu'aucune
unité du même nom n'existe déjà. Comparer toute unité existante au lieu de l'écraser.
Puis :

```bash
systemd-analyze --user verify /home/jean/.config/systemd/user/digger-demo.service
systemctl --user daemon-reload
systemctl --user start digger-demo.service
systemctl --user status digger-demo.service --no-pager
```

`daemon-reload` recharge les définitions du compte, sans redémarrer ses services.
Avant `start`, vérifier qu'aucun lancement manuel n'occupe 17000 ou le verrou.
Ne jamais arrêter un processus inconnu pour libérer le port.

Gestion du seul service démo :

```bash
systemctl --user restart digger-demo.service
systemctl --user stop digger-demo.service
journalctl --user -u digger-demo.service -n 50 --no-pager
```

Après arrêt, confirmer l'état inactif, l'absence d'écoute sur 17000 et la libération
du verrou. Si la limite de redémarrage est atteinte, diagnostiquer les journaux
avant de faire, explicitement, `systemctl --user reset-failed digger-demo.service`.
Ne pas supprimer la base, les médias ou le secret pour résoudre un échec.

Maintien après fermeture de session et redémarrage machine : vérifier avec Nico
la politique du gestionnaire utilisateur et le linger du compte `jean`.
`systemctl --user enable digger-demo.service` et `loginctl enable-linger jean`
sont deux actions supplémentaires à autoriser ; aucune n'a été réalisée ici.
Le linger maintient le gestionnaire du compte et peut concerner ses autres unités :
ne pas l'activer ou le désactiver sans revue de cette portée avec Nico.

## Ordre proposé pour l’ouverture publique HTTPS

1. Suivre `NGINX-CERTBOT.md` : HTTP ACME webroot, contrôle, Certbot certonly webroot, installation manuelle du HTTPS.
2. Relire la configuration HTTPS finale publique, les restrictions de fichiers et
   la limitation de connexion. Aucun fichier htpasswd n’est requis.
3. Après accord séparé, installer/démarrer le seul service web et vérifier le local.
4. Nico vérifie `nginx -t` puis recharge selon sa procédure ; aucun autre vhost édité.
5. Vérifier accueil et connexion démo 200 sans mot de passe Nginx, admin et
   propositions 401 sans session, rôle contributeur 403 pour admin.
6. Le propriétaire vérifie la connexion applicative HTTPS, cookies Secure et CSRF.
7. Confirmer HLS, covers publiques, SQLite et chemins de stockage fermés ;
   inscription et OAuth désactivés ; port 17000 uniquement local.

Le service persistant et le HTTPS final n’ont pas été démarrés/testés réellement.
Le contrôle des routes par code et tests ciblés est décrit dans `PUBLIC-SECURITY.md`.
Le précédent test local et la vérification statique de l’unité restent documentés.
Nico doit valider ses certificats et règles héritées avant recharge. Si le backend
est arrêté, le public peut recevoir 502 : ne jamais basculer vers Linsta.

Références officielles :
[systemd service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html),
[systemd kill](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html).

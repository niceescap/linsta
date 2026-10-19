# Procédure unique Nico : HTTP ACME webroot, puis HTTPS public

Fichiers préparés uniquement sur `feature/digger-stage2-metadata`. Aucun Nginx
réel modifié, aucun Certbot ou service lancé, aucun commit/push/merge.

## À confirmer par Nico avant installation

- DNS A et éventuel AAAA vers noe ; port 80 accessible depuis Internet.
- Conventions réelles `sites-available`/`sites-enabled` et absence d’un vhost
  `dig.hqwi.re` existant. Aucun autre vhost ou lien ne doit être remplacé.
- `/var/www/certbot` est le webroot choisi et ses fichiers sont lisibles par
  Nginx ; aucun lien symbolique dans le chemin des challenges.
- Certbot déjà disponible et politique de renouvellement coordonnée ; examiner
  ses hooks existants avant émission pour éviter une action globale inattendue.
- Dans la version finale : nom de zone `digger_demo_login` libre, chemins réels
  du certificat, conventions/includes TLS, IPv6 et éventuel proxy/CDN. Les
  modèles n’ajoutent pas de listener IPv6 sans confirmation. Si AAAA existe,
  ajouter les listeners IPv6 adéquats dans les deux étapes.

## 1. Installer HTTP avec le webroot ACME

Fichier complet : `demo/nginx.conf.example`. Il sert seulement les fichiers
`/.well-known/acme-challenge/` depuis `/var/www/certbot` ; tout autre chemin
retourne 404. Aucun proxy applicatif, login, admin ou certificat requis.
Pas de listing, pas de suivi de symlink, lecture GET/HEAD seulement.

Commandes proposées à Nico, non exécutées ici :

```bash
# Vérifier les destinations existantes avant toute copie.
sudo ls -ld /etc/nginx/sites-available/dig.hqwi.re /etc/nginx/sites-enabled/dig.hqwi.re
# Créer seulement les répertoires manquants ; si présents, vérifier leurs droits
# sans modifier récursivement un webroot qui pourrait être partagé.
sudo mkdir -p /var/www/certbot/.well-known/acme-challenge

# Seulement si les destinations du vhost sont absentes :
sudo install -m 0644 /home/jean/digger/demo/nginx.conf.example /etc/nginx/sites-available/dig.hqwi.re
sudo ln -s /etc/nginx/sites-available/dig.hqwi.re /etc/nginx/sites-enabled/dig.hqwi.re
sudo nginx -t
# Seulement si le contrôle réussit :
sudo systemctl reload nginx
```

Nico doit confirmer que le compte worker Nginx peut traverser le webroot et lire
les fichiers ACME ; pour de nouveaux répertoires root-owned, 0755 convient.
Ne pas appliquer de chmod/chown récursif au webroot partagé.

Avant Certbot, déposer un fichier de test public au nom unique et non secret dans
ce répertoire, vérifier son contenu par HTTP depuis l’extérieur, puis enlever
uniquement ce fichier. Exemple de vérification (NOM_TEST choisi sans collision) :

```bash
curl --max-time 5 --fail http://dig.hqwi.re/.well-known/acme-challenge/NOM_TEST
curl --max-time 5 -sS -o /dev/null -w '%{http_code}\n' http://dig.hqwi.re/admin
```

Attendre 200 et le contenu du fichier test, puis 404 sur `/admin`, `/demo/login`
et `/`. Aucune saisie de credentials sur HTTP. Si un contrôle échoue, corriger
le seul nouveau vhost avant de poursuivre, sans arrêter Nginx.

## 2. Obtenir le seul certificat Digger, sans installateur Nginx

```bash
sudo certbot certonly --webroot -w /var/www/certbot -d dig.hqwi.re
```

Cette commande obtient le certificat et enregistre le mode webroot de renouvellement.
Elle ne crée pas de bloc HTTPS et ne configure pas le vhost. Ne pas employer
`--nginx`, `--standalone`, `--expand`, `--force-renewal` ni arrêter les services.
Si un certificat de ce nom existe déjà, Nico examine sa portée avant toute action.
Le HTTP applicatif reste fermé après cette étape. Aucun port 443 n’est requis
pour le challenge HTTP-01.

## 3. Installer HTTPS public après émission et revue

Fichier complet : `demo/nginx.https.conf.example`. Il **remplace** le bootstrap,
il ne s’ajoute pas en parallèle. Adapter les chemins réellement annoncés par
Certbot (un suffixe `-0001` est possible) et les options TLS de Nico ; ne pas
référencer un certificat/include inexistant. Le bloc HTTP conserve le webroot
ACME sans redirection pour les renouvellements ; les autres requêtes HTTP
redirigent vers HTTPS. Ne pas déplacer cette redirection au niveau serveur.

Accueil, lecteur démo sans live et page de connexion : publics sans Basic Auth.
Admin, propositions, MP3 et preuves : authentification applicative et contrôles
rôle/propriété. Stockage brut, SQLite, covers publiques et HLS fermés. Inscription
et Google OAuth désactivés. Pas de WebSocket, cache proxy désactivé, en-têtes
forwarded remplacés pour le ProxyFix Flask. `/login` limité à 5 requêtes/min/IP,
burst 3, dépassement 429. Si un proxy/CDN est présent, valider l’IP réelle.

Nico sauvegarde le seul vhost démo hors des répertoires inclus, puis remplace
son contenu par la version HTTPS adaptée. Les autres vhosts restent inchangés.
Si les chemins du modèle sont corrects et la revue terminée :

```bash
# Sauvegarde hors des includes, sans écraser une sauvegarde existante.
sudo cp --no-clobber /etc/nginx/sites-available/dig.hqwi.re /var/backups/dig.hqwi.re.before-https
sudo install -m 0644 /home/jean/digger/demo/nginx.https.conf.example /etc/nginx/sites-available/dig.hqwi.re
```

```bash
sudo nginx -t
# Seulement si le contrôle réussit :
sudo systemctl reload nginx
```

La recharge est globale et gracieuse : Nico coordonne les changements en cours.
Aucun restart/stop Nginx. Le backend reste arrêté tant que son démarrage séparé
n’est pas autorisé ; les routes proxy peuvent donc répondre 502 jusque-là.

Après démarrage web séparément autorisé :

```bash
# 200 sans credentials Nginx :
curl --max-time 5 -sS -o /dev/null -w '%{http_code}\n' https://dig.hqwi.re/
curl --max-time 5 -sS -o /dev/null -w '%{http_code}\n' https://dig.hqwi.re/demo/login
# 401 sans session applicative :
curl --max-time 5 -sS -o /dev/null -w '%{http_code}\n' https://dig.hqwi.re/admin
curl --max-time 5 -sS -o /dev/null -w '%{http_code}\n' https://dig.hqwi.re/submissions
```

Vérifier certificat valide (sans `-k`), redirection HTTP hors ACME, challenge HTTP
toujours servi, login/admin/CSRF dans le navigateur, chemins privés et HLS fermés,
et 17000 non exposé publiquement. Voir `PUBLIC-SECURITY.md` pour les tests de routes.

## Renouvellement ciblé

Nico doit conserver le mode webroot et prévoir le rechargement gracieux de Nginx
après renouvellement effectif, avec `nginx -t` préalable, selon ses hooks habituels.
Ne pas ajouter un second timer/cron global ni changer les hooks des autres
certificats. En mode `certonly --webroot`, le certificat sur disque est renouvelé
mais Nginx doit être rechargé pour l’utiliser. Un deploy-hook peut être configuré
pour ce seul certificat après revue et autorisation.

Test ultérieur, à coordonner par Nico :

```bash
sudo certbot renew --cert-name NOM_REEL_DU_CERTIFICAT --dry-run
```

Examiner les hooks existants avant ce test ; il ne doit cibler que le certificat
Digger. Aucun renouvellement ou hook n’a été exécuté par l’agent.

Référence : [Certbot webroot et renouvellement](https://eff-certbot.readthedocs.io/en/stable/using.html#webroot).

## Contrôles réalisés sans mise en service

Les deux modèles ont passé `nginx -t` sur des copies isolées sous
`instance/demo/tmp/`, avec des sockets Unix temporaires à la place des ports
80/443 et sans chargement de certificat TLS. Aucun Nginx lancé en service,
aucun appel à la configuration active. Les directives ACME, proxy, restrictions
et limitation de connexion ont ainsi été parsées par Nginx ; les certificats,
écoutes TCP/IPv6 et règles héritées restent à valider par Nico avec les vrais
fichiers et `sudo nginx -t` après intégration.

Six tests de routes/confidentialité réussis en 3,332 s, séquentiellement sous une
limite de 60 s : visiteurs sur toutes les routes, contributeur refusé sur admin,
fichiers/covers legacy privés, absence de données personnelles publiques, médias
et preuves réservés au propriétaire/admin, accès d’un autre contributeur refusé.
Aucune suite complète, aucun réseau HTTP ni donnée historique utilisés.

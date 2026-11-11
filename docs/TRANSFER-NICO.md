# Transfert Digger Radio DEV — 11 novembre 2026

## Copie à transférer

Branche locale : `feature/transfert-nico-20261111`, issue de `5e7c35e`.
Le seul distant configuré est `git@github.com:niceescap/linsta.git`.
L'API GitHub le déclare public : aucun push autorisé vers cette destination.
Attendre l'URL d'un dépôt privé, la confirmation de son propriétaire et les
permissions d'écriture avant tout push. Ne pas modifier main ni la production.

Ce transfert conserve l'interface Editorial Underground, les sources graphiques
et le logo transparent exact, l'administration existante, le moteur continu,
les outils DEV, les tests et les diagnostics Safari. La protection de la
sentinelle currentTime est incluse ; sa recette Safari Mac reste à effectuer.
Le partage mobile existant utilise une échéance absolue de 24 h, révocable.
Aucun jeton ni lien privé actif ne doit être placé dans le dépôt.

## Sauvegarde locale préalable

Dans `instance/dev/incidents/code-transfer-backup-20261111T150558Z/` :
- `workspace-code.tar.gz` : les 199 fichiers présents avant préparation ;
- `workspace-manifest.json` : tailles et SHA-256 de chaque fichier ;
- `history.bundle` : historique et références Git, bundle vérifié ;
- `local-changes.patch`, état Git et références au moment de la sauvegarde ;
- `SHA256SUMS` : empreintes des artefacts.

Le répertoire est privé et exclu de Git. L'archive du workspace a été comparée
fichier par fichier avec les sources. Il ne s'agit pas d'une sauvegarde des
bases, secrets ni musiques ; ceux-ci restent intacts sur le serveur actuel.
Une copie hors de ce serveur sera à organiser par un canal privé autorisé.

## Informations à obtenir avant installation chez Nico

- Cible SSH, port, utilisateur, accès par clé publique et droits disponibles.
  Ne jamais transmettre une clé privée ou un mot de passe dans le dépôt.
- Distribution/version, architecture, CPU, RAM, swap, disque/inodes disponibles.
- Services et conteneurs présents, ports occupés, contraintes d'exploitation.
- Chemin du projet, propriétaire Unix et répertoires privés des données.
- Domaine DEV, DNS, TLS, mode d'accès privé et reverse proxy envisagé.
- Volume du catalogue futur, nombre d'auditeurs simultanés et débit sortant.
- Politique de sauvegarde/restauration et séparation DEV/production.

Aucune connexion ni installation sur le serveur de Nico n'a été effectuée.
Aucun service ne doit y être déployé avant confirmation de cette configuration.

## Contraintes de portabilité à traiter après confirmation

Le DEV actuel dépend explicitement de `/home/jean/digger-dev` : notamment
`dev_runtime.py`, `dev_demo_gateway.py`, `dev_gunicorn.py` et plusieurs tests.
Le binaire de tunnel et Playwright sont attendus sous `/tmp` : ils ne font pas
partie du dépôt. Ne pas copier aveuglément ces chemins ni lancer les scripts
sur une machine qui ne respecte pas les chemins et propriétaires attendus.

Versions constatées : Python 3.12.3, FFmpeg 6.1.1, Node 24.17.0.
Dépendances déclarées dans `requirements.txt` et `demo/requirements.txt` ;
Playwright et son navigateur sont nécessaires aux seuls contrôles navigateur.
Les ports DEV actuels sont 17010 (web loopback) et 17011 (passerelle temporaire).
Le lanceur officiel est `dev_launcher` ; aucune nouvelle infrastructure créée.

Le catalogue privé actuel représente environ 173 Mio avec ses données annexes.
La fenêtre HLS est glissante ; le stockage doit aussi prévoir les imports,
archives, journaux et sauvegardes. Le nombre d'auditeurs et le débit AAC 128 kbit/s
permettront de dimensionner le réseau, avec marge pour le transport et les pics.

Un clone Git seul ne reproduit pas les 26 morceaux, 3 jingles, pochettes privées,
comptes, bases et droits existants. Prévoir ensuite un transfert privé autorisé,
cohérent pour SQLite, sans suppression ni déplacement des originaux. Les secrets
et comptes DEV de destination seront distincts. Ne pas lancer `prepare()` sur
une racine déjà préparée et ne pas réinitialiser le catalogue existant.
La diffusion publique reste interdite sans autorisation juridique.

## Validation effectuée avant enregistrement

Suites ciblées réussies : passerelle privée 6 tests, interface 26, API LIVE 26,
outils DEV 7, moteur continu 6 (dont HLS synthétique isolé de 40 s) ; régressions JS horloge native/sentinelle et récupération,
connexion locale et lecteur privé historique. Syntaxe Python/JS et diff Git vérifiés
hors espaces de fin de ligne préexistants de trois licences de polices conservées.
Les fixtures utilisent des données temporaires, sans arrêt du direct.

## Prochaine étape

Confirmer l'URL et la visibilité privée du dépôt de destination, ainsi que
l'autorisation d'y pousser la branche. Effectuer alors un push explicite de
cette seule branche et vérifier son HEAD distant. Aucun déploiement automatique.

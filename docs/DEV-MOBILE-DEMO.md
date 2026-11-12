# Démonstration mobile privée DEV

Quick Tunnel isolé : cloudflared officiel 2026.10.3, empreinte SHA256 vérifiée
053b80b0a23031a038fdbf3c073f59f82b8f1f152aa67971d7383ffafc0dbab0.
La passerelle locale 127.0.0.1:17011 réutilise dev_web, les vues et le moteur
existants, sans exposer directement 17010, sans compte ni session administrateur.
Le serveur DEV existant et le moteur radio ne sont ni redémarrés ni remplacés.

Le lien complet est dans `instance/dev/mobile-demo/link.txt` (privé, ignoré Git).
Son fragment contient un jeton aléatoire de 256 bits, échangé contre un cookie
Secure/HttpOnly/SameSite=Strict. Le fragment est effacé immédiatement ; ni token
ni cookie ne sont transférés à Flask. Chaque requête contrôle la révocation et
l'échéance absolue de 24 heures à partir de l’activation du nouveau lien. Posséder le lien donne le droit d'écoute :
le transmettre uniquement aux invités autorisés. Aucun accès anonyme aux médias.

Liste blanche de GET/HEAD uniquement : accueil, pages éditoriales, fiches,
ressources graphiques, pochettes, métadonnées et segments de la playlist privée.
Administration, authentification, originaux audio, contributions et écritures
sont refusés indépendamment de l'identité du visiteur. Un invité virtuel sans
compte ni privilège autorise les vues existantes uniquement dans ce processus.

Arrêt immédiat depuis le serveur :
`.venv/bin/python -B dev_demo_share.py stop`
La commande révoque le jeton et termine uniquement les deux processus créés
par son superviseur. L'échéance arrête aussi automatiquement ces processus.
Aucun service système, DNS, pare-feu, BÊTA ou production n'est modifié.

Relance ultérieure (nouveau lien et nouveau jeton) :
`.venv/bin/python -B dev_demo_share.py start`
Le binaire cloudflared doit encore exister dans /tmp/digger-cloudflared.
Tests ciblés : `tests/test_dev_demo_gateway.py`, `tests/dev_demo_browser.cjs`.
Les preuves de test HTTPS/HLS restent privées dans instance/dev/mobile-demo.
Quick Tunnel reste un accès temporaire de démonstration, sans garantie de durée.

Durée : 86 400 secondes après obtention du nom HTTPS et activation du jeton.
Le cookie expire à cette même échéance absolue ; une réouverture du lien ne
prolonge pas sa validité. Le superviseur révoque le lien et arrête ses enfants
à expiration ; aucune relance automatique du tunnel ni du moteur.

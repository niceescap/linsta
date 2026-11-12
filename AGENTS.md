# Alive Radio — Agent Instructions

- Ne jamais modifier main directement.
- Toujours travailler sur une branche feature/.
- Ne jamais supprimer une fonction existante sans justification.
- Tester avant commit.
- Ne jamais modifier les secrets ou .env.
- Ne jamais changer la configuration production sans demande explicite.
- SQLite est la source de vérité.
- Les MP3 validés sont les seuls fichiers éligibles à la diffusion.
- Le pipeline HLS existant doit rester fonctionnel.
- Chaque modification doit être commitée avec un message explicite.

## Protocole permanent de passation express — Digger Radio

Quand l'utilisateur écrit « On clôture Digger Radio » :

- Actualiser `docs/SESSION-STATE.md` en 15 lignes maximum.
- Indiquer la date, la branche active, le HEAD et les modifications non committées.
- Résumer uniquement les changements significatifs depuis la dernière passation.
- Indiquer les tests déjà réalisés et leur résultat, sans les relancer.
- Préciser l'état du déploiement, uniquement s'il est connu.
- Mentionner les problèmes non résolus.
- Terminer par une **PROCHAINE ACTION EXACTE**, immédiatement exécutable.
- Ne lancer aucun audit complet, test supplémentaire, redémarrage de service, déploiement ou commit automatique.
- Ne jamais écraser les modifications existantes ni les consignes de sécurité.
- Répondre en cinq lignes maximum.

À la reprise d'une session, lire d'abord `docs/SESSION-STATE.md`, puis vérifier uniquement les éléments techniques nécessaires à la tâche demandée.

La clôture d'une session n'est pas nécessairement la fin d'une conversation : une même conversation peut contenir plusieurs sessions. Le déclencheur est uniquement la demande explicite de clôture.
Le fichier de reprise est un résumé opérationnel ; l'historique détaillé reste dans Git et les documents de passation existants. Objectif : clôture en 30 à 60 secondes.

Garde-fous : préserver l'interdiction de diffusion HLS publique sans autorisation juridique ; ne pas modifier le fonctionnement de la radio, les données musicales, les services ou la configuration réseau ; ne pas créer de commit automatiquement. La consigne historique de message de commit explicite reste applicable aux commits autorisés ; elle n'autorise pas un commit automatique.

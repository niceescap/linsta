# Fiabilisation HLS privée — validation réelle en attente

> Mise à jour de consolidation, 19 octobre 2026 : le scénario FFmpeg synthétique
> de la version finale passe désormais, notamment la transition bloquée de 8 s.
> La suite consolidée collecte 410 tests, dont 409 réussis et 1 HTTP opt-in ignoré.
> Voir [le rapport de consolidation](PR-digger-consolidation.md). Le compte rendu
> ci-dessous décrit l'état historique du 16 octobre ; aucune nouvelle écoute FGL,
> preview Safari ou endurance 24/7 n'a été réalisée pendant la consolidation.

Le correctif est préparé sur la branche feature, mais ne doit pas être considéré
comme validé pour une nouvelle écoute FGL avant réussite du test FFmpeg ci-dessous.
Aucun commit n’est créé tant que cette validation réelle reste incomplète.

## Diagnostic constaté

L’incident a laissé des segments espacés d’environ quatre secondes jusqu’à
00:36:53 UTC, puis la fin de la lecture privée à 00:37:18, le 16 octobre 2026.
Le deuxième morceau avait commencé à 00:30:40 pour une durée de 375,36 secondes.
Les PTS des deux premiers MP3 sont réguliers, sans saut de temps anormal.
Aucun processus radio privé ni écouteur 17001 n’était actif à l’audit.
Le serveur d’administration existant n’a pas été interrompu.

La reproduction synthétique avec le moteur initial a montré les FFmpeg vivants
mais en attente disque (`folio_wait_bit_common`, `__wait_on_buffer`), au démarrage
et en fin de piste. Trois WAV synthétiques de trois secondes ont demandé environ
23 secondes au total. Un autre démarrage FFmpeg synthétique a dépassé 15 secondes.
La pression globale PSI disque a atteint plus de 80 % de temps bloqué et celle
mémoire plus de 20 %, malgré une faible charge CPU. Le serveur dispose d’environ
4 Gio de RAM sans swap, avec moins de 600 Mio disponibles lors des mesures.
Ces observations ne permettent pas d’identifier le consommateur responsable ;
aucun processus d’un autre utilisateur n’a été modifié.

Le mécanisme est reproductible : l’ancien moteur ne fournit aucun PCM lorsque le
thread principal attend le décodeur ou les entrées-sorties d’une transition.
L’encodeur, pourtant vivant, n’a alors plus assez de données pour publier ses
segments et le watchdog attribue l’absence de progression au pipeline.
Les journaux de l’incident passé ne contiennent pas l’état des processus : cette
explication est étayée par la reproduction, mais ne prouve pas à elle seule la
cause exacte de cet incident particulier. Le message `frame size not set` fourni
ne permet pas de trancher cette question.

## Correctif préparé

Réutilisation de `worker.Engine`, avec une option `continuous=True` activée
uniquement par le moteur privé ; aucune nouvelle route, aucun nouveau service.
Les arguments FFmpeg historiques et le mode de programmation public sont conservés.

- Un thread PCM alimente l’unique encodeur AAC en continu. Un tampon FIFO de
  16 blocs au maximum (564 480 octets, soit 3,2 secondes de PCM) exerce une
  contre-pression sur le décodeur ; il ne grossit pas avec la durée d’écoute.
- Une seule horloge PCM absolue assure le débit : le décodeur privé n’utilise plus
  `-re`, car une seconde horloge provoquerait des sous-alimentations et dérives.
  Les retards ordinaires d’ordonnancement sont compensés ; pas de rattrapage massif
  après une longue suspension. Le tampon est entièrement envoyé avant que
  `play()` ne rende la main et que les métadonnées du morceau suivant soient écrites.
- Si l’entrée manque, le thread fournit temporairement du silence et le signale
  après une seconde, puis signale la reprise. Cela maintient la connexion HLS,
  sans garantir une musique audible sans trou sur un serveur en attente disque.
- Le **watchdog de progression HLS reste à 20 secondes**, au démarrage comme en
  régime normal. Un encodeur figé provoque toujours une erreur.
- L’attente de PCM est surveillée séparément, avec une limite de **60 secondes** :
  décodeur bloqué ou source inactive provoquent une erreur et l’arrêt, pas un
  silence permanent. Ce délai d’entrée ne remet pas à zéro le watchdog HLS.
- Les pipes d’écriture sont réellement non bloquants, avec gestion des écritures
  partielles et d’EAGAIN ; la surveillance et les demandes d’arrêt restent actives.
- Le thread PCM est le seul observateur des fichiers HLS, pour éviter des mises
  à jour concurrentes du watchdog et des lectures disque redondantes.
- Aucun redémarrage automatique en boucle et aucun saut vers un fichier non validé.
  Le verrou existant protège le catalogue pendant toute l’exécution.

La limite de 3,2 secondes concerne uniquement notre FIFO (un bloc supplémentaire
peut être en cours d’envoi). Elle ne prétend pas borner le délai total de Safari,
de la fenêtre HLS, des buffers FFmpeg ou du réseau SSH.

## Vérifications

Tests déterministes et administrateur réussis : attente d’entrée simulée de
25 secondes avec HLS alimenté et timeout inchangé, encodeur réellement figé,
source inactive limitée à 60 secondes, débit sans dérive cumulative, tampon plein,
écritures partielles/EAGAIN, fin de tampon avant transition, remontée des erreurs,
arrêt et réinitialisation du moteur, intégrité et verrouillage du catalogue.

```bash
PYTHONPATH=.:tests timeout --signal=TERM --kill-after=2s 40s \
  instance/demo/venv/bin/python -B -m unittest -v \
  test_private_hls_continuity test_private_radio_admin
```

Le premier essai réel du correctif a dépassé la limite globale de 58 secondes,
pendant une nouvelle pression disque élevée. Aucun FFmpeg de cet essai n’est
resté actif après son arrêt. Il faut encore valider la version finale du correctif
avec FFmpeg réel, lorsque les ressources sont suffisantes :

```bash
PYTHONPATH=.:tests timeout --signal=TERM --kill-after=2s 58s \
  instance/demo/venv/bin/python -B -m unittest -v \
  test_private_radio.PrivateRadioTests.test_continuous_engine_survives_transition_wait_and_loops
```

Ce test utilise uniquement des MP3 synthétiques et un répertoire temporaire.
Il vérifie la boucle 1,2,1, les métadonnées, la lecture AAC stéréo 44,1 kHz et une
attente simulée de huit secondes au bouclage. Son watchdog local est volontairement
abaissé à six secondes pour rendre la régression détectable rapidement ; aucune
valeur du runtime réel n’est modifiée. L’exécution reste unique et bornée.

Ne pas lancer les FGL dans l’état de pression disque/mémoire observé. Après réussite
réelle et retour durable des ressources, vérifier à nouveau port et verrou, puis
utiliser la commande privée habituelle pour un premier essai borné (par exemple
60 secondes). Ne jamais arrêter une instance existante, ni lancer de service
persistant ou ouvrir un proxy public. Contrôler ensuite métadonnées/progression,
absence de FFmpeg résiduel, état du catalogue et historique de cet essai.

Le catalogue privé a été contrôlé en lecture seule : 26 lignes, références,
empreintes et tailles identiques à l’audit initial, trois lectures historiques
inchangées et `PRAGMA quick_check` égal à `ok`. Aucun MP3 ni consentement n’a été
modifié, aucun essai FGL lancé, aucune donnée réimportée. Aucun redémarrage du
service web n’est nécessaire pour ces fichiers de processus radio séparé.

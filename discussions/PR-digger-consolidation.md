# Consolider Digger : interface, contributions URL-first et radio privée

Cette branche réunit l'interface Digger et le backend récent. L'accueil et
l'administration utilisent les assets Digger, tout en conservant le parcours
URL-first, le CSRF et la séparation entre sélection éditoriale et validation audio.
Le dépôt direct public `/tracks` reste fermé (410). Aucun document juridique
n'est activé : l'approbation finale des nouvelles contributions reste bloquée.

## Provenance et ordre d'intégration

| Source | Intégration |
| --- | --- |
| `feature/digger-stage2-metadata`, `1e4117c` | Base : métadonnées, submissions, audio, droits et catalogue privé |
| `feature/jean-digger-development`, `8d0f349` | Stage 1 déjà ancêtre de la base ; aucune seconde application de migration |
| `feature/digger-radio-worker-hls`, `bd62b17` | Déjà ancêtre de la base, y compris séquences HLS `epoch_us` |
| `origin/feature/digger-radio-ui-refresh`, `c5a36f8` | Fusion réelle avec deux parents, commit `06742f0` |
| Travail local préexistant | Réappliqué après fusion depuis une sauvegarde vérifiée de 30 fichiers |

Les trois têtes distantes ont été vérifiées par `git ls-remote --heads origin`.
Aucun fetch nécessaire : les références locales correspondaient aux têtes distantes.
Aucun push, PR, merge vers main, déploiement ou accès aux secrets effectué.

## Résolution et corrections de consolidation

- Conflit `templates/index.html` : structure/player Digger ; session, CSRF et lien
  `/submissions` conservés ; formulaire MP3-first de la branche UI écarté.
- Administration : actions de soumission et protections récentes conservées,
  branding Digger et fermeture de la balise image corrigée.
- Player : HLS natif prioritaire pour Safari, lecture demandée par un geste,
  refus de `play()` géré, reprise après erreur fatale restaurée, état accessible,
  cover stable sans timestamp à chaque poll et repli Digger.
- Consentement : le brouillon `draft-media-v1` effectivement enregistré est à
  nouveau consultable dans le dépôt. Le projet V0.3 reste informatif et distinct.
- Tests démo : restauration des modules config/app après la fixture pour éviter
  une contamination des tests du worker privé. Aucune protection runtime relâchée.

## Travail local récupéré

Option `Engine(continuous=True)` et tests de continuité HLS privée ; route/page
informative V0.3 ; actions et textes de revue initiale ; environnement démo isolé,
ses exemples de configuration, sa documentation et ses tests ; documents de
passation et de diagnostic. Les fichiers démo ne sont ni installés ni démarrés.

La FIFO et l'horloge indépendantes sont activées uniquement par la radio privée.
Le watchdog HLS reste à 20 s et la source inactive est bornée à 60 s. Les écritures
non bloquantes partagées restent couvertes par les tests. La radio publique garde
son mode de programmation historique ; aucune stabilité 24/7 n'est annoncée.

## Vérification reproductible

Résultat de consolidation : **410 tests collectés, 409 réussis, 1 ignoré** en
60,256 s. Le test ignoré est le test HTTP privé opt-in sur port fixe. Les deux
scénarios FFmpeg réels passent. Les tests Node du player, son contrôle de syntaxe
et `git diff --check` passent également. Le test privé seul avait aussi réussi :
11 tests collectés dont 1 ignoré en 26,456 s.

Depuis la racine de cette branche, avec un Python disposant de `requirements.txt`
et des dépendances de `demo/requirements.txt`, Node, FFmpeg et ffprobe :

```bash
PYTHONPATH=.:tests python -B -m unittest discover -s tests -q
node --check static/js/radio.js
node tests/test_radio_player.js
git diff --check
```

Les tests utilisent des SQLite et médias temporaires. OAuth, SMTP et APIs
fournisseurs sont simulés. Le test HTTP privé sur le port fixe 17001 reste opt-in
et n'a pas été exécuté. Les scénarios FFmpeg réels utilisent seulement des sons
synthétiques, des répertoires temporaires et des enfants créés par ces tests.

Le scénario privé réel teste la boucle 1,2,1, une attente de transition de 8 s,
la progression HLS, les métadonnées, l'AAC stéréo 44,1 kHz et l'arrêt des enfants.
Il ne remplace pas un essai Safari ni une endurance en conditions de déploiement.

## Limites avant preview

- Aucune preview lancée, aucun service existant redémarré et aucune DB réelle migrée.
- Google OAuth est conservé et testé avec Authlib simulé ; callback, credentials
  et connexion Google réels restent à valider dans un environnement autorisé.
- La démo récupérée ferme volontairement Google OAuth, inscriptions et direct.
  Elle ne suffit donc pas au scénario OAuth complet : prévoir une preview dédiée
  avec DB/stockage isolés et configuration fournie par l'exploitant, sans ouvrir
  automatiquement ces fonctions dans la démo existante.
- YouTube réel et activation Spotify restent à valider. Spotify est désactivé par
  défaut ; la complétion manuelle reste possible.
- Le contrat V0.3 n'est pas activé et les nouvelles contributions ne deviennent
  pas diffusables. Les dépôts/revues privées restent disponibles.
- Ressources du serveur, exposition HLS et reconnexion/endurance réelles restent
  à contrôler avec Nico avant un essai audio prolongé.
- La récupération historique `refetch-cover` référence encore une fonction non
  importée. Elle n'est pas réactivée ici : son ancien récupérateur générique exige
  un traitement SSRF séparé. Le nouveau dépôt de cover ne l'utilise pas.
- La sécurisation opérationnelle (débits, quotas globaux, sauvegardes/restauration,
  proxy et supervision) ne fait pas partie de cette consolidation.

Les comptes rendus du 16 octobre sont conservés comme historique. Leurs états de
processus, ports et déploiement ne constituent pas une photographie actuelle.

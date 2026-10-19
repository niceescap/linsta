# Revue de l’accès public de la démo

Décision : accueil et lecteur accessibles sans Basic Auth globale. HTTP sert seulement les challenges ACME webroot avant Certbot certonly ; HTTPS devient public après revue du modèle final par Nico.
La connexion applicative est obligatoire pour les espaces privés. L’inscription,
OAuth Google, vérification email et le live HLS restent fermés.

## Routes contrôlées dans le code et les tests

| Routes | Accès effectif dans la démo |
| --- | --- |
| `GET /` | Public, template `demo/templates/index.html`, lecteur sans source HLS, aucun nom/email/rôle de compte affiché |
| `GET /static/radio-logo.png`, `/static/alive-og.jpg` | Assets publics existants, sans média de contribution |
| `GET /now-playing` | Public, réponse fixe sans lecture de `now.json`, pas de données personnelles ni preuves |
| `GET /demo/login` | Formulaire public pour compte existant, sans identifiant prérempli |
| `GET /csrf-token` | Token CSRF de la session du visiteur, pas un identifiant ou secret serveur ; no-store |
| `POST /login` | Mot de passe vérifié par Werkzeug, CSRF requis, session renouvelée ; limitation Nginx proposée sur cette seule route |
| `POST /logout`, `/logout-form` | Effacent uniquement la session courante, CSRF requis |
| `GET /me` | Compte connecté et vérifié ; aucune information de compte sans session |
| `/submissions`, détails, création, édition, refresh, soumission | Compte vérifié obligatoire, opérations filtrées par propriétaire dans `SubmissionService` |
| `/submissions/<id>/audio`, `/audio/media/<kind>` | Compte vérifié ET propriété, contrôle des fichiers et preuves dans `SubmissionAudioService` |
| `/admin`, `/admin/submissions`, leurs détails et mutations/revues audio | Compte vérifié ET rôle admin, contrôles dans décorateurs et services |
| `/admin/tracks/<id>/*`, `/media/<id>` | Admin seulement ; les actions historiques refusent les pistes rattachées aux propositions |
| `/media/cover/<id>` | Toujours 404 dans cette démo et dans le vhost final, y compris covers legacy ; prévisualisations privées conservées |
| `POST /tracks` | Auth obligatoire ; dépôt direct toujours retiré (410 pour compte autorisé avec CSRF) |
| `/register`, `/login/google`, `/auth/callback`, `/verify/<token>` | Fermés par le garde démo ; POST sans CSRF peut être rejeté 400 avant le rejet démo 403 |
| `/hls/*`, `/storage/*`, `/instance/*`, fichiers SQLite et cachés | Aucun contenu public ; rejets Nginx, pas d’alias vers ces répertoires, pas de route Flask servant ces fichiers |

Sources relues : `app.py` (décorateurs, auth et média legacy), `web_security.py`
(compte relu en base, CSRF signé par session), `submission_routes.py`,
`submission_review_routes.py`, `submission_audio_routes.py`, services
`submissions.py`, `submission_review.py`, `submission_audio.py`.
Le comportement historique n'est pas modifié ; seuls les points d'entrée et
templates démo ajoutent leurs restrictions. Le registre juridique reste vide,
les migrations et le blocage d’approbation finale restent inchangés.

## Vérification ciblée sans serveur

Le test démo énumère **chaque route enregistrée**, avec contrôle GET/POST applicable
et CSRF valide sur les mutations pour vérifier l’authentification elle-même.
Une nouvelle route non classée fait échouer le test. HEAD suit les handlers GET ;
OPTIONS automatique n’expose que les méthodes, pas les données privées.
Un contributeur vérifié est testé sur toutes les routes admin : 403.

Les tests insèrent seulement des fixtures temporaires : cover legacy non autorisée,
comptes fictifs et `now.json` contenant des marqueurs personnels. Ils vérifient que
ces marqueurs, identifiants fictifs et secret de test ne se retrouvent pas sur les
pages publiques, et que les fichiers bruts ne sont pas accessibles. Aucun mot de
passe administrateur réel ou base/média historique n’est utilisé.

Quatre tests existants ciblent en plus les médias/proofs privés : propriétaire et
admin uniquement, autre contributeur refusé, révocation des droits admin pendant
la transaction et confirmations finales obligatoires. Le test démo conserve le
contrôle de connexion admin avec CSRF et de document juridique non validé.

Commande unique, séquentielle, limitée à 60 secondes :
Dernier résultat : **16 tests réussis en 1,837 s**, sans serveur réseau, après
correction de la fermeture d’une réponse de fichier dans le test lui-même.

```bash
PYTHONPATH=.:tests timeout --signal=TERM --kill-after=2s 58s \
  instance/demo/venv/bin/python -B -m unittest -v test_demo_environment \
  test_audio_workflow.AudioWorkflowTests.test_private_media_owner_and_admin_only_public_cover_hidden \
  test_audio_workflow.AudioWorkflowTests.test_other_verified_contributor_cannot_access_media_or_evidence \
  test_audio_workflow.AudioWorkflowTests.test_admin_demoted_or_unverified_during_final_transaction_is_denied \
  test_audio_workflow.AudioWorkflowTests.test_admin_page_and_both_confirmations_required
```

## Configuration et limites à valider avant ouverture

Nico doit intégrer le seul vhost final, vérifier ses chemins TLS, ses règles
héritées et l’absence de zone `digger_demo_login` homonyme, puis exécuter `nginx -t`
avant toute recharge. `auth_basic off` évite de conserver une protection globale
héritée. La zone de débit s’applique seulement à la connexion : 5 requêtes/min/IP,
burst 3, HTTP 429 en dépassement. Elle réduit les tentatives simples, sans offrir
de garantie contre des attaques distribuées ; un proxy/CDN demande une revue
spécifique de l’IP réelle. Les quotas Gunicorn/systemd sont documentés séparément.

Le proxy ne sert aucun fichier runtime directement. Les pages dynamiques démo sont
no-store ; cookies Secure, HTTP-only, SameSite Lax et CSRF restent actifs. Ne pas
journaliser les mots de passe, cookies, tokens ou corps de requêtes.
Le lecteur démo ne charge pas de CDN et ne tente pas de connexion HLS.

Ces contrôles ne constituent pas un audit complet de sécurité Internet. Le HTTPS,
le filtrage/rate limit Nginx et le démarrage persistant ne sont pas testés sur le
serveur réel dans cette étape. Vérifier après autorisation : accueil/login 200,
admin/propositions 401 sans session, contributeur refusé sur admin, fichiers et
HLS fermés, login sur HTTPS et comportement CSRF, aucun accès public au port 17000.

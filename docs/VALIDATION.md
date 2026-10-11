# Validation réalisée

Résultats obtenus le **11 octobre 2026**, dans l’environnement Linux de développement. Les contrôles réseau réels sont indiqués séparément des tests utilisant des réponses simulées.

| Vérification | Résultat réellement obtenu |
| --- | --- |
| Suite Python du projet | **143 tests Pytest réussis** : API invités et administrateur, sessions, CSRF, retrait personnel, migration SQLite, transferts Wi-Fi, votes, sources, file, bibliothèque, exports et intégration cloud. Après l’ajustement du nom par défaut, les 28 tests de migration et des API invité/administrateur ont de nouveau réussi. |
| Navigateur Chromium | **42 vérifications réussies**, sur écran de 1 440 px et écran mobile de 390 px : retrait personnel, aperçu sur demande, validation groupée, ZIP et partage. Treize captures, aucune erreur JavaScript, console ou réseau, aucune destination extérieure inattendue. |
| Champ de recherche | Après ajustement CSS, vérification ciblée du site généré à 1 440 px et 390 px : texte séparé de l’icône, marge gauche 47 px et aucun débordement. Aucun appel cloud pour ce contrôle visuel. |
| Audio local | Import d’un WAV silencieux généré pour le test, vérification par ffprobe, renommage avec accents, récupération du ZIP depuis le navigateur et contrôle des fichiers M3U8 UTF-8 utilisant des chemins relatifs. |
| Installation et services Linux | Installation réutilisable, diagnostics et génération du site statique réussis. Démarrage, arrêt et redémarrage des services contrôlés ; commande d’arrêt et superviseur terminés avec le code **0**. |
| Docker sous Linux | Image de la première version réellement construite, avec vérification TLS et sommes de contrôle conservées. Diagnostics et requêtes aux services invités/administrateur réussis dans un conteneur sans accès réseau extérieur, puis conteneur arrêté. L’image n’a pas été reconstruite pour les ajouts Fanny/retrait/transfert. |
| Scripts Windows | Revue des scripts sans exécution PowerShell. L’installation et le lancement sur Windows 11 restent à essayer sur le PC de l’organisateur. |
| GitHub Pages | **Construction et déploiement réussis** par le workflow [38157177635](https://github.com/BOUILLOON/Ekoraid/actions/runs/38157177635), commit `964c14c`. L’API Pages confirme un site public, HTTPS obligatoire et source GitHub Actions à https://bouilloon.github.io/Ekoraid/. La lecture directe de cette URL depuis l’environnement Codex reste refusée par son proxy réseau (CONNECT 403 avant toute réponse du site) ; aucune vérification HTTP/navigateur à cette URL n’est revendiquée. |

## Parcours vérifiés dans le navigateur

Le test démarre deux serveurs sur des ports locaux libres, avec une base temporaire et des identifiants de test. Il arrête ses processus et supprime sa base à la fin ; il ne modifie pas `.env.local`.

- Pseudo conservé après rechargement, proposition manuelle et lien YouTube enregistrés hors ligne.
- Ajout et retrait d’un vote, filtre « Mes propositions », recherche dans le catalogue et identités distinctes pour deux navigateurs partageant la même IP.
- Recherche pendant la saisie, sans soumettre le formulaire, puis choix d’un résultat ; deux variantes d’un même lien retrouvent la même source.
- Connexion privée, tableau de bord, QR code local, validation d’une sélection et confirmation des autorisations avant mise en file.
- Suspension après le morceau en cours et import d’une playlist contenant un doublon et un élément indisponible, dans l’ordre analysé.
- Import audio, renommage, préparation et récupération du pack, réglages, diagnostics et création de codes d’invitation.
- Administration et fichiers audio absents de la surface publique ; administration exigeant une session.
- Nom, invitation et détails de la soirée de Fanny ; aucun débordement sur écran mobile.
- Retrait personnel avec annulation puis confirmation, compteurs actualisés et isolation de deux personnes portant le même pseudo.
- Aperçu vidéo dans les résultats et les propositions : aucune iframe avant le clic, aucun autoplay, lecteur supprimé à la fermeture, contrôles bureau et mobile. Trois lecteurs ont été remplacés par une page de test locale ; aucune vidéo YouTube distante n’a été lue.
- Validation groupée en une seule requête, puis export limité aux fichiers prêts sélectionnés ; véritable archive ZIP récupérée et inspectée.
- Création d’un QR code avec expiration de dix minutes, téléchargement du même ZIP dans un contexte mobile et révocation rendant le lien indisponible. L’adresse privée `192.168.50.10` et l’interface réseau sont simulées par un proxy de test sur loopback qui conserve l’en-tête Host. La création de l’autorisation et le téléchargement utilisent le vrai backend ; les octets et le nom du ZIP correspondent au téléchargement administrateur. Ce test ne constitue pas un transfert entre appareils physiques sur le Wi-Fi.

Les métadonnées YouTube sont **simulées** dans ce test. Aucun processus de téléchargement audio YouTube n’est lancé. Le WAV et le ZIP sont réellement traités par les outils locaux. Le rapport et les captures sont écrits dans un dossier temporaire `playlist25-browser-*` ; `PLAYLIST_BROWSER_ARTIFACTS` permet de choisir leur destination.

Commande, depuis la racine du projet, avec Playwright et Chromium installés :

```bash
node tests/browser-smoke.cjs
```

`CHROMIUM_PATH` permet de désigner un navigateur installé ; `PLAYLIST_TEST_PYTHON` permet de désigner le Python de l’environnement virtuel. Sans ces options, le test utilise `.venv` et Chromium lorsqu’il est disponible à l’emplacement système prévu.

## Base cloud et recherche : tests isolés

Les contrôles suivants ont réussi :

- Tests Node du parseur de recherche et de l’adaptateur public, avec réponses HTTP simulées.
- Six tests Python de synchronisation SQLite/Pages utilisant des données de test, dont la conservation d’un retrait face à une ancienne validation et une nouvelle proposition après retrait sans reprise des anciens votes.
- Application de la migration SQL **deux fois** dans un PostgreSQL 16 jetable sous Docker, sans port publié et sans réseau extérieur. Les contrôles couvrent RLS et permissions de table, isolation des sessions, invitations, quotas, doublons, votes concurrents et retrait, fusion des votes et publication administrateur atomique. La mise à jour vérifie également le retrait par son propriétaire, deux personnes portant le même pseudo, une collecte fermée, le retrait répété, le nouvel ajout avec un identifiant distinct et les indicateurs `mine: false` d’un visiteur anonyme.
- Trois tests de migration SQLite conservent les identifiants, votes, références cloud, tâches et fichiers audio d’une ancienne base. Une migration répétée reste stable ; les colonnes personnalisées inconnues ou les références incohérentes interrompent la migration avec retour arrière.
- Trente-cinq tests de transfert couvrent la session et le CSRF administrateur, le lien aléatoire limité à un ZIP, son expiration et sa révocation, le client et l’adresse réseau privés, le refus des en-têtes de proxy, les chemins et liens symboliques interdits ainsi que les archives modifiées. Les réponses de transfert imposent `no-store` et `no-referrer`.
- Vérification TypeScript avec Deno, puis un test du gestionnaire Edge en cinq étapes avec réponses réseau simulées. Les contrôles portent notamment sur les destinations fixes, le budget serveur, les résultats YouTube et la confidentialité de la clé API optionnelle.

Commandes de reproduction :

```bash
.venv/bin/python -m pytest -q
node cloud/test_search.mjs
node cloud/test_adapter.mjs
.venv/bin/python cloud/test_sync.py
.venv/bin/python cloud/test_sql.py

deno check cloud/supabase/functions/music-search/index.ts
deno test --allow-env cloud/supabase/functions/music-search/index_test.ts
```

Le test SQL nécessite Docker. Les tests Edge ont été exécutés avec Deno dans un conteneur officiel. Sous Windows, remplacez `.venv/bin/python` par `.venv\Scripts\python.exe` pour les commandes Python.

## Contrôles réseau réels Supabase

Sur le projet configuré, avec **uniquement la clé publique publishable** côté invité :

| Opération | Réponse observée |
| --- | --- |
| Session publique | HTTP **200**, invité absent, réglages publics reçus, événement « Les 25 ans de Fanny ». |
| Liste des propositions | HTTP **200**, les deux propositions existantes conservées ; indicateurs `mine` strictement faux pour un visiteur anonyme. |
| Lecture directe des tables invités et invitations | HTTP **401**, refus PostgreSQL `42501`. |
| RPC de snapshot administrateur | HTTP **401**, refus PostgreSQL `42501`. |
| RPC de publication administrateur | HTTP **401**, refus PostgreSQL `42501`. |
| RPC de retrait avec un jeton au format valide mais sans identité | HTTP **200**, erreur applicative **401** ; aucune proposition modifiée. |
| Fonction `music-search`, requête « Dalida » | HTTP **200**, fournisseur `youtube-public-search`, cinq vidéos réelles reçues sans clé YouTube optionnelle. |

La recherche réelle a notamment reçu « Dalida - Il venait d’avoir 18 ans » (**188 s**) et « Dalida - Le temps des fleurs » (**238 s**). D’autres résultats étaient une interview et une compilation : le choix de la bonne version reste à l’invité. Ce contrôle a utilisé les budgets de lecture/recherche anonymes et n’a créé aucune identité, proposition ou vote ; aucun audio n’a été téléchargé.

Avant la mise à jour SQL, un snapshot privé de sauvegarde a été enregistré hors Git. Le schéma et les permissions Supabase ont été installés et vérifiés, puis le nom de l’événement a été publié sans modification de proposition. La comparaison avant/après confirme la conservation de l’invité, des deux sources et des deux propositions existantes. Aucun invité ni morceau de test n’a été ajouté sur le projet réel.

L’instance locale de préparation a aussi été réellement lancée avec sa configuration privée : connexion administrateur réussie, `POST /api/cloud/sync` HTTP **200** avec les deux propositions réelles, adresses de transfert privées reçues en HTTP **200**, puis arrêt propre. Cette synchronisation utilise la clé privilégiée **uniquement dans le backend local**. Les requêtes invité HTML/session/catalogue ont répondu HTTP **200** ; les routes privées d’administration, de cloud et `static/admin.js` ont répondu **404** sur le service invité. Un lien de transfert sans autorisation a renvoyé **404**, avec `no-store` et `no-referrer`.

Pour la première version, le dossier exact `_site` destiné à Pages a été servi localement et contrôlé dans Chromium à **1 440 px et 390 px**, avec sa politique CSP et ses ressources publiques. Le formulaire de pseudo s’affiche, sans débordement ni erreur JavaScript. L’adaptateur du navigateur a reçu de **vraies réponses Supabase** : session HTTP 200, catalogue HTTP 200 alors vide et recherche « Dalida » HTTP 200 avec cinq résultats. Comme Chromium ne possédait pas le certificat de confiance du proxy de cet environnement, les seules requêtes au projet Supabase ont été transmises, sans modification de leur URL ou de leur contenu, par un transport Python/httpx utilisant le proxy normal et la vérification TLS active. Ce contrôle vérifie l’intégration de l’interface construite avec le backend réel ; il ne constitue pas une ouverture directe du domaine Pages. Aucune identité, proposition ni vote n’a été créé. Rapport et captures : dossier temporaire `playlist25-live-transport-*`.

Ces résultats établissent le fonctionnement du fournisseur et des permissions au moment du contrôle. La disponibilité future des services gratuits reste à vérifier avant la soirée.

## Vérifications restantes sur votre matériel

- Installation complète, lancement et arrêt sur Windows 11.
- Utilisation sur un téléphone ou une tablette physique, réception du ZIP sur votre Wi-Fi et sur celui de la salle.
- Ouverture du pack dans Mixxx, déplacement du dossier complet et lecture avec le matériel audio prévu.
- Préparation d’un fichier externe dont vous avez les autorisations et lecture d’un aperçu YouTube réel : les essais réalisés n’ont téléchargé aucun audio YouTube réel ni lu de vidéo distante.
- Ouverture de l’adresse GitHub Pages publiée et essai de collecte avec le PC de préparation éteint ; le déploiement a réussi, mais le proxy de l’environnement de préparation a empêché sa lecture directe.

Aucune lecture dans Mixxx, aucun essai Android physique et aucune installation Windows réelle ne sont revendiqués dans ce rapport. Le fonctionnement hors ligne de la soirée doit être confirmé avec les fichiers et le matériel retenus.

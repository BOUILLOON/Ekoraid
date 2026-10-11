# Validation réalisée

Résultats obtenus le **11 octobre 2026**. Les tests de développement utilisent Linux ; les contrôles du navigateur, de Supabase et du paquet Windows sont distingués ci-dessous. Aucun test automatisé ne télécharge de musique YouTube commerciale.

| Vérification | Résultat obtenu pour cette version |
| --- | --- |
| Suite Python | **260 tests réussis, 1 ignoré et 7 sous-tests**, en **20,67 s**. Le test ignoré exige PowerShell, absent de cet environnement Linux. La suite couvre les API, les permissions, les photos, les durées, les playlists proposées, les migrations, la file, les fichiers, les exports, la synchronisation et le lancement autonome. |
| Distribution Windows : tests locaux | **30 tests réussis et 1 ignoré** sous Linux, où PowerShell est absent. Le même groupe de tests réussit dans le workflow Windows, y compris l’analyse des scripts PowerShell. |
| Navigateur avec le vrai backend local | **51 vérifications réussies et 15 captures**, à 1 440 px et 390 px. API, SQLite, profils, WAV généré et ZIP réellement utilisés ; métadonnées YouTube et lecteurs vidéo simulés. Aucune erreur JavaScript, console ou réseau, aucune requête extérieure inattendue. |
| Catalogue de 20 001 éléments dans Chromium | **14 vérifications réussies**, avec données et réponses API simulées. Pagination, recherche complète, sélection groupée, file, bibliothèque et pack vérifiés sans afficher tout le catalogue à la fois. |
| Photos et durées dans Chromium | **11 vérifications ciblées réussies**, avec images générées et API simulée : JPEG/WebP, recadrage, annulation, enregistrement, retrait, identité conservée, durées distinctes et inconnues. |
| Synchronisation cloud | **16 tests réussis et 7 sous-tests**, avec snapshots simulés. Conservation des identifiants, corrections, votes, retraits et photos ; récupération de gros catalogues par lots. |
| PostgreSQL 16 isolé | Migration appliquée **deux fois** avec succès ; permissions, profils, durées, identifiants, propriétaires, votes, propositions illimitées et catalogue de **20 001 propositions** contrôlés. |
| Parseur et adaptateur cloud | Tests Node réussis, avec réponses réseau simulées. |
| Fonction de recherche Edge | Vérification Deno réussie ; **1 test en 9 étapes** réussi, avec réponses réseau simulées. |
| Supabase réel | Mise à jour SQL, huit RPC concernées et fonction Edge installées et contrôlées. La comparaison avec la sauvegarde confirme que les identifiants, propriétaires et votes existants sont conservés ; propositions illimitées avec `max_proposals=0`. |
| Recherche YouTube réelle | HTTP **200** pour une recherche de morceaux et pour les métadonnées d’une vidéo précise ; cette dernière renvoie une durée de **143 s**, via `youtube-public-search-exact-video`. Aucun audio téléchargé. |
| Durées des propositions existantes | Durées réelles complétées pour **27 sources vidéo**, uniquement lorsque leur durée était absente. **7 vidéos non résolues** et les demandes manuelles sans source restent inconnues. Identifiants et propriétaires conservés ; titres, artistes, états, ordre et votes non modifiés. |
| Interface publiée avec Supabase réel | Le dossier exact `_site` servi localement a reçu de vraies réponses Supabase dans Chromium à **1 440 px et 390 px** : **33 propositions publiques**, recherche « Dalida » avec **5 résultats réels et leurs durées**, aucune erreur JavaScript ni débordement. Aucun invité, proposition ou vote de test créé. |
| GitHub Pages | Déploiement [38166729295](https://github.com/BOUILLOON/Ekoraid/actions/runs/38166729295) **réussi**, commit `544a86e`. Le site inclut le téléchargement direct du studio Windows vérifié. |
| Exécutable Windows autonome | Workflow [38166188497](https://github.com/BOUILLOON/Ekoraid/actions/runs/38166188497) **réussi**, commit `45aae4c`. Les **10 contrôles de l’application compilée** ont réussi sur Windows-2025 avec Python 3.12.10 ; la [release stable](https://github.com/BOUILLOON/Ekoraid/releases/tag/studio-windows-v2026.10.11.1) est publiée. |

## Parcours vérifiés avec le vrai backend local

Le test navigateur démarre les services sur des ports locaux libres, avec une base et des identifiants temporaires. Il arrête ses processus et supprime sa base à la fin, sans modifier la configuration privée de l’installation.

- Pseudo persistant, proposition manuelle, lien canonique, votes, recherche, filtre « Mes propositions » et séparation de deux navigateurs portant le même pseudo ou partageant la même IP.
- Photo recadrée au centre en PNG de **128 × 128 pixels**, enregistrée par l’API dans SQLite puis affichée après rechargement. Une autre identité voit la photo publique sans obtenir les droits du profil. Retirer la photo conserve l’identité, les propositions et les votes.
- Retrait personnel annulé puis confirmé, listes et compteurs actualisés, impossibilité de retirer les propositions d’autrui ou de valider celles déjà retirées.
- Proposition d’un lien de playlist enregistrée comme **une seule proposition**, sans développement automatique ni lecteur de vidéo isolée. Pour une URL vidéo et playlist, les deux choix explicites sont conservés par l’API.
- Durées au-delà de 24 heures sans remise à zéro, source dupliquée comptée une seule fois, inconnus signalés et total indépendant du filtre. Si toutes les durées sont inconnues, le site et le studio affichent **À compléter**.
- Recherche pendant la saisie puis choix d’une version, sans lecteur chargé avant un clic. Les trois lecteurs ouverts pendant le test sont des pages locales simulées, sans autoplay ni lecture YouTube distante ; fermer le dialogue les supprime.
- Connexion privée, réglages, diagnostics, QR local et codes d’invitation. Validation groupée en une requête, confirmation des droits avant mise en file et suspension après le morceau en cours.
- Import d’une playlist simulée contenant un doublon et un élément indisponible, avec ordre conservé. La file est persistante ; le worker de téléchargement YouTube n’est pas lancé par ce test.
- Import d’un WAV silencieux généré, contrôle réel par ffprobe, renommage accentué et ZIP récupéré dans le navigateur. Le pack contient uniquement les fichiers prêts, avec M3U8 UTF-8 et chemins relatifs.
- Création du partage ZIP, QR, expiration de dix minutes, téléchargement identique sur un second navigateur mobile et révocation immédiate. Le réseau privé est **simulé sur loopback**, avec conservation de l’en-tête Host ; l’autorisation et les octets du ZIP passent par le vrai backend. Ce contrôle ne constitue pas un transfert entre appareils physiques sur Wi-Fi.
- Interface lisible à 1 440 px et 390 px, administration et audio absents de la surface publique, routes privées exigeant une session.

Rapport et 15 captures : `/tmp/playlist25-browser-z5iWqu`. Ces fichiers sont des artefacts de l’environnement de test, pas des données publiées sur le site.

## Gros catalogues, photos et durées : essais ciblés

Le test Python [test_large_collections.py](../tests/test_large_collections.py) enregistre réellement **20 001 sources** dans SQLite, réserve les tâches, conserve les positions de playlist et écrit sur disque un pack de **20 001 fichiers** avec archive compatible ZIP64. Les fichiers de ce test contiennent des octets de fixture ; **ffprobe est simulé pour ce gros lot**. Les tests audio séparés et le parcours navigateur contrôlent de vrais échantillons générés avec ffprobe. Ce résultat ne signifie pas que 20 001 musiques YouTube ont été téléchargées ou écoutées.

La synchronisation d’un catalogue de cette taille est vérifiée en **21 lots**. Les essais Chromium de gros catalogue vérifient la navigation, la recherche jusqu’au dernier élément, la sélection sur toutes les pages, la validation et la préparation groupées, les playlists, la bibliothèque et la sélection du pack. Le navigateur limite les éléments visibles tout en conservant la sélection complète. Les mutations de ces essais sont interceptées par des fixtures locales ; aucune requête n’atteint la production. Rapport et captures : `/tmp/playlist25-large-ooOUL4`.

Les essais ciblés de profil vérifient également l’annulation, le retrait confirmé par enregistrement, la conversion JPEG/WebP en PNG, les limites de **10 Mo et 20 millions de pixels**, le refus d’un SVG renommé, le cache des seules photos visibles et la lecture sur écran mobile. Ils confirment la durée des sources distinctes, indépendante du filtre, et les playlists explicitement signalées comme restant à analyser. Rapport et captures : `/tmp/playlist25-profile-86WzXd`.

## Base cloud et permissions

PostgreSQL 16, dans un conteneur jetable, vérifie la migration répétée, RLS, le refus de lecture directe des tables et des RPC privées avec le rôle public, l’isolation des sessions et des profils, les invitations, les budgets par minute, les doublons, votes concurrents et retraits, la fusion et la publication administrateur atomique.

Les tests SQLite et de synchronisation vérifient la conservation des références cloud, des corrections locales, des tâches et fichiers, des retraits et des votes. Les tests de transfert vérifient la session et le CSRF, l’autorisation limitée à un ZIP, expiration et révocation, clients et adresses privés, refus des en-têtes de proxy, chemins confinés et archives modifiées.

Les tests Edge contrôlent les réponses de recherche et les métadonnées de vidéo, les destinations réseau fixes, les budgets serveur et la confidentialité de la clé API optionnelle. Les photos utilisent l’identité enregistrée dans le navigateur ; un pseudo identique ne donne aucun droit sur un autre profil.

Commandes de reproduction, depuis la racine du projet :

```bash
.venv/bin/python -m pytest -q
node tests/browser-smoke.cjs
node cloud/test_search.mjs
node cloud/test_adapter.mjs
.venv/bin/python cloud/test_sync.py
.venv/bin/python cloud/test_sql.py

deno check cloud/supabase/functions/music-search/index.ts
deno test --allow-env cloud/supabase/functions/music-search/index_test.ts
```

Le test navigateur nécessite Playwright et Chromium ; `CHROMIUM_PATH`, `PLAYLIST_TEST_PYTHON` et `PLAYLIST_BROWSER_ARTIFACTS` permettent de choisir le navigateur, Python et le dossier des rapports. Le test SQL nécessite Docker. Sous Windows, remplacez `.venv/bin/python` par `.venv\Scripts\python.exe` pour les commandes Python.

## Contrôles sur les services publics réels

Avant la mise à jour Supabase, une sauvegarde privée hors Git contenait **27 sources, 29 propositions, 8 invités et 14 votes**. La comparaison avant/après confirme la conservation de leurs identifiants, propriétaires et votes. La mise à jour ne réinitialise pas la collecte ; `max_proposals=0` laisse les propositions illimitées. Aucune identité, proposition ou vote de test n’a été créé sur ce projet réel.

Les contrôles avec la clé publique obtiennent HTTP **200** pour les sessions et le catalogue. La lecture directe des tables privées, le snapshot et la publication administrateur sont refusés HTTP **401**. Une recherche réelle reçoit cinq résultats YouTube ; les métadonnées d’un lien vidéo sont aussi récupérées par recherche exacte de cette vidéo, avec durée. Ces requêtes consomment des budgets de lecture/recherche, sans téléchargement audio ni lecture distante dans le navigateur de test.

Après une sauvegarde privée, les durées de **27 vidéos existantes** ont été récupérées par leur identifiant exact. La mise à jour SQL a écrit uniquement `duration`, avec la condition `duration IS NULL`. La comparaison confirme les mêmes identifiants et propriétaires avant/après ; titres, artistes, états, ordre et votes n’ont pas été écrits. Les **7 vidéos non résolues** restent sans durée, comme les demandes manuelles non associées à une source. Aucun audio n’a été téléchargé pour compléter ces métadonnées.

Le dossier exact `_site` destiné au site publié a également été servi localement et contrôlé dans Chromium à **1 440 px et 390 px**. L’interface affiche les **33 propositions publiques** alors présentes et reçoit **5 résultats réels** pour « Dalida », avec durées. Sessions, catalogue et recherche répondent HTTP **200**, sans création d’identité, de proposition ou de vote de test. Aucun débordement horizontal ni erreur JavaScript ou console n’a été relevé. Pour utiliser le transport réseau de cet environnement, les requêtes Supabase originales ont été transmises par Python/httpx via le proxy normal, avec vérification TLS active. Rapport et captures : `/tmp/playlist25-live-transport-FsNBr5`.

Le site public est à <https://bouilloon.github.io/Ekoraid/>. Le succès du workflow Pages prouve le déploiement ; l’ouverture directe de ce domaine depuis l’environnement Codex reste bloquée par son proxy avant toute réponse du site. Le contrôle de l’interface construite avec le backend Supabase réel ne constitue donc pas une visite directe de cette adresse. Les services gratuits restent à vérifier avant la soirée.

## Application Windows autonome publiée

La [release `studio-windows-v2026.10.11.1`](https://github.com/BOUILLOON/Ekoraid/releases/tag/studio-windows-v2026.10.11.1) fournit [Studio-Fanny-Windows.zip](https://github.com/BOUILLOON/Ekoraid/releases/latest/download/Studio-Fanny-Windows.zip), de **150 549 087 octets**. Son rapport `windows-smoke.json` et sa somme SHA-256 accompagnent l’archive. Le workflow [38166188497](https://github.com/BOUILLOON/Ekoraid/actions/runs/38166188497), commit `45aae4c2abe3d672e4ee7dbb2e98af740c57ab59`, a construit et réellement exécuté l’application compilée sur **Windows-2025**, avec **Python 3.12.10**.

Les **10 contrôles ont réussi** : outils et runtimes inclus, yt-dlp, exécutables, fenêtre Tk, démarrage puis arrêt des trois services sans processus restant, réponses HTTP, authentification et CSRF, conversion d’un WAV généré en MP3 avec les vrais FFmpeg/ffprobe, ZIP complet avec M3U8, et séparation des ressources privées du service invités. Aucun audio extérieur n’a été téléchargé et aucune donnée Supabase n’a été écrite par ces essais Windows.

Cette validation porte sur le runner CI. Le démarrage sur le PC Windows 11 de l’organisation et les transferts entre appareils physiques restent à essayer.

## Vérifications restantes

- Installation, lancement et arrêt de la version publiée sur le PC Windows 11 de l’organisation.
- Réception du ZIP sur un téléphone ou une tablette physique, sur votre Wi-Fi et celui de la salle.
- Ouverture du pack dans Mixxx, déplacement du dossier complet et lecture avec le matériel audio de la soirée, Internet coupé.
- Préparation d’un fichier externe autorisé et écoute d’un aperçu YouTube réel. Les essais rapportés n’ont téléchargé aucun audio YouTube réel ni lu de vidéo distante.
- Ouverture du site Pages publié et collecte depuis un appareil indépendant, avec le PC de préparation éteint.

Aucune lecture réelle dans Mixxx ni aucun téléchargement réel de 10 000 ou 20 000 musiques n’est revendiqué. L’absence de plafond de nombre ne supprime pas le temps de préparation, l’espace disque nécessaire ou les limites des services gratuits.

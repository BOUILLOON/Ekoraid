# Validation réalisée

Résultats obtenus le **11 octobre 2026**. Les tests de développement utilisent Linux ; les contrôles du navigateur, de Supabase et du paquet Windows sont distingués ci-dessous. Aucun test automatisé ne télécharge de musique YouTube commerciale.

| Vérification | Résultat obtenu pour cette version |
| --- | --- |
| Suite Python | **255 tests réussis, 1 ignoré et 7 sous-tests**, en **21,30 s**. Le test ignoré exige PowerShell, absent de cet environnement Linux. La suite couvre les API, les permissions, les photos, les durées, les playlists proposées, les migrations, la file, les fichiers, les exports, la synchronisation et le lancement autonome. |
| Distribution Windows : tests locaux | **21 tests de packaging réussis** après la dernière correction de l’archive, avec un contrôle supplémentaire des chemins Windows. Ces tests Linux ne prouvent pas le fonctionnement de l’exécutable sous Windows. |
| Navigateur avec le vrai backend local | **51 vérifications réussies et 15 captures**, à 1 440 px et 390 px. API, SQLite, profils, WAV généré et ZIP réellement utilisés ; métadonnées YouTube et lecteurs vidéo simulés. Aucune erreur JavaScript, console ou réseau, aucune requête extérieure inattendue. |
| Catalogue de 20 001 éléments dans Chromium | **14 vérifications réussies**, avec données et réponses API simulées. Pagination, recherche complète, sélection groupée, file, bibliothèque et pack vérifiés sans afficher tout le catalogue à la fois. |
| Photos et durées dans Chromium | **11 vérifications ciblées réussies**, avec images générées et API simulée : JPEG/WebP, recadrage, annulation, enregistrement, retrait, identité conservée, durées distinctes et inconnues. |
| Synchronisation cloud | **16 tests réussis et 7 sous-tests**, avec snapshots simulés. Conservation des identifiants, corrections, votes, retraits et photos ; récupération de gros catalogues par lots. |
| PostgreSQL 16 isolé | Migration appliquée **deux fois** avec succès ; permissions, profils, durées, identifiants, propriétaires, votes, propositions illimitées et catalogue de **20 001 propositions** contrôlés. |
| Parseur et adaptateur cloud | Tests Node réussis, avec réponses réseau simulées. |
| Fonction de recherche Edge | Vérification Deno réussie ; **1 test en 9 étapes** réussi, avec réponses réseau simulées. |
| Supabase réel | Mise à jour SQL, huit RPC concernées et fonction Edge installées et contrôlées. La comparaison avec la sauvegarde confirme que les identifiants, propriétaires et votes existants sont conservés ; propositions illimitées avec `max_proposals=0`. |
| Recherche YouTube réelle | HTTP **200** pour une recherche de morceaux et pour les métadonnées d’une vidéo précise ; cette dernière renvoie une durée de **143 s**, via `youtube-public-search-exact-video`. Aucun audio téléchargé. |
| GitHub Pages | Déploiement [38165026450](https://github.com/BOUILLOON/Ekoraid/actions/runs/38165026450) réussi, commit `9d9d6b3`. Publication de la version `8188d45` en cours dans [38165261752](https://github.com/BOUILLOON/Ekoraid/actions/runs/38165261752). |
| Exécutable Windows autonome | Construction et test réel en cours dans [38165260096](https://github.com/BOUILLOON/Ekoraid/actions/runs/38165260096). **L’exécutable et sa release ne sont pas encore déclarés validés ou disponibles dans ce rapport.** |

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

Le site public est à <https://bouilloon.github.io/Ekoraid/>. Le succès d’un workflow Pages prouve le déploiement ; l’ouverture directe de ce domaine depuis l’environnement Codex reste bloquée par son proxy avant toute réponse du site. Les essais de l’interface construite et du backend Supabase réel sont distincts d’une visite directe de cette adresse. Les services gratuits restent à vérifier avant la soirée.

## Vérifications restantes

- Fin du workflow Windows, contrôle du vrai exécutable et publication de l’archive. Le parcours installé sur le PC Windows 11 de l’organisation reste ensuite à vérifier, même si le runner CI réussit.
- Réception du ZIP sur un téléphone ou une tablette physique, sur votre Wi-Fi et celui de la salle.
- Ouverture du pack dans Mixxx, déplacement du dossier complet et lecture avec le matériel audio de la soirée, Internet coupé.
- Préparation d’un fichier externe autorisé et écoute d’un aperçu YouTube réel. Les essais rapportés n’ont téléchargé aucun audio YouTube réel ni lu de vidéo distante.
- Ouverture du site Pages publié et collecte depuis un appareil indépendant, avec le PC de préparation éteint.

Aucune lecture réelle dans Mixxx ni aucun téléchargement réel de 10 000 ou 20 000 musiques n’est revendiqué. L’absence de plafond de nombre ne supprime pas le temps de préparation, l’espace disque nécessaire ou les limites des services gratuits.

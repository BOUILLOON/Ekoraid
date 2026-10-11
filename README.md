# La playlist des 25 ans

Une application en français pour préparer une soirée : les invités proposent des musiques et votent ; l’organisateur choisit les bonnes versions, prépare les fichiers audio et exporte un dossier transportable pour Mixxx. L’ancien site PHP a été remplacé.

**Site invités publié : https://bouilloon.github.io/Ekoraid/**. Le déploiement GitHub Pages a réussi le 11 octobre 2026 ; Supabase et la recherche YouTube ont été vérifiés en ligne. Votre PC peut rester éteint pendant la collecte. L’installation ci-dessous sert à votre administration privée et à la préparation des fichiers audio.

## Deux usages

**Collecter sans laisser votre PC allumé : GitHub Pages + Supabase gratuit.** Pages sert l’interface ; Supabase conserve les propositions et les votes. La recherche propose de vrais résultats YouTube pendant la saisie, avec choix explicite de la version. Si le fournisseur ne répond pas, le lien ou la demande textuelle reste utilisable. Il faut créer un projet Supabase et configurer le site : [guide de publication gratuit](cloud/README.md). Les quotas et conditions des offres gratuites peuvent évoluer ; le projet ne requiert aucun abonnement ni API payante. Aucun déploiement n’est créé par la simple installation locale.

**Préparer les fichiers et fonctionner hors ligne : l’application sur votre PC.** Python/FastAPI, SQLite, yt-dlp, FFmpeg et Node.js. L’administration écoute uniquement en boucle locale, sur un service HTTP distinct. Les téléchargements sont traités dans un processus séparé : fermer le navigateur n’arrête pas la file. Aucun invité ne peut supprimer, télécharger ou accéder à vos fichiers audio. Une proposition ne déclenche jamais un téléchargement.

Vous pouvez aussi utiliser ce serveur local pour la collecte avec un tunnel HTTPS gratuit : votre PC doit alors rester allumé. Le [guide d’installation](docs/INSTALLATION.md) contient cette option et les instructions Windows, Linux et Docker.

## Démarrer sur Windows 11

Installez Python 3.12 ou plus, Node.js 20 ou plus et FFmpeg avec ffprobe, puis ouvrez un terminal dans ce dossier :

```powershell
powershell -NoProfile -File .\install-windows.ps1
.venv\Scripts\python.exe -m playlist25 configure-admin
powershell -NoProfile -File .\Start-Windows.ps1
```

Invités locaux : `http://127.0.0.1:8000`. Administration privée : `http://127.0.0.1:8001`. Le secret initial est dans `.env.local`, ignoré par Git ; `configure-admin` permet de choisir votre mot de passe sans l’afficher. Le guide fournit les commandes Python si PowerShell refuse les scripts, sans modifier sa politique de sécurité.

Arrêt : `Ctrl+C`, `Stop-Windows.ps1` ou `.venv\Scripts\python.exe -m playlist25 stop`. Les données sont conservées. Pour Linux : `bash scripts/install-linux.sh`, `.venv/bin/python -m playlist25 configure-admin`, puis `bash scripts/start.sh`.

## Le parcours des invités

1. Choisir un pseudo, sans inscription ni email.
2. Taper par exemple **Dalida**, choisir un résultat, coller un lien YouTube ou écrire le titre demandé.
3. Voir qui a ajouté quoi, retrouver ses propositions, voter et retirer son vote.

Les recherches signalent la chaîne comme une chaîne, pas comme un artiste vérifié. Aucune version live, reprise ou remix n’est choisie automatiquement. Les mêmes identifiants YouTube sont dédupliqués ; les titres proches sont signalés, pas fusionnés. Dix propositions par session par défaut. Sans compte, changer de navigateur ou effacer sa session permet de contourner l’identité ; les codes d’invitation sont une option plus stricte.

## Préparer la soirée

1. Si vous avez utilisé Pages, renseigner les paramètres Supabase privés **sur votre PC**, puis utiliser **Récupérer la collecte**. La clé secrète/service_role n’est jamais envoyée à Pages. Cette récupération ne télécharge aucune musique.
2. Valider ou archiver les demandes, corriger titres/artistes, associer les demandes textuelles à une vidéo précise. Les fichiers peuvent être renommés avec leurs métadonnées. Les propositions restent visibles par pseudo.
3. Sélectionner les morceaux à télécharger et confirmer les droits d’usage. Pour une playlist : **Importer → Analyser → Tout sélectionner ou décocher → Confirmer**. Une URL vidéo avec `list=` demande « ce morceau » ou « toute la playlist ».
4. Suivre la file persistante ; les erreurs ne bloquent pas les autres morceaux. La suspension s’applique après le morceau en cours. Les fichiers ne sont disponibles qu’après contrôle ffprobe ; ils sont recontrôlés au redémarrage.
5. Importer vos propres fichiers si besoin : les originaux ne sont pas déplacés ni supprimés.
6. **Préparer le pack pour Mixxx**, télécharger le ZIP ou conserver le dossier local, puis l’ouvrir dans votre lecteur. Les M3U8 contiennent des chemins relatifs UTF-8 et uniquement des fichiers présents.

```text
Anniversaire25/
  audio/
  01-selection.m3u8
  02-classement-votes.m3u8
  playlists-importees/
  catalogue.csv
  rapport-erreurs.txt
  manifeste.json
```

Un fichier téléchargé est enregistré sur la machine du backend. Avec le backend sur votre PC, il est déjà local. Avec un backend distant, il faut récupérer le pack dans le navigateur. La lecture dans Mixxx est indépendante du serveur web et ne modifie pas sa base de données. Convertir en MP3 n’améliore pas la qualité de la source ; le profil source évite une conversion quand le format convient.

## Hors ligne

Activer **Mode soirée hors ligne** : l’interface, le catalogue, les propositions manuelles et les exports restent utilisables ; recherche et téléchargements externes sont désactivés. Les ressources de l’interface sont locales. Pour les téléphones dans la salle, utiliser le Wi-Fi local et son URL/QR code, distincts du lien Internet Pages. Mixxx utilise les fichiers du pack même si le site s’arrête.

## Configuration et sécurité

Les valeurs initiales sont dans [.env.example](.env.example), les données dans `data/`. Les réglages persistés se modifient dans l’administration. Les ouvertures et la modération locales s’appliquent au serveur local ; le bouton de publication cloud les applique explicitement au site Pages. Les demandes cloud peuvent être récupérées sans écraser vos corrections locales.

URL YouTube strictement normalisées, requêtes publiques limitées, sessions distinctes, CSRF, votes uniques en base, quotas, chemins confinés, noms Windows compatibles et CSV protégés contre les formules. Les outils sont appelés avec des listes d’arguments, sans shell et sans options fournies par les invités. Les uploads audio sont contrôlés sans protocoles réseau. Ne partagez jamais `.env.local`, les sauvegardes ou une clé Supabase secrète. Le service public ne monte pas l’administration ni les fichiers audio.

Aucune suppression de morceaux n’est exposée : la modération archive. Sauvegardez le dossier de données après un arrêt propre des services et conservez une copie indépendante du pack.

## Vérifier et déboguer

```powershell
.venv\Scripts\python.exe -m playlist25 doctor
.venv\Scripts\python.exe -m pytest -q
```

Sur Linux : `.venv/bin/python`. Les diagnostics de l’administration vérifient outils, droits d’écriture, espace disque et file. Le [guide d’installation](docs/INSTALLATION.md) fournit les erreurs courantes et la checklist avant la soirée. Les tests du navigateur et de la base cloud ont leurs commandes dans [le rapport de validation](docs/VALIDATION.md).

Les tests automatiques utilisent des fournisseurs simulés et des sons générés : ils ne téléchargent pas arbitrairement des musiques commerciales. Les contrôles réseau réels sont séparés. Windows 11, Android et Mixxx doivent être vérifiés sur votre matériel ; aucune lecture dans Mixxx n’est revendiquée ici.

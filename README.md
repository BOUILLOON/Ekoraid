# Les 25 ans de Fanny

Une application en français pour les **25 ans de Fanny, le 17 octobre 2026 dès 19 h, à la salle des fêtes de Fontain** : les invités proposent des musiques et votent ; Fanny choisit les bonnes versions, prépare les fichiers audio et récupère un pack transportable pour Mixxx. L’ancien site PHP a été remplacé.

**Site invités publié : https://bouilloon.github.io/Ekoraid/**. Le déploiement GitHub Pages a réussi le 11 octobre 2026 ; Supabase et la recherche YouTube ont été vérifiés en ligne. Votre PC peut rester éteint pendant la collecte. L’installation ci-dessous sert à votre administration privée et à la préparation des fichiers audio.

## Deux usages

**Collecter sans laisser votre PC allumé : GitHub Pages + Supabase gratuit.** Pages sert l’interface ; Supabase conserve les propositions et les votes. La recherche propose de vrais résultats YouTube pendant la saisie, avec choix explicite de la version. Si le fournisseur ne répond pas, le lien ou la demande textuelle reste utilisable. Il faut créer un projet Supabase et configurer le site : [guide de publication gratuit](cloud/README.md). Les quotas et conditions des offres gratuites peuvent évoluer ; le projet ne requiert aucun abonnement ni API payante. Aucun déploiement n’est créé par la simple installation locale.

**Préparer les fichiers et fonctionner hors ligne : l’application sur votre PC.** Python/FastAPI, SQLite, yt-dlp, FFmpeg et Node.js. L’administration privée s’ouvre sur ce PC ; votre mot de passe suffit, sans compte invité à créer. Les téléchargements sont traités dans un processus séparé : fermer le navigateur n’arrête pas la file. Une proposition ne déclenche jamais un téléchargement. Pour récupérer le pack sur un téléphone ou une tablette, vous pouvez créer un lien privé valable dix minutes sur le même Wi-Fi ; il donne accès au seul ZIP choisi.

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
4. Retirer uniquement sa propre proposition après confirmation. Elle disparaît du catalogue public ; son historique reste conservé dans le studio, sans possibilité de la réactiver.

Les recherches signalent la chaîne comme une chaîne, pas comme un artiste vérifié. Aucune version live, reprise ou remix n’est choisie automatiquement. Les mêmes identifiants YouTube sont dédupliqués ; les titres proches sont signalés, pas fusionnés. Dix propositions par session par défaut. Sans compte, changer de navigateur ou effacer sa session permet de contourner l’identité ; les codes d’invitation sont une option plus stricte.

## Récupérer toutes vos musiques avec l’interface

1. Lancez l’application sur votre PC et ouvrez **http://127.0.0.1:8001**, puis entrez votre mot de passe administrateur. Le site Pages collecte les idées PC éteint ; la préparation audio nécessite ce PC allumé et l’application lancée.
2. Dans **Réglages & diagnostics**, choisissez **Récupérer les propositions du site public**. La connexion Supabase privée se configure sur ce PC ; sa clé secrète n’est jamais envoyée à Pages. Cette récupération ne télécharge aucune musique.
3. Dans **Propositions**, corrigez les titres/artistes et associez les demandes sans lien à une vidéo précise avec **Modifier**. Validez vos choix, puis utilisez **Tout sélectionner** pour les lignes affichées ou **Sélectionner tous les validés**. Les propositions retirées par leur auteur restent exclues.
4. Cliquez sur **Préparer les fichiers de la sélection**, puis confirmez vos droits d’usage. Cette action lance la préparation sur le PC. Dans **Téléchargements**, les compteurs distinguent les morceaux en attente, en préparation, prêts et en erreur.
5. Dès que des fichiers sont prêts, choisissez **Récupérer les fichiers prêts en ZIP**. Dans **Packs & exports**, cochez les fichiers voulus puis **Créer le ZIP de X fichiers**. Vous pouvez récupérer un premier lot sans attendre les morceaux en erreur ou encore en préparation. Depuis Propositions, **Récupérer les fichiers déjà prêts** limite le pack aux fichiers prêts de votre sélection.
6. Après création, cliquez sur **Télécharger le ZIP sur cet appareil** pour l’enregistrer sur le PC. Pour un téléphone ou une tablette, choisissez **Partager sur mon Wi-Fi**, sélectionnez la connexion locale du PC, créez le QR code et scannez-le sur l’appareil connecté au même Wi-Fi. Le lien privé expire après dix minutes ; **Fermer le partage** le révoque immédiatement. Gardez le PC et l’application allumés jusqu’à la fin du transfert.

Votre navigateur enregistre le ZIP dans son dossier Téléchargements ou vous demande où le placer. Le téléphone reçoit le ZIP via ce lien Wi-Fi, sans ouvrir l’administration privée. Le site Pages seul ne peut pas préparer les fichiers audio. Un réseau qui isole les appareils peut empêcher le transfert ; dans ce cas, récupérez le ZIP sur le PC puis copiez-le par USB.

Pour une playlist entière : **Import de playlists → Analyser → Tout sélectionner ou décocher → Importer et préparer les fichiers**. Une URL vidéo avec `list=` demande « ce morceau » ou « toute la playlist ». Les erreurs ne bloquent pas les autres morceaux ; réessayez après avoir corrigé leur cause. Les fichiers ne sont disponibles qu’après contrôle ffprobe, puis recontrôlés au redémarrage. Vous pouvez aussi importer vos fichiers dans **Bibliothèque locale** ; les originaux restent intacts.

Le pack contient les fichiers et les playlists M3U8 avec des chemins relatifs UTF-8 :

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

La préparation écrit l’audio sur votre PC. Le téléchargement du ZIP l’enregistre sur l’appareil qui ouvre le lien. Après extraction, gardez le dossier entier ensemble. La lecture dans Mixxx est indépendante du serveur web et ne modifie pas sa base de données. Convertir en MP3 n’améliore pas la qualité de la source ; le profil source évite une conversion quand le format convient. Le [guide de l’administration](docs/ADMINISTRATION.md) détaille les gestes dans le studio.

## Hors ligne

Activer **Mode soirée hors ligne** : l’interface, le catalogue, les propositions manuelles et les exports restent utilisables ; recherche et téléchargements externes sont désactivés. Les ressources de l’interface sont locales. Pour les téléphones dans la salle, utiliser le Wi-Fi local et son URL/QR code, distincts du lien Internet Pages. Mixxx utilise les fichiers du pack même si le site s’arrête.

## Configuration et sécurité

Les valeurs initiales sont dans [.env.example](.env.example), les données dans `data/`. Les réglages persistés se modifient dans l’administration. Les ouvertures et la modération locales s’appliquent au serveur local ; le bouton de publication cloud les applique explicitement au site Pages. Les demandes cloud peuvent être récupérées sans écraser vos corrections locales.

URL YouTube strictement normalisées, requêtes publiques limitées, sessions distinctes, CSRF, votes uniques en base, quotas, chemins confinés, noms Windows compatibles et CSV protégés contre les formules. Les outils sont appelés avec des listes d’arguments, sans shell et sans options fournies par les invités. Les uploads audio sont contrôlés sans protocoles réseau. Ne partagez jamais `.env.local`, les sauvegardes ou une clé Supabase secrète. Le service public ne monte pas l’administration ni les fichiers audio.

Les invités peuvent retirer leurs propres propositions ; les autres propositions restent protégées. Le retrait conserve l’historique, les fichiers audio et les traitements déjà lancés. La modération archive également. Sauvegardez le dossier de données après un arrêt propre des services et conservez une copie indépendante du pack.

## Vérifier et déboguer

```powershell
.venv\Scripts\python.exe -m playlist25 doctor
.venv\Scripts\python.exe -m pytest -q
```

Sur Linux : `.venv/bin/python`. Les diagnostics de l’administration vérifient outils, droits d’écriture, espace disque et file. Le [guide d’installation](docs/INSTALLATION.md) fournit les erreurs courantes et la checklist avant la soirée. Les tests du navigateur et de la base cloud ont leurs commandes dans [le rapport de validation](docs/VALIDATION.md).

Les tests automatiques utilisent des fournisseurs simulés et des sons générés : ils ne téléchargent pas arbitrairement des musiques commerciales. Les contrôles réseau réels sont séparés. Windows 11, Android et Mixxx doivent être vérifiés sur votre matériel ; aucune lecture dans Mixxx n’est revendiquée ici.

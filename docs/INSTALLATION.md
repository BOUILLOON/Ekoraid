# Installation, lancement et préparation de la soirée

Le site public <https://bouilloon.github.io/Ekoraid/> collecte les propositions et les votes même si votre PC est éteint. Le studio sur votre PC sert à choisir les morceaux, préparer l’audio et récupérer les fichiers pour la soirée. Les deux usages sont séparés ; votre mot de passe administrateur reste privé.

## Windows 11 — version autonome recommandée

La version autonome **Studio-Fanny-Windows.zip est disponible** dans la [release Windows](https://github.com/BOUILLOON/Ekoraid/releases/tag/studio-windows-v2026.10.11.1). Son lancement et la préparation d’un audio généré ont été vérifiés sur Windows en CI. Le [guide de l’administration](ADMINISTRATION.md) décrit ensuite la sélection et la récupération des musiques.

1. [Téléchargez l’archive Windows](https://github.com/BOUILLOON/Ekoraid/releases/latest/download/Studio-Fanny-Windows.zip).
2. Faites un clic droit sur le ZIP, puis **Extraire tout**. Ne lancez pas l’application directement depuis l’archive et gardez tout le dossier extrait ensemble.
3. Ouvrez le dossier `Studio-Fanny`, puis double-cliquez sur **Studio-Fanny.exe**.
4. Au premier démarrage, choisissez un mot de passe administrateur d’au moins **12 caractères**. Vous pouvez aussi renseigner la connexion Supabase privée à cette étape, ou le faire plus tard avec **Configurer la connexion**.
5. L’application lance le studio et ouvre votre navigateur. Saisissez le mot de passe sur la page privée, puis **Ouvrir le studio**.

Python, Node.js, FFmpeg et ffprobe sont inclus dans cette version. Aucun terminal, script PowerShell ou modification manuelle de `.env.local` n’est nécessaire pour ce parcours.

La fenêtre de l’application reste ouverte pendant les téléchargements et les partages Wi-Fi. Vous pouvez fermer le navigateur ; les traitements continuent. Ses boutons permettent de :

- **Ouvrir le studio** : revenir à l’administration privée dans le navigateur.
- **Arrêter le studio** : arrêter proprement les services, en conservant les données et les fichiers.
- **Ouvrir mes fichiers** : accéder au dossier de vos musiques et exports.
- **Configurer la connexion** : renseigner ou corriger la connexion privée à Supabase.
- **Changer mon mot de passe** : choisir un nouveau mot de passe local, y compris si vous avez oublié le précédent.
- **Reprendre une ancienne installation** : sélectionner le dossier de votre précédente installation contenant `.env.local`, sans déplacer ni supprimer ses fichiers.

L’administration s’ouvre sur <http://127.0.0.1:8001>, exclusivement sur le PC qui lance l’application. Le site invités local est sur <http://127.0.0.1:8000>. Le site public GitHub Pages garde la même adresse et reste accessible indépendamment de ce PC.

### Connexion à la collecte publique

La connexion peut être renseignée au premier démarrage. Pour la modifier ensuite, cliquez d’abord sur **Arrêter le studio**, puis **Configurer la connexion**. Utilisez le projet `https://dpcohlyczczaqovblzin.supabase.co`. Le bouton **Ouvrir la page de ma clé Supabase** mène aux clés du projet : copiez la clé **Secret** ou **service_role** dans le champ privé de l’application, puis **Enregistrer et ouvrir le studio**. La connexion est vérifiée avant l’enregistrement ; une erreur conserve la configuration précédente.

Cette connexion est facultative pour ouvrir le studio local, mais nécessaire pour récupérer les propositions publiques et publier vos décisions. La clé reste sur votre PC ; ne la placez jamais dans GitHub Pages ou dans une capture d’écran. La prochaine ouverture réutilise vos réglages et récupère la collecte avant d’ouvrir le navigateur, sauf en mode hors ligne. Elle ne lance aucun téléchargement audio.

### Données, mises à jour et sauvegarde

Par défaut, les données sont dans `%LOCALAPPDATA%\StudioFanny\data`. La configuration privée est conservée séparément de l’application dans `%LOCALAPPDATA%\StudioFanny`. Pour voir ce dossier, collez ce chemin dans la barre d’adresse de l’Explorateur Windows.

Une mise à jour consiste à extraire la nouvelle archive dans un nouveau dossier, puis à ouvrir son `Studio-Fanny.exe` après avoir arrêté l’ancienne version. Elle réutilise vos données personnelles et ne supprime aucune proposition de la collecte Supabase. Ne supprimez pas votre dossier de données lors d’une mise à jour.

Pour sauvegarder, utilisez **Arrêter le studio**, puis copiez le dossier `%LOCALAPPDATA%\StudioFanny` complet vers un support privé. Si vous avez repris une ancienne installation, sauvegardez aussi ce dossier, avec sa configuration privée et ses données. Conservez en plus une copie du pack audio final sur un second support.

Pour changer le mot de passe ou reprendre une ancienne installation, arrêtez d’abord le studio. Ces opérations conservent les données ; la reprise utilise le dossier choisi à son emplacement actuel.

## Windows 11 — installation depuis les sources, option avancée

Cette méthode conserve les anciens scripts pour les personnes qui souhaitent utiliser ou développer le projet source. Elle est distincte de la version autonome ci-dessus.

Installez **Python 3.12 ou plus**, **Node.js 20 ou plus** et **FFmpeg avec ffprobe**. Node.js sert aux composants JavaScript nécessaires à YouTube. Les dépendances Python comprennent `yt-dlp[default]` et `yt-dlp-ejs` ; installer uniquement yt-dlp ne prépare pas tous les prérequis.

Les installateurs officiels conviennent. Avec le gestionnaire Windows `winget`, vous pouvez aussi exécuter vous-même :

```powershell
winget install --exact --id Python.Python.3.12 --source winget
winget install --exact --id OpenJS.NodeJS.LTS --source winget
winget install --exact --id Gyan.FFmpeg --source winget
```

Ouvrez ensuite un **nouveau terminal** dans le dossier du projet. Les scripts ne changent ni la politique PowerShell, ni le pare-feu, ni les réglages de votre box.

```powershell
powershell -NoProfile -File .\install-windows.ps1
.venv\Scripts\python.exe -m playlist25 configure-admin
powershell -NoProfile -File .\Start-Windows.ps1
```

L’installation crée un environnement Python `.venv`, installe les versions de `requirements.txt`, initialise la base et crée `.env.local` avec des secrets aléatoires lorsqu’il manque. Un fichier `.env.local` déjà présent est conservé. `configure-admin` demande votre mot de passe dans le terminal sans l’afficher ; choisissez au moins 12 caractères.

Après cette installation, **Start-Windows.cmd** permet aussi de démarrer la version source par double-clic, sans passer par PowerShell. Le mot de passe doit déjà avoir été configuré.

Si Windows refuse l’exécution des scripts, suivez les mêmes étapes avec les commandes Python. Aucune modification de la politique PowerShell n’est nécessaire :

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playlist25 init
.venv\Scripts\python.exe -m playlist25 configure-admin
.venv\Scripts\python.exe -m playlist25 doctor
.venv\Scripts\python.exe -m playlist25 start
```

Le Python choisi par `py -3` doit être en version 3.12 ou plus : vérifiez `py -3 --version`.

Le lanceur garde une fenêtre ouverte. Laissez-la ouverte pendant l’utilisation du site. Liens par défaut :

- Invités sur ce PC : <http://127.0.0.1:8000>.
- Administration privée : <http://127.0.0.1:8001>.

Arrêtez avec `Ctrl+C` dans la fenêtre de lancement, ou depuis un second terminal :

```powershell
powershell -NoProfile -File .\Stop-Windows.ps1
```

La commande équivalente est `.venv\Scripts\python.exe -m playlist25 stop`. Les données et les fichiers audio restent présents.

## Configuration de la version source

Après `init`, ajoutez les paramètres souhaités de [.env.example](../.env.example) dans votre `.env.local`, en conservant `ADMIN_PASSWORD` et `SESSION_SECRET`. N’utilisez pas `.env.example` comme remplacement complet : ses commentaires ne contiennent pas de secrets utilisables.

| Paramètre | Exemple / rôle |
| --- | --- |
| `EVENT_NAME` | `"Les 25 ans de Fanny"` : valeur initiale du nom. |
| `DATA_DIR` | `data` ou `D:/Anniversaire25/donnees` : base, audio, temporaires et exports. |
| `GUEST_PORT` | `8000`, différent du port administrateur. |
| `ADMIN_PORT` | `8001`. `ADMIN_HOST` reste `127.0.0.1`. |
| `LOCAL_URL` | `http://192.168.1.25:8000` pour les téléphones du même Wi-Fi. |
| `PUBLIC_URL` | Adresse HTTPS du tunnel invités ou du site Pages. |
| `MAX_PROPOSALS` | `0` par défaut : propositions illimitées. Une valeur positive impose un quota volontaire. |
| `MAX_PLAYLIST_ITEMS` | `0` par défaut : aucune limite de nombre par analyse. Une valeur positive impose une limite volontaire. |
| `MAX_QUEUE_SIZE` | `0` par défaut : aucune limite de nombre pour la file. Les morceaux sont traités un par un. |
| `MAX_DURATION` | `1800` secondes maximum par morceau. |
| `OUTPUT_FORMAT` | `mp3` ou `source` pour éviter une conversion inutile. |
| `FFMPEG_PATH`, `FFPROBE_PATH` | Nom de commande, ou chemin absolu vers les exécutables. |
| `JS_RUNTIME` | `node` par défaut. |

Redémarrez après un changement de configuration de processus. Les réglages déjà enregistrés en base, notamment le nom, l’ouverture des propositions/votes, le mode hors ligne, les limites de propositions/playlists et les liens, se modifient dans **Administration → Réglages**. Les valeurs `.env.local` définissent leurs valeurs initiales.

Ne publiez jamais `.env.local`, `data/`, les sauvegardes ou la clé Supabase `service_role`. La clé publique Supabase du site Pages a un rôle différent ; le guide cloud explique cette séparation.

## Partager un lien depuis votre PC : tunnel temporaire

Cette alternative utilise votre PC comme hébergeur. Il doit rester **allumé et connecté à Internet** pendant la collecte. Pour éviter cette contrainte, utilisez [GitHub Pages + Supabase](../cloud/README.md).

1. Démarrez l’application et vérifiez le lien invités local.
2. Installez `cloudflared` depuis [Cloudflare](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/). Sur Windows, `winget install --exact --id Cloudflare.cloudflared --source winget` est une possibilité.
3. Dans une seconde fenêtre, lancez **uniquement le port invités** :

   ```powershell
   cloudflared tunnel --url http://127.0.0.1:8000
   ```

4. Copiez le lien HTTPS `https://…trycloudflare.com` affiché. Enregistrez-le comme **Lien public** dans les réglages administrateur. Partagez le lien et son QR code depuis l’interface invités.
5. Testez sur un téléphone en réseau mobile. Le lien doit afficher le site invités ; les API d’administration et les fichiers audio ne sont pas servis par ce port.

N’exposez pas `8001`. Un Quick Tunnel n’est pas un hébergement permanent : son URL est aléatoire, peut changer après un redémarrage et n’est pas garantie pour une collecte de longue durée. Le site actualise les propositions par de petites requêtes HTTP périodiques. Consultez les [limites actuelles des Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/) avant de le retenir. Pour une adresse stable, utilisez le site Pages ou un tunnel nommé avec un domaine déjà disponible.

Le QR code public utilise le lien configuré ; remplacez ce lien et régénérez le QR code si l’adresse du tunnel change. Le QR code local utilise `LOCAL_URL` et nécessite le Wi-Fi local. Un lien Internet public ne fonctionnera pas dans une salle sans Internet.

## Fanny : sélectionner et récupérer les musiques dans le studio

Le studio privé s’ouvre **sur votre PC** à <http://127.0.0.1:8001>, avec le mot de passe choisi dans l’application Windows ou, pour la version source, avec `configure-admin`. Aucun compte invité n’est nécessaire pour administrer. Le site public Pages reçoit les propositions PC éteint ; pour préparer l’audio, lancez l’application sur le PC et gardez-la ouverte.

1. Dans **Réglages & diagnostics**, cliquez sur **Récupérer les propositions du site public**. Ce bouton récupère les idées et les votes ; il ne prépare pas encore d’audio. Dans la version autonome, **Configurer la connexion** permet de renseigner Supabase sans modifier de fichier.
2. Dans **Propositions**, vérifiez les versions et utilisez **Modifier** pour corriger les informations ou associer un lien YouTube à une demande textuelle. Les noms de chaînes ne constituent pas une identification garantie de l’artiste.
3. Cliquez sur **Tout sélectionner** pour les propositions correspondant au filtre, sur toutes les pages, ou sur **Sélectionner tous les validés**. Les compteurs indiquent les sources distinctes, les fichiers prêts et les demandes encore sans lien. Les propositions retirées par leur auteur restent dans l’historique mais ne peuvent pas être sélectionnées ou réactivées.
4. Cliquez sur **Préparer les fichiers de la sélection**, confirmez vos droits d’usage, puis suivez **Téléchargements**. L’ajout à la file ne crée pas immédiatement un ZIP. Les morceaux deviennent prêts après vérification de leur fichier.
5. Cliquez sur **Récupérer les fichiers prêts en ZIP**, choisissez les fichiers dans **Packs & exports**, puis **Créer le ZIP de X fichiers**. Le pack utilise uniquement des fichiers effectivement présents. Vous pouvez récupérer une partie prête pendant que d’autres morceaux attendent ou sont en erreur. Pour respecter votre sélection initiale, utilisez **Récupérer les fichiers déjà prêts** depuis Propositions.
6. Une fois le ZIP créé, cliquez sur **Télécharger le ZIP sur cet appareil**. Le navigateur du PC l’enregistre dans Téléchargements ou vous propose un dossier. Si vous voulez le récupérer sur un téléphone ou une tablette, utilisez le partage Wi-Fi décrit ci-dessous.

Pour choisir les fichiers déjà importés ou téléchargés, **Bibliothèque locale → Récupérer mes fichiers en ZIP** ouvre directement la sélection du pack. Les fichiers qui échouent restent signalés dans Téléchargements ; corrigez la cause avant de réessayer. Le [guide de l’administration](ADMINISTRATION.md) décrit chaque écran.

## Récupérer le ZIP sur un téléphone ou une tablette

1. Connectez le PC et le téléphone ou la tablette **au même Wi-Fi**. Gardez le PC et l’application allumés.
2. Après avoir créé votre ZIP dans **Packs & exports**, cliquez sur **Partager sur mon Wi-Fi**.
3. Choisissez dans la liste l’adresse de la connexion Wi-Fi du PC, puis **Créer le QR code de partage**. La liste contient les interfaces privées actives compatibles ; le studio ne partage aucun fichier avant cette action.
4. Scannez le QR code avec le téléphone ou la tablette. La page locale ouverte propose **Télécharger le pack ZIP**. Le navigateur de cet appareil gère l’emplacement du fichier ; consultez son dossier Téléchargements ou son application Fichiers.
5. Une fois le transfert terminé, cliquez sur **Fermer le partage** dans le studio. Cela révoque le lien immédiatement. Fermer le dialogue le révoque également ; sinon il expire automatiquement dix minutes après sa création.

Le lien privé donne accès uniquement à ce ZIP. Il utilise le service invités sur le réseau local et ne donne aucun accès au studio, qui reste sur `127.0.0.1:8001`. Il ne passe pas par GitHub Pages ou par un tunnel Internet. Les téléphones en données mobiles ne peuvent pas l’utiliser. Si le réseau Wi-Fi isole les appareils ou si le pare-feu bloque le service invités, récupérez le ZIP sur le PC et copiez-le par USB, ou testez le réseau local avant la soirée. N’ouvrez pas le port administrateur sur le réseau.

## Importer une playlist entière

Un invité peut proposer une playlist depuis **Coller un lien YouTube → Ajouter ce lien**. Le site conserve une seule proposition pour ce lien complet. Pour une URL qui comporte vidéo et playlist, l’invité choisit **Ce morceau seulement** ou **Toute la playlist**. Sélectionner cette proposition dans le studio puis **Préparer les fichiers de la sélection** analyse son contenu, ajoute les morceaux disponibles à la file et signale les indisponibles ou toute analyse incomplète. Son ordre d’origine reste dans **Playlists enregistrées** et dans le pack.

Exemple dans l’administration :

1. Ouvrez **Import de playlists**. Collez `https://www.youtube.com/playlist?list=IDENTIFIANT_DE_VOTRE_PLAYLIST` en remplaçant l’identifiant par celui d’une playlist publique ou non répertoriée autorisée.
2. Cliquez sur **Analyser**. L’audio n’est pas téléchargé pendant cette étape. Vérifiez les titres, l’ordre, les éléments indisponibles et les avertissements.
3. Si votre URL comporte à la fois `v=` et `list=`, choisissez explicitement **ce morceau seulement** ou **toute la playlist**.
4. Sélectionnez tout ou décochez les versions indésirables. Une analyse interrompue ou limitée est signalée comme incomplète ; relancez-la après correction de sa cause. Par défaut, il n’y a pas de plafond de nombre d’éléments. Si vous avez volontairement défini une limite, **0** la désactive.
5. Cliquez sur **Importer et préparer les fichiers**, puis confirmez votre sélection et vos droits d’usage avant le lancement du lot. Cette confirmation ne vérifie pas juridiquement les droits. **Enregistrer sans télécharger** permet de conserver la playlist pour plus tard.
6. Consultez **Téléchargements**. Le worker traite un seul morceau à la fois, conserve la progression et les erreurs, et vérifie chaque fichier avant de le déclarer disponible. Réessayez uniquement les erreurs lorsque leur cause a été corrigée.

Vous pouvez également coller plusieurs liens individuels, un par ligne, ou choisir un fichier TXT contenant ces liens. Les chaînes, radios potentiellement infinies et directs en cours sont refusés. Une proposition d’invité ne lance aucun téléchargement.

Vous pouvez fermer votre navigateur pendant une tâche : le worker continue tant que le lanceur reste actif. **Suspendre la file** signifie arrêter avant le prochain morceau, après le traitement en cours.

Les très gros lots se préparent progressivement, un morceau à la fois. L’absence de plafond de nombre ne supprime pas le temps de traitement, l’espace disque nécessaire ni les limites de durée et de taille de chaque fichier. Prévoyez de préparer la musique assez longtemps avant la soirée.

Le total **Durée connue de toute la playlist** du site public porte sur les morceaux distincts dont la durée est connue. Les morceaux sans durée et les playlists non analysées restent à compléter ; ils ne représentent pas zéro minute de musique. Ce total reste partiel tant que ces informations manquent.

## Préparer le pack puis l’ouvrir dans Mixxx

Dans **Bibliothèque locale**, importez aussi vos propres fichiers audio si nécessaire. L’application copie les fichiers sans déplacer ni supprimer les originaux. Choisissez ensuite les morceaux effectivement disponibles dans **Packs & exports → Créer le ZIP de X fichiers**.

- Sur votre PC, vous pouvez conserver le dossier d’export sans ZIP.
- Vous pouvez aussi récupérer l’archive ZIP depuis l’administration et la décompresser dans le dossier de votre choix.
- Le pack contient ses fichiers `audio/`, la sélection et le classement en M3U8, les playlists importées dans leur ordre d’origine, le catalogue et les rapports d’erreurs.

Conservez la structure du pack quand vous le déplacez : les chemins des M3U8 sont relatifs. Dans Mixxx, ajoutez le dossier `audio/` à la bibliothèque et importez la playlist M3U8 depuis le pack décompressé. Vérifiez la lecture sur le PC de la soirée avant de partir ; l’application ne modifie pas la base de Mixxx.

Le choix MP3 favorise la compatibilité. Une conversion à un débit élevé n’améliore pas la qualité d’origine. Le profil `source` permet de conserver l’audio source lorsqu’il convient à votre lecteur.

La préparation écrit les fichiers audio sur **le PC qui exécute l’application**. Un ZIP récupéré dans le navigateur est enregistré sur **l’appareil qui ouvre le lien**, selon les réglages du navigateur. Le partage Wi-Fi permet donc de le recevoir sur un téléphone ou une tablette ; le site Pages seul ne prépare pas l’audio.

## Linux et Docker — options secondaires

Installation directe avec Python 3.12 ou plus, Node.js 20 ou plus, FFmpeg et ffprobe déjà installés :

```bash
bash scripts/install-linux.sh
.venv/bin/python -m playlist25 configure-admin
bash scripts/start.sh
# Depuis un autre terminal :
bash scripts/stop.sh
```

Pour sélectionner un Python précis : `PLAYLIST_PYTHON=python3.12 bash scripts/install-linux.sh`. L’installateur ne modifie pas les paquets système.

Docker Compose vise **Linux avec le réseau de l’hôte**. Ce choix garde l’administration sur le `127.0.0.1` du PC Linux et évite de publier son port sur toutes les interfaces. La recette n’est pas proposée pour Docker Desktop Windows : privilégiez les lanceurs Windows ci-dessus.

```bash
# Si votre utilisateur Linux n’a pas l’UID/GID 1000 :
export PLAYLIST_UID="$(id -u)"
export PLAYLIST_GID="$(id -g)"
docker compose build
docker compose run --rm playlist python -m playlist25 init
docker compose run --rm playlist python -m playlist25 configure-admin
docker compose up -d
docker compose logs --tail=100 playlist
# Arrêt sans effacer les fichiers :
docker compose down
```

Le checkout est monté dans `/app` pour conserver `.env.local` et `data/` lorsque le conteneur est recréé. Lancez les commandes depuis le projet. Ne démarrez pas simultanément les lanceurs natifs et Compose avec les mêmes ports ou la même base.

## Avant la soirée

- Vérifier chaque morceau voulu dans la bibliothèque : un lien enregistré ne suffit pas.
- Préparer le pack, le déplacer ou le copier sur son support final et vérifier quelques morceaux et playlists dans votre lecteur.
- Prévoir une copie indépendante du pack sur un second support.
- Activer **Mode soirée hors ligne** avant de couper Internet : recherche et téléchargements externes sont désactivés, catalogue et exports restent disponibles.
- Si les invités doivent encore proposer sur place, configurer le Wi-Fi local et le bon `LOCAL_URL`, puis tester un téléphone sur ce réseau.
- Vérifier les sorties audio et l’alimentation du PC. La lecture des fichiers dans Mixxx reste indépendante du serveur web.

## Pour déboguer

Dans la version autonome, commencez par **Réglages & diagnostics → Vérifier les prérequis**. Pour changer la connexion privée ou retrouver l’accès, cliquez sur **Arrêter le studio**, puis **Configurer la connexion** ou **Changer mon mot de passe** dans la fenêtre de l’application. **Ouvrir le studio** permet de rouvrir le navigateur.

Les commandes suivantes concernent uniquement la version source :

```powershell
.venv\Scripts\python.exe -m playlist25 doctor
.venv\Scripts\python.exe -m pytest
```

Sur Linux, remplacez `.venv\Scripts\python.exe` par `.venv/bin/python`. Consultez aussi **Réglages → Diagnostics** et les erreurs de chaque tâche.

- **ffmpeg/ffprobe absent** : installez FFmpeg, ouvrez un nouveau terminal ou configurez les chemins absolus.
- **Node absent ou trop ancien** : utilisez Node.js 20 ou plus et vérifiez `node --version`.
- **Recherche bloquée par YouTube** : l’erreur est affichée ; les liens et propositions manuelles restent enregistrables. N’importez pas de cookies personnels et ne désactivez pas les contrôles de connexion.
- **Port déjà utilisé / lancement déjà actif** : arrêtez le lancement précédent avec `stop`, ou choisissez deux ports libres différents.
- **Espace insuffisant** : libérez de l’espace dans le volume de `DATA_DIR`, puis relancez seulement les tâches en échec. Un export peut nécessiter de copier l’audio et de produire en plus un ZIP.
- **Session expirée / contrôle CSRF** : actualisez la page. Les données déjà enregistrées restent présentes.
- **Lien public expiré** : redémarrez le tunnel et mettez à jour le lien public ; le lien local est distinct.

Les essais automatisés utilisent des fournisseurs simulés et des fichiers de test autorisés. Les contrôles réels du paquet Windows et des services publics sont indiqués dans les notes de version et [le rapport de validation](VALIDATION.md). Aucun téléchargement réel de 10 000 ou 20 000 musiques ni aucun essai de Mixxx avec votre matériel n’est revendiqué. Vérifiez le studio, le transfert sur votre téléphone et la lecture du pack avant la soirée.

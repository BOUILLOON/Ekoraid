# Installation, lancement et préparation de la soirée

L’application locale réunit le site invités, l’administration privée et un processus de téléchargement. Elle conserve les propositions dans SQLite et les fichiers audio dans `data/audio/`. La collecte gratuite avec le PC éteint utilise GitHub Pages et Supabase : suivez [le guide cloud](../cloud/README.md). Le PC reste nécessaire au moment où vous préparez vos fichiers et vos exports.

## Windows 11 — installation recommandée

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

## Valeurs à personnaliser

Après `init`, ajoutez les paramètres souhaités de [.env.example](../.env.example) dans votre `.env.local`, en conservant `ADMIN_PASSWORD` et `SESSION_SECRET`. N’utilisez pas `.env.example` comme remplacement complet : ses commentaires ne contiennent pas de secrets utilisables.

| Paramètre | Exemple / rôle |
| --- | --- |
| `EVENT_NAME` | `"La playlist des 25 ans"` : valeur initiale du nom. |
| `DATA_DIR` | `data` ou `D:/Anniversaire25/donnees` : base, audio, temporaires et exports. |
| `GUEST_PORT` | `8000`, différent du port administrateur. |
| `ADMIN_PORT` | `8001`. `ADMIN_HOST` reste `127.0.0.1`. |
| `LOCAL_URL` | `http://192.168.1.25:8000` pour les téléphones du même Wi-Fi. |
| `PUBLIC_URL` | Adresse HTTPS du tunnel invités ou du site Pages. |
| `MAX_PROPOSALS` | `10` propositions par session invitée. |
| `MAX_PLAYLIST_ITEMS` | `500` éléments par analyse ; augmenter explicitement si nécessaire. |
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

## Importer une playlist entière

Exemple dans l’administration :

1. Ouvrez **Import de playlists**. Collez `https://www.youtube.com/playlist?list=IDENTIFIANT_DE_VOTRE_PLAYLIST` en remplaçant l’identifiant par celui d’une playlist publique ou non répertoriée autorisée.
2. Cliquez sur **Analyser**. L’audio n’est pas téléchargé pendant cette étape. Vérifiez les titres, l’ordre, les éléments indisponibles et les avertissements.
3. Si votre URL comporte à la fois `v=` et `list=`, choisissez explicitement **ce morceau seulement** ou **toute la playlist**.
4. Sélectionnez tout ou décochez les versions indésirables. Une analyse limitée ou interrompue est signalée comme incomplète : augmentez la limite dans les réglages puis analysez de nouveau si nécessaire.
5. Confirmez votre sélection et vos droits d’usage avant le lancement du lot. Cette confirmation ne vérifie pas juridiquement les droits.
6. Consultez **Téléchargements**. Le worker traite un seul morceau à la fois, conserve la progression et les erreurs, et vérifie chaque fichier avant de le déclarer disponible. Réessayez uniquement les erreurs lorsque leur cause a été corrigée.

Vous pouvez également coller plusieurs liens individuels, un par ligne, ou choisir un fichier TXT contenant ces liens. Les chaînes, radios potentiellement infinies et directs en cours sont refusés. Une proposition d’invité ne lance aucun téléchargement.

Vous pouvez fermer votre navigateur pendant une tâche : le worker continue tant que le lanceur reste actif. **Suspendre la file** signifie arrêter avant le prochain morceau, après le traitement en cours.

## Préparer le pack puis l’ouvrir dans Mixxx

Dans **Bibliothèque**, importez aussi vos propres fichiers audio si nécessaire. L’application copie les fichiers sans déplacer ni supprimer les originaux. Choisissez ensuite les morceaux effectivement disponibles dans **Exports → Préparer le pack pour Mixxx**.

- Sur votre PC, vous pouvez conserver le dossier d’export sans ZIP.
- Vous pouvez aussi récupérer l’archive ZIP depuis l’administration et la décompresser dans le dossier de votre choix.
- Le pack contient ses fichiers `audio/`, la sélection et le classement en M3U8, les playlists importées dans leur ordre d’origine, le catalogue et les rapports d’erreurs.

Conservez la structure du pack quand vous le déplacez : les chemins des M3U8 sont relatifs. Dans Mixxx, ajoutez le dossier `audio/` à la bibliothèque et importez la playlist M3U8 depuis le pack décompressé. Vérifiez la lecture sur le PC de la soirée avant de partir ; l’application ne modifie pas la base de Mixxx.

Le choix MP3 favorise la compatibilité. Une conversion à un débit élevé n’améliore pas la qualité d’origine. Le profil `source` permet de conserver l’audio source lorsqu’il convient à votre lecteur.

Les téléchargements sont écrits sur **la machine qui exécute le backend**. Un ZIP récupéré dans le navigateur est enregistré sur votre PC selon les réglages du navigateur ; un serveur distant ne peut pas écrire directement dans votre dossier Musique.

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

Les essais automatisés utilisent des fournisseurs simulés et des fichiers de test autorisés. Ils ne prouvent ni la disponibilité future de YouTube, ni le fonctionnement de votre matériel audio ou de Mixxx sur votre PC. Vérifiez ces points avant la soirée. Les essais réseau réels, quand ils sont exécutés, sont indiqués séparément dans le compte rendu de livraison.

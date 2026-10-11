# Fanny : se connecter, choisir les musiques et récupérer les fichiers

Pour les **25 ans de Fanny, le 17 octobre 2026 dès 19 h, à la salle des fêtes de Fontain**. Les invités ajoutent leurs idées sur le site public. Vous préparez les fichiers dans le studio privé de votre PC, puis vous récupérez un ZIP pour la soirée.

| Où aller | À quoi cela sert |
| --- | --- |
| [Site public](https://bouilloon.github.io/Ekoraid/) | Proposer, écouter un aperçu YouTube et voter. La collecte fonctionne même si votre PC est éteint. |
| [Studio privé sur votre PC](http://127.0.0.1:8001) | Administrer la collecte, préparer les fichiers audio et créer les packs. L’application doit être lancée sur ce PC. |
| `http://127.0.0.1:8000` | Tester le site invités local. Pour les téléphones du même Wi-Fi, utiliser l’adresse locale du PC affichée par le partage. |

**Il n’y a pas de connexion administrateur sur GitHub Pages.** Votre pseudo invité et votre mot de passe administrateur ont des rôles différents. Sur un téléphone, `127.0.0.1` désigne ce téléphone : cette adresse n’ouvre pas le studio de votre PC.

## 1. Installer le studio sur Windows

La version autonome **Studio-Fanny-Windows.zip est disponible** dans la [release Windows corrigée](https://github.com/BOUILLOON/Ekoraid/releases/tag/studio-windows-v2026.10.11.2). Son démarrage sans console, au premier lancement et avec une configuration existante, ainsi que la préparation audio ont été vérifiés sur Windows en CI.

1. [Téléchargez Studio-Fanny-Windows.zip](https://github.com/BOUILLOON/Ekoraid/releases/latest/download/Studio-Fanny-Windows.zip).
2. Faites un clic droit sur l’archive, puis **Extraire tout**. Gardez le dossier extrait entier.
3. Ouvrez le dossier `Studio-Fanny`, puis double-cliquez sur **Studio-Fanny.exe**.
4. Au premier démarrage, la fenêtre vous demande de choisir votre mot de passe administrateur : **au moins 12 caractères**. Conservez-le dans votre gestionnaire de mots de passe.

Python, Node.js, FFmpeg et ffprobe sont inclus. Vous n’avez pas à les installer séparément, à ouvrir un terminal ou à modifier un fichier de configuration. Le [guide d’installation](INSTALLATION.md#windows-11--version-autonome-recommandée) détaille ce parcours ; l’ancienne installation depuis les sources y reste disponible en option.

## 2. Relier ce PC à la collecte publique

Cette configuration se fait une seule fois sur votre PC, pendant le premier démarrage. Pour la faire plus tard, cliquez sur **Arrêter le studio**, puis sur **Configurer la connexion** dans la fenêtre de l’application.

1. Utilisez l’adresse du projet : `https://dpcohlyczczaqovblzin.supabase.co`.
2. Cliquez sur **Ouvrir la page de ma clé Supabase** et connectez-vous à votre compte Supabase.
3. Copiez la clé **Secret** ou **service_role** du projet, réservée à l’administration, dans le champ privé de l’application. Cliquez sur **Enregistrer et ouvrir le studio**.

La connexion est vérifiée avant son enregistrement. En cas de refus, la configuration précédente est conservée. La clé reste sur votre PC : ne la collez ni dans une conversation, ni dans le site public, ni dans une capture d’écran. Le studio peut démarrer sans cette connexion ; renseignez-la avant de récupérer la collecte publique. La clé publique du site ne donne pas ces droits d’administration.

Si vous utilisiez déjà les anciens scripts, arrêtez le studio, choisissez **Reprendre une ancienne installation** et sélectionnez le dossier qui contient votre `.env.local`. L’application réutilise cette installation sans déplacer ni supprimer ses fichiers. Ne remplacez pas votre ancienne configuration par un exemple.

## 3. Lancer et se connecter

Double-cliquez sur **Studio-Fanny.exe**. Si la connexion Supabase est configurée et le mode hors ligne désactivé, l’application récupère les propositions avant d’ouvrir le navigateur **de ce PC** ; aucun audio n’est préparé automatiquement. Sur la page privée, saisissez votre **Mot de passe administrateur**, puis cliquez sur **Ouvrir le studio**. Aucun compte invité n’est nécessaire pour ce studio.

Si vous avez fermé le navigateur, **Ouvrir le studio** dans la fenêtre de l’application le rouvre. L’adresse privée est <http://127.0.0.1:8001>. Gardez l’application ouverte pendant la préparation et les transferts ; vous pouvez fermer le navigateur.

Dans **Réglages & diagnostics**, cliquez sur **Vérifier les prérequis**. Vérifiez Python, yt-dlp, FFmpeg, ffprobe, Node.js et l’espace disque avant de lancer la préparation.

**Mot de passe oublié ?** Dans la fenêtre de l’application, utilisez **Arrêter le studio**, puis **Changer mon mot de passe**. Choisissez un nouveau mot de passe, puis **Enregistrer et ouvrir le studio**. Cette opération conserve la collecte et les fichiers audio ; elle nécessite l’accès à votre PC.

## 4. Récupérer les idées et gérer le site public

1. Ouvrez **Réglages & diagnostics**.
2. Cliquez sur **Récupérer les propositions du site public**. Attendez le message de réussite : les titres, pseudos et votes sont récupérés, sans télécharger de musique.
3. Consultez **Propositions**. Vous pourrez refaire cette récupération pour obtenir les nouveaux ajouts et les votes actualisés.

Pour ouvrir ou fermer les propositions et les votes, modifiez les interrupteurs du tableau de bord ou des réglages. Cliquez sur **Enregistrer les réglages** si vous utilisez le formulaire, puis sur **Publier les réglages et la modération sur le site public** pour transmettre vos décisions au site GitHub Pages.

**Enregistrer** applique un réglage localement ; **Publier** l’applique à la collecte publique. La publication transfère vos réglages et votre modération, sans publier de fichiers audio ni de clé privée.

Pour laisser les propositions illimitées, indiquez **0** dans **Propositions par invité**, puis enregistrez et publiez. La valeur **0** dans **Limite d’éléments par playlist** permet aussi de préparer une playlist entière sans plafond de nombre.

Sur le site public, **Durée connue de toute la playlist** additionne les durées disponibles des morceaux distincts : plusieurs propositions du même morceau ne le comptent pas plusieurs fois. Les durées inconnues restent **à compléter**. Une playlist proposée mais pas encore analysée a une durée inconnue, et non une durée de zéro minute ; ne considérez donc pas ce total partiel comme la durée complète de la soirée.

## 5. Choisir et préparer les musiques

1. Dans **Propositions**, vérifiez les titres et les versions. Pour une demande manuelle sans lien, cliquez sur **Modifier**, renseignez un **Lien YouTube précis**, puis **Enregistrer**. Vérifiez qu’il s’agit du morceau souhaité, pas d’une reprise ou d’un remix choisi par erreur.
2. Cochez quelques morceaux, ou cliquez sur **Tout sélectionner** pour choisir toutes les propositions correspondant au filtre, sur toutes les pages. **Valider la sélection** marque votre choix. **Sélectionner tous les validés** permet de reprendre ce groupe plus tard. Les propositions retirées par les invités sont exclues de cette sélection automatique.
3. Cliquez sur **Préparer les fichiers de la sélection**. Confirmez que vous disposez des autorisations nécessaires, puis cliquez sur **Confirmer la préparation**. Cette confirmation ne vérifie pas juridiquement les droits.
4. Ouvrez **Téléchargements** et attendez la fin du traitement. Un titre dans la liste n’est pas encore un fichier audio : il doit devenir **Disponible** après conversion et contrôle.

Le PC et l’application doivent rester allumés pendant la préparation. Vous pouvez fermer le navigateur. **Suspendre après le morceau en cours** laisse terminer ce morceau ; **Réessayer les échecs** relance les éléments concernés après correction de leur problème.

La file et les analyses n’ont pas de plafond de nombre par défaut. Un gros lot se prépare progressivement, un morceau à la fois : prévoyez suffisamment de temps et d’espace disque. Les fichiers prêts peuvent être récupérés pendant que le reste se prépare.

Une proposition portant la mention **Playlist YouTube** représente le lien de la playlist entière. Quand vous la sélectionnez puis lancez **Préparer les fichiers de la sélection**, le studio analyse son contenu avant d’ajouter les morceaux disponibles à la file. Dans **Téléchargements**, vérifiez les avertissements : éléments indisponibles et analyses incomplètes sont signalés. Vous retrouvez son contenu et son ordre d’origine dans **Import de playlists → Playlists enregistrées**. Une entrée indisponible reste dans le rapport ; elle ne produit pas de fichier audio.

Pour une playlist entière : **Import de playlists → Une playlist YouTube → Analyser**, vérifiez les éléments et avertissements, sélectionnez tout ou partie, puis confirmez la préparation. Si le lien contient une vidéo et une playlist, choisissez explicitement le morceau seul ou toute la playlist. **Plusieurs liens / fichier TXT** accepte aussi une URL de morceau par ligne.

Dans **Bibliothèque locale**, **Importer mon audio** ajoute une copie d’un fichier que vous possédez ; son original reste à sa place. Le lecteur local permet de vérifier vos fichiers prêts.

## 6. Créer le ZIP et l’enregistrer sur votre PC

1. Dans **Téléchargements**, cliquez sur **Récupérer les fichiers prêts en ZIP**. Vous pouvez aussi passer par **Bibliothèque locale → Récupérer mes fichiers en ZIP** ou **Packs & exports**.
2. Dans **Packs & exports**, cochez les fichiers voulus ou **Tout sélectionner**. Les fichiers absents ou encore en préparation ne sont pas proposés. Gardez **Créer une archive ZIP** activé.
3. Cliquez sur **Créer le ZIP de X fichiers**, où X correspond à votre sélection. L’application crée le pack sur le PC de préparation.
4. Cliquez ensuite sur **Télécharger le ZIP sur cet appareil**. Votre navigateur l’enregistre dans son dossier de téléchargements habituel ou vous demande un emplacement. Comme le studio est ouvert sur votre PC, c’est ici votre PC qui reçoit ce ZIP.
5. Décompressez le ZIP. Gardez le dossier `Anniversaire25` entier, avec `audio/` et les fichiers `.m3u8`. Vous pouvez le copier sur une clé USB ou un autre appareil.

**Récupérer les fichiers déjà prêts**, dans Propositions, ouvre **Packs & exports** avec les fichiers présents correspondant à votre sélection déjà cochés. Le CSV et le JSON contiennent les propositions et les votes ; ils ne remplacent pas le ZIP audio.

Dans Mixxx, ajoutez le dossier `audio/` à la bibliothèque, puis importez le M3U8 du pack. Vérifiez les morceaux et les playlists après avoir déplacé le dossier et coupé Internet. La lecture de ces fichiers reste possible si le site s’arrête.

## 7. Envoyer le ZIP à un téléphone sur votre Wi-Fi

Le téléphone peut **recevoir un pack déjà préparé**. Le site GitHub Pages sur le téléphone ne prépare pas l’audio à lui seul.

1. Créez d’abord le ZIP dans **Packs & exports**.
2. Connectez le PC et le téléphone au **même Wi-Fi**. Gardez l’application lancée sur le PC.
3. Cliquez sur **Partager sur mon Wi-Fi**. Dans la fenêtre, choisissez l’adresse du PC correspondant à ce Wi-Fi, puis cliquez sur **Créer le QR code de partage**.
4. Scannez le QR code avec le téléphone ou ouvrez le lien affiché. Sur le téléphone, cliquez sur **Télécharger le pack ZIP**.
5. Cliquez sur **Fermer le partage** quand le transfert est terminé.

Le lien permet uniquement de récupérer ce ZIP. Il expire après **10 minutes** et peut être fermé plus tôt. Le transfert passe par le service invités sur le port **8000**, sans accès au studio privé sur **8001**. Il nécessite le réseau local direct : le lien GitHub Pages et les tunnels Internet ne donnent pas accès à ce partage.

Si Windows demande une autorisation réseau, autorisez l’application uniquement sur votre **réseau privé de confiance**. Gardez l’administration sur `127.0.0.1` ; n’ouvrez pas son port sur le réseau. Si le téléphone ne joint pas le PC, vérifiez aussi que le Wi-Fi n’isole pas les appareils entre eux. Une copie par câble USB reste possible.

## 8. Aider les invités

Pour proposer une playlist, ouvrez **Coller un lien YouTube**, collez son lien, puis **Ajouter ce lien**. Si le lien contient aussi une vidéo, la fenêtre **Ce morceau ou la playlist ?** propose **Ce morceau seulement** ou **Toute la playlist**. Le choix de la playlist ajoute une seule proposition pour ce lien ; l’organisation en retrouve les morceaux lors de la préparation sur le PC.

Sur le site public, l’invité clique sur sa photo ou son pseudo dans l’en-tête pour ouvrir **Mon profil**.

1. Cliquez sur **Choisir une photo** et sélectionnez une image **PNG, JPEG ou WebP**, jusqu’à **10 Mo**. Elle est recadrée en carré et réduite à **128 × 128 pixels** ; vérifiez l’aperçu.
2. Cliquez sur **Enregistrer ma photo** pour confirmer. **Annuler** conserve la photo précédente.
3. Pour revenir à l’initiale du pseudo, cliquez sur **Retirer ma photo**, puis sur **Enregistrer ma photo**. Cela conserve le pseudo, les propositions et les votes.

Il n’y a pas de compte à créer ni d’email à fournir. Le profil appartient à l’identité enregistrée dans ce navigateur : saisir le même pseudo dans un autre navigateur ne permet pas de modifier cette photo ou ces propositions.

Sur le site public : **Mes propositions → Retirer ma proposition**. La fenêtre **Retirer ce morceau ?** affiche le titre et l’avertissement **ATTENTION**. **Garder mon morceau** annule ; **Retirer ma proposition** confirme le retrait de sa propre proposition.

L’invité doit utiliser **le même navigateur sur le même site**, avec son identité encore enregistrée. Saisir le même pseudo ailleurs ne récupère pas cette identité. Si elle a été perdue, demandez à l’organisation d’archiver la proposition depuis le studio.

Le retrait masque la proposition dans la liste publique. L’historique, les votes conservés pour l’organisation et les fichiers audio déjà préparés restent dans votre administration. Un invité ne peut pas retirer les morceaux d’une autre personne.

Le bouton **Écouter** ouvre un aperçu YouTube uniquement après un clic. Il nécessite Internet ; écouter cet aperçu ne prépare pas le fichier du pack.

## 9. Avant la soirée et pour dépanner

- Refaites **Récupérer les propositions du site public**, terminez votre sélection et publiez la fermeture des ajouts/votes si souhaité.
- Vérifiez que tous les morceaux retenus sont disponibles. Consultez les erreurs restantes et le rapport du pack.
- Essayez le pack décompressé dans Mixxx, avec Internet coupé, sur le matériel audio de la soirée. Gardez une deuxième copie sur un autre support.
- Pour sauvegarder la préparation, cliquez sur **Arrêter le studio**, puis copiez le dossier `%LOCALAPPDATA%\StudioFanny` complet vers un support privé. Collez ce chemin dans la barre d’adresse de l’Explorateur Windows pour le trouver. Si vous avez repris une ancienne installation, sauvegardez aussi ce dossier, avec sa configuration privée et ses données.
- Activez **Mode soirée hors ligne** avant de couper Internet. Les fichiers locaux restent utilisables ; la recherche YouTube et les nouveaux téléchargements sont désactivés. Le site public GitHub Pages nécessite Internet et reste distinct du Wi-Fi de la salle.

Si le studio ne s’ouvre pas, vérifiez le message dans la fenêtre de l’application et cliquez sur **Ouvrir le studio**. Pour un diagnostic, utilisez **Réglages & diagnostics → Vérifier les prérequis**. Pour corriger Supabase, arrêtez le studio, puis utilisez **Configurer la connexion**. **Ouvrir mes fichiers** ouvre le dossier des données, de l’audio et des exports.

Si une erreur **`[WinError 6]`** apparaît avant toute fenêtre du studio, [téléchargez le ZIP corrigé](https://github.com/BOUILLOON/Ekoraid/releases/latest/download/Studio-Fanny-Windows.zip) de la [version `.2`](https://github.com/BOUILLOON/Ekoraid/releases/tag/studio-windows-v2026.10.11.2), vérifiée sans console. Faites **Extraire tout** dans un nouveau dossier et gardez tout son contenu ensemble. Conservez `%LOCALAPPDATA%\StudioFanny` et le dossier de votre ancienne installation, avec leur configuration privée : vos données restent à leur emplacement, sans connexion Supabase à refaire.

Si Windows affiche exactement **« Windows a protégé votre ordinateur »**, pour ce ZIP provenant de la release officielle, cliquez sur **Informations complémentaires / More info**, puis **Exécuter quand même / Run anyway**. Cet avertissement SmartScreen est différent de WinError 6. La mention **Éditeur inconnu** peut rester, car l’application n’a pas de signature d’éditeur. Le [guide d’installation](INSTALLATION.md#pour-déboguer) détaille ces deux situations.

Un refus de récupération cloud demande de vérifier la clé privée et le projet Supabase. Une erreur YouTube ne supprime pas les demandes enregistrées. Un manque d’espace exige de libérer de la place avant de réessayer ; le pack et son ZIP occupent de l’espace supplémentaire.

Pour mettre à jour l’application, arrêtez le studio, extrayez la nouvelle archive dans un nouveau dossier, puis ouvrez son `Studio-Fanny.exe`. Vos données personnelles sont séparées du programme ; la mise à jour ne supprime pas les propositions du site public.

Les essais du projet sont détaillés dans [le compte rendu de validation](VALIDATION.md) et les notes de la release Windows. Aucun téléchargement réel de 10 000 ou 20 000 musiques n’est revendiqué. Vérifiez le démarrage sur votre PC, le transfert sur votre téléphone et la lecture dans Mixxx avec votre matériel avant la soirée.

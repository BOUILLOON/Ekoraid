# Fanny : se connecter, choisir les musiques et récupérer les fichiers

Pour les **25 ans de Fanny, le 17 octobre 2026 dès 19 h, à la salle des fêtes de Fontain**. Les invités ajoutent leurs idées sur le site public. Vous préparez les fichiers dans le studio privé de votre PC, puis vous récupérez un ZIP pour la soirée.

| Où aller | À quoi cela sert |
| --- | --- |
| [Site public](https://bouilloon.github.io/Ekoraid/) | Proposer, écouter un aperçu YouTube et voter. La collecte fonctionne même si votre PC est éteint. |
| [Studio privé sur votre PC](http://127.0.0.1:8001) | Administrer la collecte, préparer les fichiers audio et créer les packs. L’application doit être lancée sur ce PC. |
| `http://127.0.0.1:8000` | Tester le site invités local. Pour les téléphones du même Wi-Fi, utiliser l’adresse locale du PC affichée par le partage. |

**Il n’y a pas de connexion administrateur sur GitHub Pages.** Votre pseudo invité et votre mot de passe administrateur ont des rôles différents. Sur un téléphone, `127.0.0.1` désigne ce téléphone : cette adresse n’ouvre pas le studio de votre PC.

## 1. Installer le studio sur Windows

La livraison Windows **Studio-Fanny-Windows.zip** se télécharge depuis la [dernière release Windows](https://github.com/BOUILLOON/Ekoraid/releases/latest). Elle comprend le studio et ses outils ; les contrôles réalisés sont indiqués dans les notes de la release et le [rapport de validation](VALIDATION.md).

1. [Téléchargez Studio-Fanny-Windows.zip](https://github.com/BOUILLOON/Ekoraid/releases/latest/download/Studio-Fanny-Windows.zip).
2. Faites un clic droit sur l’archive, puis **Extraire tout**. Gardez le dossier extrait entier.
3. Ouvrez le dossier `Studio-Fanny`, puis double-cliquez sur **Studio-Fanny.exe**.
4. Au premier démarrage, la fenêtre vous demande de choisir votre mot de passe administrateur : **au moins 12 caractères**. Conservez-le dans votre gestionnaire de mots de passe.

Python, Node.js, FFmpeg et ffprobe sont inclus. Vous n’avez pas à les installer séparément, à ouvrir un terminal ou à modifier un fichier de configuration. Le [guide d’installation](INSTALLATION.md#windows-11--version-autonome-recommandée) détaille ce parcours ; l’ancienne installation depuis les sources y reste disponible en option.

## 2. Relier ce PC à la collecte publique

Cette configuration se fait sur votre PC dans le **navigateur privé**, sans arrêter le studio. Depuis <http://127.0.0.1:8001>, ouvrez **Réglages & diagnostics**, puis le formulaire de connexion à Supabase.

1. Dans **Adresse du projet Supabase**, utilisez `https://dpcohlyczczaqovblzin.supabase.co`.
2. Dans le tableau de bord Supabase du projet, ouvrez **Project Settings → API Keys** et copiez la clé **Secret** ou **service_role**. La clé publique et un jeton de compte personnel ne conviennent pas.
3. Collez cette clé dans le champ privé du studio et cliquez sur **Vérifier et enregistrer la connexion**. Une clé laissée vide conserve celle déjà configurée ; elle n’est jamais réaffichée dans le formulaire.
4. Après le message de réussite, cliquez sur **Récupérer les propositions du site public**. Les titres et votes sont récupérés, sans préparer d’audio.

La connexion est vérifiée avant son enregistrement et s’applique immédiatement. En cas de refus, la configuration précédente et vos fichiers sont conservés. La clé reste sur votre PC : ne la collez ni dans une conversation, ni dans le site public, ni dans une capture d’écran. Le studio peut démarrer sans cette connexion ; renseignez-la avant de récupérer la collecte publique. La clé publique du site ne donne pas ces droits d’administration.

Le formulaire natif du premier démarrage reste utilisable. Si vous préférez configurer depuis la fenêtre de l’application, cliquez sur **Arrêter le studio**, puis **Configurer la connexion → Enregistrer et ouvrir le studio**. Cet arrêt concerne seulement cette méthode alternative ; le formulaire du navigateur n’en a pas besoin.

Sur un second PC, la connexion au même projet Supabase retrouve la même collecte ; elle ne la réinitialise pas. Les invités continuent à proposer et voter sur le même lien pendant que vous arrêtez, mettez à jour ou relancez le studio. Pour transporter aussi vos fichiers audio déjà préparés, utilisez la sauvegarde et la restauration décrites plus bas.

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

1. Dans **Propositions**, vérifiez les titres et les versions. Pour une demande manuelle sans lien, cliquez sur **Modifier**. Le champ de recherche reprend le titre et l’artiste : cliquez sur **Rechercher sur YouTube**, comparez la chaîne et la durée, puis **Choisir cette version**. **Voir d’autres versions** affiche la suite des résultats. Le choix remplit le formulaire ; seul **Enregistrer** associe la vidéo après vérification de ses métadonnées par le serveur. **Annuler** conserve la demande initiale. Vous pouvez aussi coller directement un **Lien YouTube précis**.
2. Cochez quelques morceaux, ou cliquez sur **Tout sélectionner** pour choisir toutes les propositions correspondant au filtre, sur toutes les pages. **Valider la sélection** marque votre choix. **Sélectionner tous les validés** permet de reprendre ce groupe plus tard. Les propositions retirées par les invités sont exclues de cette sélection automatique.
3. Cliquez sur **Préparer les fichiers de la sélection**. Confirmez que vous disposez des autorisations nécessaires, puis cliquez sur **Confirmer la préparation**. Cette confirmation ne vérifie pas juridiquement les droits.
4. Ouvrez **Téléchargements** et attendez la fin du traitement. Un titre dans la liste n’est pas encore un fichier audio : il doit devenir **Disponible** après conversion et contrôle.

Le PC et l’application doivent rester allumés pendant la préparation. Vous pouvez fermer le navigateur. **Suspendre après le morceau en cours** laisse terminer ce morceau ; **Réessayer les échecs** relance les éléments concernés après correction de leur problème.

La recherche et l’association d’une source ne préparent aucun audio et conservent l’auteur, les votes et l’historique. Si le fichier de la version choisie existe déjà, il est réutilisé avec son nom et ses métadonnées. Pour modifier un fichier préparé, utilisez explicitement **Bibliothèque locale → Renommer**.

Dans **Réglages & diagnostics**, choisissez le format **MP3 — compatibilité simple**, puis **Enregistrer les réglages**, pour préparer des `.mp3` destinés au pack. La préparation convertit et vérifie les nouveaux fichiers avant de les marquer disponibles. Ce réglage ne convertit pas rétroactivement les fichiers déjà prêts ; vérifiez leur format dans votre pack. La conversion n’améliore pas la qualité de la source.

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

**Packs déjà créés** permet de retrouver les ZIP encore présents dans cette installation après la fermeture du navigateur ou un redémarrage du studio. Choisissez l’archive voulue pour la récupérer sans refaire la préparation du pack. Une archive déplacée ou supprimée n’est plus proposée comme disponible. Les sauvegardes du studio n’incluent pas ces exports : sur un autre PC, copiez le ZIP final séparément ou recréez-le depuis votre bibliothèque audio restaurée.

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

**Plus de résultats** affiche d’autres résultats de recherche à la demande. Les repères live, reprise, remix ou instrumental proviennent du titre et restent indicatifs : ils ne certifient ni l’artiste ni la version. Les brouillons de recherche, lien, titre et artiste restent dans le même navigateur après une coupure ou une actualisation ; l’invité doit toujours confirmer l’ajout lui-même. Aucun ajout ni recherche n’est lancé automatiquement à la réouverture. Une erreur, un doublon ou une annulation conserve le brouillon ; un ajout réussi vide seulement les champs du mode envoyé s’ils n’ont pas été modifiés pendant la requête. Le catalogue conserve le défilement et le focus et propose **Afficher les nouveaux morceaux** lors de nouveaux ajouts ; ce bouton affiche ensuite les propositions les plus récentes.

### Sauvegarder la liste publique sur cet appareil

Sur le site public, cliquez sur **Sauvegarder la liste**, puis choisissez :

- **Enregistrer la liste CSV** : toute la collecte publique, même si vous consultez un filtre ou une autre page, avec titres, artistes, liens, auteurs, votes, durées et catégories. Les demandes sans lien restent présentes.
- **Enregistrer les liens TXT** : un lien YouTube par ligne, sans doublons, playlists comprises. Les demandes sans lien restent disponibles dans le CSV.

Le navigateur enregistre le fichier sur l’appareil utilisé. Cette exportation fonctionne PC éteint ; elle ne contient ni audio, ni photos, ni données de connexion. Elle ne prépare pas les MP3 et ne remplace pas une sauvegarde complète du studio.

La liste est rechargée avant l’export si la connexion fonctionne. En cas de coupure, une liste déjà chargée sur cette page peut être enregistrée comme **copie datée** ; l’avertissement précise que des ajouts ou votes plus récents peuvent manquer. Sans liste déjà chargée, attendez le retour de la connexion.

### Catégories et accès organisation sur le site public

Le site public permet de créer les **moments de la soirée** et de classer les propositions, sans laisser le PC allumé. Ouvrez **Espace organisation → Mon accès organisation** et connectez-vous avec le compte Supabase autorisé `info@bouillon.run`. Ce mot de passe appartient au compte Supabase ; il ne s’agit ni de la clé privée du projet, ni du mot de passe local du studio. Un pseudo invité ne donne pas cet accès.

Si ce compte n’existe pas, **Créer mon accès** demande un mot de passe d’au moins 12 caractères et une confirmation par email. La création ne vous connecte pas automatiquement. Une adresse différente de celle autorisée ne reçoit pas les droits d’organisation.

Si le mail de confirmation ne peut pas être envoyé ou n’arrive pas, le propriétaire du projet peut créer **ce seul compte** depuis le tableau de bord Supabase, une fois :

1. Ouvrez le projet de la collecte, puis **Authentication → Users → Add user → Create user**.
2. Saisissez `info@bouillon.run` et choisissez vous-même un mot de passe privé. Ne le partagez pas dans une conversation ou une capture d’écran.
3. Cochez **Auto Confirm User** pour ce compte uniquement, puis confirmez sa création. Gardez la confirmation globale des autres comptes activée.
4. Revenez sur le site public, ouvrez **Espace organisation** et cliquez sur **Se connecter** avec cet email et ce mot de passe.

Le service email par défaut de Supabase peut refuser des destinataires selon sa configuration. Ces instructions ne supposent pas qu’un compte ou un email a déjà été créé ou envoyé. Si la connexion est refusée, vérifiez dans **Authentication → Users** que le compte existe et est confirmé ; l’adresse doit aussi être autorisée pour l’événement.

Après connexion, **Voir des idées de moments** affiche des suggestions privées avec leurs explications. Rien n’est créé ni publié automatiquement. Pour retenir une catégorie, choisissez son nom et son ordre, puis confirmez **Créer ce moment**. La case **Proposer ce moment aux invités** décide si elle est disponible publiquement. Vous pouvez ensuite renommer, réordonner ou archiver les moments ; l’archivage conserve les anciens classements et ne supprime aucun morceau.

Sur une carte, **Choisir les moments** ouvre **Classer ce morceau** ; **Enregistrer les moments** confirme une ou plusieurs catégories. Un invité peut classer ses propres propositions ; l’organisation autorisée peut classer celles de tous les invités. Une catégorie archivée peut rester dans un classement existant ou être retirée, mais n’est plus proposée pour de nouveaux classements. **Se déconnecter** ferme l’accès organisation de cet onglet.

Cet accès public organise les catégories. Les vrais fichiers MP3 et leurs ZIP se préparent dans le studio local, sur le PC qui exécute l’application.

## 9. Sauvegarder et continuer sur un autre PC

Une sauvegarde du studio est différente d’un pack audio pour la soirée. Elle contient la configuration privée, la base des propositions et votes, les profils et photos, et les fichiers audio locaux. Elle contient aussi le mot de passe et la clé privée Supabase : gardez ce ZIP confidentiel sur un support privé. Les exports et fichiers temporaires ne sont pas inclus ; conservez séparément votre pack final.

1. Sur le premier PC, cliquez sur **Sauvegarder mon studio** dans la fenêtre de l’application et choisissez où enregistrer le ZIP. L’application arrête proprement le studio avant la copie et reprend ensuite s’il tournait. Attendez le résumé de réussite. La collecte publique continue pendant cette opération.
2. Copiez cette sauvegarde sur le second PC. Installez l’application Windows, puis cliquez sur **Restaurer une sauvegarde**, également disponible dans le formulaire du premier lancement. Vous utiliserez le mot de passe de la sauvegarde sans créer une nouvelle configuration.
3. Choisissez le ZIP, puis le dossier parent du nouveau studio. Vérifiez sa date et l’inventaire des propositions, invités, votes, photos et fichiers audio. Confirmez **Restaurer cette sauvegarde ?**. Une annulation conserve l’installation actuelle.
4. La restauration crée un nouveau dossier `StudioFanny-restaure-…`, sans écraser l’ancien. L’application adopte ce dossier et relance le studio ; si elle indique que la relance automatique a échoué, fermez la fenêtre et rouvrez l’application.
5. La file de préparation reste suspendue. Avant de reprendre, cliquez sur **Récupérer les propositions du site public** pour obtenir les ajouts et votes postérieurs à la sauvegarde. Désactivez le mode hors ligne s’il était enregistré.

La connexion privée et les données locales sont reprises, tandis que l’ancien dossier reste disponible comme retour arrière. La restauration ne modifie ni ne réinitialise Supabase et ne publie pas automatiquement les réglages ou la modération de la sauvegarde. Vérifiez-les avant une publication explicite vers le site public. Sur un PC sans sauvegarde, vous pouvez simplement configurer la même connexion pour retrouver les informations publiques ; ses fichiers audio devront être préparés ou transférés séparément.

## 10. Avant la soirée et pour dépanner

- Refaites **Récupérer les propositions du site public**, terminez votre sélection et publiez la fermeture des ajouts/votes si souhaité.
- Vérifiez que tous les morceaux retenus sont disponibles. Consultez les erreurs restantes et le rapport du pack.
- Essayez le pack décompressé dans Mixxx, avec Internet coupé, sur le matériel audio de la soirée. Gardez une deuxième copie sur un autre support.
- Pour sauvegarder la préparation, utilisez **Sauvegarder mon studio** et gardez le ZIP privé sur un second support, en plus du pack audio final. Pour trouver vos fichiers, **Ouvrir mes fichiers** ouvre leur dossier. Par défaut, il est dans `%LOCALAPPDATA%\StudioFanny` ; une installation restaurée ou reprise peut utiliser un autre dossier.
- Activez **Mode soirée hors ligne** avant de couper Internet. Les fichiers locaux restent utilisables ; la recherche YouTube et les nouveaux téléchargements sont désactivés. Le site public GitHub Pages nécessite Internet et reste distinct du Wi-Fi de la salle.

Si le studio ne s’ouvre pas, vérifiez le message dans la fenêtre de l’application et cliquez sur **Ouvrir le diagnostic**. Ce bouton reste disponible sans configuration ou après l’échec des services et ne modifie pas la collecte. Dans le navigateur privé, **Réglages & diagnostics → Vérifier les prérequis** contrôle aussi les outils. Si ce navigateur répond, corrigez directement la connexion Supabase dans ses réglages, sans arrêter le studio. Le formulaire natif après arrêt reste une alternative. **Ouvrir mes fichiers** ouvre le dossier des données, de l’audio et des exports.

Si la fenêtre ne démarre pas, double-cliquez sur **Diagnostic-Studio.cmd**, à côté de `Studio-Fanny.exe`. Il ouvre un rapport texte dans le Bloc-notes, ou les consignes locales si l’exécutable est bloqué avant de produire le diagnostic. Aucun PowerShell ni droit administrateur n’est requis. Le rapport exclut les mots de passe, clés privées et titres des invités ; il ne change aucune donnée publique. Le lanceur produit aussi un rapport automatiquement lorsqu’une erreur de démarrage peut être capturée.

Si une erreur **`[WinError 6]`** apparaît avant toute fenêtre du studio, ouvrez ce diagnostic et [téléchargez le ZIP](https://github.com/BOUILLOON/Ekoraid/releases/latest/download/Studio-Fanny-Windows.zip) de la [dernière release Windows](https://github.com/BOUILLOON/Ekoraid/releases/latest). Faites **Extraire tout** dans un nouveau dossier et gardez tout son contenu ensemble. Conservez `%LOCALAPPDATA%\StudioFanny` et le dossier de votre ancienne installation, avec leur configuration privée : vos données restent à leur emplacement, sans connexion Supabase à refaire.

Si Windows affiche exactement **« Windows a protégé votre ordinateur »**, pour ce ZIP provenant de la release officielle, cliquez sur **Informations complémentaires / More info**, puis **Exécuter quand même / Run anyway**. Cet avertissement SmartScreen est différent de WinError 6. La mention **Éditeur inconnu** peut rester, car l’application n’a pas de signature d’éditeur. Le [guide d’installation](INSTALLATION.md#pour-déboguer) détaille ces deux situations.

Un refus de récupération cloud demande de vérifier la clé privée et le projet Supabase. Une erreur YouTube ne supprime pas les demandes enregistrées. Un manque d’espace exige de libérer de la place avant de réessayer ; le pack et son ZIP occupent de l’espace supplémentaire.

Pour mettre à jour l’application, arrêtez le studio, extrayez la nouvelle archive dans un nouveau dossier, puis ouvrez son `Studio-Fanny.exe`. Vos données personnelles sont séparées du programme ; la mise à jour ne supprime pas les propositions du site public.

Les essais du projet sont détaillés dans [le compte rendu de validation](VALIDATION.md) et les notes de la release Windows. Aucun téléchargement réel de 10 000 ou 20 000 musiques n’est revendiqué. Vérifiez le démarrage sur votre PC, le transfert sur votre téléphone et la lecture dans Mixxx avec votre matériel avant la soirée.

# Fanny : se connecter, choisir les musiques et récupérer les fichiers

Pour les **25 ans de Fanny, le 17 octobre 2026 dès 19 h, à la salle des fêtes de Fontain**. Les invités ajoutent leurs idées sur le site public. Vous préparez les fichiers dans le studio privé de votre PC, puis vous récupérez un ZIP pour la soirée.

| Où aller | À quoi cela sert |
| --- | --- |
| [Site public](https://bouilloon.github.io/Ekoraid/) | Proposer, écouter un aperçu YouTube et voter. La collecte fonctionne même si votre PC est éteint. |
| [Studio privé sur votre PC](http://127.0.0.1:8001) | Administrer la collecte, préparer les fichiers audio et créer les packs. L’application doit être lancée sur ce PC. |
| `http://127.0.0.1:8000` | Tester le site invités local. Pour les téléphones du même Wi-Fi, utiliser l’adresse locale du PC affichée par le partage. |

**Il n’y a pas de connexion administrateur sur GitHub Pages.** Votre pseudo invité et votre mot de passe administrateur ont des rôles différents. Sur un téléphone, `127.0.0.1` désigne ce téléphone : cette adresse n’ouvre pas le studio de votre PC.

## 1. Installer le studio sur Windows

Installez **Python 3.12 ou plus**, **Node.js 20 ou plus** et **FFmpeg avec ffprobe**. Le [guide d’installation](INSTALLATION.md#windows-11--installation-recommandée) donne les liens, les commandes `winget` facultatives et la solution si PowerShell refuse les scripts. Il n’est pas nécessaire de désactiver une protection Windows.

Depuis [le dépôt GitHub](https://github.com/BOUILLOON/Ekoraid), **Code → Download ZIP** permet de récupérer le projet complet. Décompressez-le dans un dossier de votre PC. Ouvrez un terminal dans ce dossier, celui qui contient `install-windows.ps1` et `Start-Windows.ps1`.

```powershell
powershell -NoProfile -File .\install-windows.ps1
.venv\Scripts\python.exe -m playlist25 configure-admin
```

La deuxième commande vous demande un **mot de passe d’au moins 12 caractères**, puis sa confirmation. La saisie reste invisible dans le terminal. Conservez ce mot de passe dans votre gestionnaire de mots de passe.

L’installation crée le fichier privé `.env.local`. Conservez les lignes `ADMIN_PASSWORD` et `SESSION_SECRET` de ce fichier : ne le remplacez pas par `.env.example` et ne l’envoyez pas sur GitHub.

## 2. Relier ce PC à la collecte publique

Cette configuration se fait une seule fois sur votre PC. Ouvrez le projet Supabase dans [son tableau de bord](https://supabase.com/dashboard), puis **Project Settings → API Keys**. Copiez sa clé **Secret** ou **service_role**, réservée à l’administration. La clé publique du site ne suffit pas pour récupérer et administrer la collecte.

Ouvrez `.env.local`, dans le dossier du projet, avec Bloc-notes ou votre éditeur. Ajoutez les lignes suivantes, en remplaçant uniquement le texte de remplacement de la clé par la valeur copiée :

```dotenv
SUPABASE_URL=https://dpcohlyczczaqovblzin.supabase.co
SUPABASE_SERVICE_ROLE_KEY=COLLEZ_ICI_LA_CLE_PRIVEE
PUBLIC_URL=https://bouilloon.github.io/Ekoraid/
```

Conservez toutes les autres lignes existantes. Si ces paramètres sont déjà présents, corrigez leurs lignes plutôt que de les ajouter une deuxième fois. La clé privée reste dans ce fichier sur votre PC : ne la collez ni dans une conversation, ni dans le site public, ni dans une capture d’écran. Le [guide cloud](../cloud/README.md#4-récupérer-la-collecte-sur-votre-pc) détaille cette connexion.

## 3. Lancer et se connecter

Dans le dossier du projet :

```powershell
powershell -NoProfile -File .\Start-Windows.ps1
```

Laissez cette fenêtre ouverte. Dans le navigateur **de ce PC**, ouvrez <http://127.0.0.1:8001>, saisissez votre **Mot de passe administrateur**, puis cliquez sur **Ouvrir le studio**. Aucun compte invité n’est nécessaire pour ce studio.

Dans **Réglages & diagnostics**, cliquez sur **Vérifier les prérequis**. Vérifiez Python, yt-dlp, FFmpeg, ffprobe, Node.js et l’espace disque avant de lancer la préparation.

**Mot de passe oublié ?** Arrêtez l’application, puis choisissez-en un nouveau depuis le dossier du projet :

```powershell
.venv\Scripts\python.exe -m playlist25 stop
.venv\Scripts\python.exe -m playlist25 configure-admin
powershell -NoProfile -File .\Start-Windows.ps1
```

L’ancien mot de passe n’est pas demandé. Cette opération conserve la collecte et les fichiers audio. Elle nécessite l’accès à votre PC et au dossier du projet.

## 4. Récupérer les idées et gérer le site public

1. Ouvrez **Réglages & diagnostics**.
2. Cliquez sur **Récupérer les propositions du site public**. Attendez le message de réussite : les titres, pseudos et votes sont récupérés, sans télécharger de musique.
3. Consultez **Propositions**. Vous pourrez refaire cette récupération pour obtenir les nouveaux ajouts et les votes actualisés.

Pour ouvrir ou fermer les propositions et les votes, modifiez les interrupteurs du tableau de bord ou des réglages. Cliquez sur **Enregistrer les réglages** si vous utilisez le formulaire, puis sur **Publier les réglages et la modération sur le site public** pour transmettre vos décisions au site GitHub Pages.

**Enregistrer** applique un réglage localement ; **Publier** l’applique à la collecte publique. La publication transfère vos réglages et votre modération, sans publier de fichiers audio ni de clé privée.

## 5. Choisir et préparer les musiques

1. Dans **Propositions**, vérifiez les titres et les versions. Pour une demande manuelle sans lien, cliquez sur **Modifier**, renseignez un **Lien YouTube précis**, puis **Enregistrer**. Vérifiez qu’il s’agit du morceau souhaité, pas d’une reprise ou d’un remix choisi par erreur.
2. Cochez quelques morceaux, ou cliquez sur **Tout sélectionner** pour choisir les propositions affichées. **Valider la sélection** marque votre choix. **Sélectionner tous les validés** permet de reprendre ce groupe plus tard. Les propositions retirées par les invités sont exclues de cette sélection automatique.
3. Cliquez sur **Préparer les fichiers de la sélection**. Confirmez que vous disposez des autorisations nécessaires, puis cliquez sur **Confirmer la préparation**. Cette confirmation ne vérifie pas juridiquement les droits.
4. Ouvrez **Téléchargements** et attendez la fin du traitement. Un titre dans la liste n’est pas encore un fichier audio : il doit devenir **Disponible** après conversion et contrôle.

Le PC et l’application doivent rester allumés pendant la préparation. Vous pouvez fermer le navigateur. **Suspendre après le morceau en cours** laisse terminer ce morceau ; **Réessayer les échecs** relance les éléments concernés après correction de leur problème.

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

## 8. Aider un invité à retirer sa proposition

Sur le site public : **Mes propositions → Retirer ma proposition**. La fenêtre **Retirer ce morceau ?** affiche le titre et l’avertissement **ATTENTION**. **Garder mon morceau** annule ; **Retirer ma proposition** confirme le retrait de sa propre proposition.

L’invité doit utiliser **le même navigateur sur le même site**, avec son identité encore enregistrée. Saisir le même pseudo ailleurs ne récupère pas cette identité. Si elle a été perdue, demandez à l’organisation d’archiver la proposition depuis le studio.

Le retrait masque la proposition dans la liste publique. L’historique, les votes conservés pour l’organisation et les fichiers audio déjà préparés restent dans votre administration. Un invité ne peut pas retirer les morceaux d’une autre personne.

Le bouton **Écouter** ouvre un aperçu YouTube uniquement après un clic. Il nécessite Internet ; écouter cet aperçu ne prépare pas le fichier du pack.

## 9. Avant la soirée et pour dépanner

- Refaites **Récupérer les propositions du site public**, terminez votre sélection et publiez la fermeture des ajouts/votes si souhaité.
- Vérifiez que tous les morceaux retenus sont disponibles. Consultez les erreurs restantes et le rapport du pack.
- Essayez le pack décompressé dans Mixxx, avec Internet coupé, sur le matériel audio de la soirée. Gardez une deuxième copie sur un autre support.
- Pour sauvegarder la préparation, arrêtez les services avec `Stop-Windows.ps1` ou `playlist25 stop`, puis copiez le dossier `data/` complet, qui contient notamment la base, l’audio et les exports. Si vous avez choisi un autre dossier de données, sauvegardez celui-ci. Conservez aussi `.env.local` dans une sauvegarde privée.
- Activez **Mode soirée hors ligne** avant de couper Internet. Les fichiers locaux restent utilisables ; la recherche YouTube et les nouveaux téléchargements sont désactivés. Le site public GitHub Pages nécessite Internet et reste distinct du Wi-Fi de la salle.

Si le studio ne s’ouvre pas, vérifiez la fenêtre de lancement et l’adresse `http://127.0.0.1:8001`. Après une modification de `.env.local`, arrêtez et relancez l’application. Pour obtenir un diagnostic :

```powershell
.venv\Scripts\python.exe -m playlist25 doctor
```

Un refus de récupération cloud demande de vérifier la clé privée et le projet Supabase. Une erreur YouTube ne supprime pas les demandes enregistrées. Un manque d’espace exige de libérer de la place avant de réessayer ; le pack et son ZIP occupent de l’espace supplémentaire.

Les essais du projet sont détaillés dans [le compte rendu de validation](VALIDATION.md). L’installation sur un véritable PC Windows, le transfert sur votre téléphone et la lecture dans Mixxx avec votre matériel restent à vérifier avant la soirée.

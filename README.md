# Les 25 ans de Fanny

Une application en français pour les **25 ans de Fanny, le 17 octobre 2026 dès 19 h, à la salle des fêtes de Fontain** : les invités proposent des musiques et votent ; Fanny choisit les bonnes versions, prépare les fichiers audio et récupère un pack transportable pour Mixxx. L’ancien site PHP a été remplacé.

**Site invités publié : https://bouilloon.github.io/Ekoraid/**. Le déploiement GitHub Pages a réussi le 11 octobre 2026 ; Supabase et la recherche YouTube ont été vérifiés en ligne. Votre PC peut rester éteint pendant la collecte. L’installation ci-dessous sert à votre administration privée et à la préparation des fichiers audio.

Les [pistes d’amélioration](docs/AMELIORATIONS.md) présentent les prochaines évolutions proposées pour les invités, l’organisation et la soirée.

## Deux usages

**Collecter sans laisser votre PC allumé : GitHub Pages + Supabase gratuit.** Pages sert l’interface ; Supabase conserve les propositions et les votes. Le site de Fanny est déjà configuré à l’adresse ci-dessus. La recherche propose de vrais résultats YouTube pendant la saisie, avec choix explicite de la version. Si le fournisseur ne répond pas, le lien ou la demande textuelle reste utilisable. Le [guide de publication gratuit](cloud/README.md) permet de reproduire ce déploiement pour un autre projet. Les quotas et conditions des offres gratuites peuvent évoluer ; le projet ne requiert aucun abonnement ni API payante.

**Préparer les fichiers et fonctionner hors ligne : l’application sur votre PC.** Le studio Windows inclut les outils nécessaires à la préparation audio. L’administration privée s’ouvre sur ce PC ; votre mot de passe suffit, sans compte invité à créer. Les téléchargements continuent si vous fermez le navigateur. Une proposition ne déclenche jamais un téléchargement. Pour récupérer le pack sur un téléphone ou une tablette, vous pouvez créer un lien privé valable dix minutes sur le même Wi-Fi ; il donne accès au seul ZIP choisi.

Vous pouvez aussi utiliser ce serveur local pour la collecte avec un tunnel HTTPS gratuit : votre PC doit alors rester allumé. Le [guide d’installation](docs/INSTALLATION.md) contient cette option et les instructions Windows, Linux et Docker.

## Ouvrir le studio sur Windows 11

La livraison **Studio-Fanny-Windows.zip**, avec ses outils inclus, se télécharge depuis la [dernière release Windows](https://github.com/BOUILLOON/Ekoraid/releases/latest). Les contrôles réalisés sont détaillés dans les notes de la release et le [rapport de validation](docs/VALIDATION.md).

1. [Téléchargez l’archive Windows](https://github.com/BOUILLOON/Ekoraid/releases/latest/download/Studio-Fanny-Windows.zip), faites un clic droit dessus et choisissez **Extraire tout**. Gardez le dossier extrait entier.
2. Ouvrez le dossier `Studio-Fanny`, puis double-cliquez sur **Studio-Fanny.exe**. Python, Node.js, FFmpeg et ffprobe sont inclus : aucun terminal ni installation séparée de ces outils n’est nécessaire.
3. Au premier démarrage, choisissez un mot de passe d’au moins **12 caractères**. La fenêtre permet aussi de renseigner la connexion Supabase privée ; vous pouvez la configurer plus tard directement dans **Réglages & diagnostics** du navigateur, sans arrêter le studio. Le studio s’ouvre automatiquement dans votre navigateur.

Pour relier le studio à la collecte sans l’arrêter, ouvrez **Réglages & diagnostics**, renseignez **Adresse du projet Supabase** et sa clé privée, puis **Vérifier et enregistrer la connexion**. Le changement s’applique immédiatement après vérification ; une clé vide conserve celle déjà enregistrée. La clé reste sur ce PC et ne doit jamais être saisie dans le site public.

La fenêtre de l’application propose aussi **Ouvrir le studio**, **Arrêter le studio**, **Ouvrir mes fichiers**, **Configurer la connexion**, **Changer mon mot de passe**, **Sauvegarder mon studio**, **Restaurer une sauvegarde**, **Ouvrir le diagnostic** et **Reprendre une ancienne installation**. Le formulaire natif **Configurer la connexion** reste une alternative après l’arrêt des services. Les données restent sur votre PC, par défaut dans `%LOCALAPPDATA%\StudioFanny\data`. Fermer le navigateur laisse les traitements continuer ; gardez l’application ouverte pendant la préparation et les transferts.

Administration privée : <http://127.0.0.1:8001>. Invités locaux : <http://127.0.0.1:8000>. Le [guide de l’administration](docs/ADMINISTRATION.md) détaille la connexion et la récupération des musiques. Les scripts de l’ancienne installation et les commandes Linux/Docker restent disponibles dans le [guide d’installation](docs/INSTALLATION.md).

## Le parcours des invités

1. Choisir un pseudo, sans inscription ni email.
2. Taper par exemple **Dalida**, choisir un résultat, coller un lien YouTube de morceau ou de playlist, ou écrire le titre demandé.
3. Voir qui a ajouté quoi, retrouver ses propositions, voter et retirer son vote.
4. Retirer uniquement sa propre proposition après confirmation. Elle disparaît du catalogue public ; son historique reste conservé dans le studio, sans possibilité de la réactiver.

Les recherches signalent la chaîne comme une chaîne, pas comme un artiste vérifié. Aucune version live, reprise ou remix n’est choisie automatiquement. Les mêmes identifiants YouTube sont dédupliqués ; les titres proches sont signalés, pas fusionnés. Les propositions sont **sans limite de nombre par défaut** ; la valeur **0** des réglages signifie illimité. Sans compte, changer de navigateur ou effacer sa session permet de contourner l’identité ; les codes d’invitation sont une option plus stricte.

La livraison ajoute six fonctions : **Plus de résultats** dans la recherche des invités, des **brouillons conservés** après une coupure ou une actualisation, une **actualisation discrète** du catalogue, des **repères de version** tirés du titre, la **recherche dans Modifier** pour l’organisation, et la **sauvegarde/restauration du studio**. Les brouillons de recherche, lien, titre et artiste restent dans ce navigateur ; aucune recherche ni aucun ajout n’est envoyé automatiquement à la réouverture. Le catalogue conserve le défilement et le focus et propose **Afficher les nouveaux morceaux** quand de nouvelles propositions arrivent. Les mentions live, reprise, remix ou instrumental sont indicatives : vérifiez le titre et la chaîne avant de choisir.

Un lien de playlist crée **une proposition pour la playlist entière**. Si l’URL contient une vidéo et une playlist, l’invité choisit **Ce morceau seulement** ou **Toute la playlist**. Le studio retrouve les morceaux quand vous préparez cette proposition. La **durée connue de toute la playlist** additionne les morceaux distincts dont la durée est disponible ; les durées inconnues restent à compléter. Une playlist non analysée ne compte pas comme une playlist de zéro minute.

## Sauvegarder la liste et organiser les moments depuis le site

Sur le site public, **Sauvegarder la liste → Enregistrer la liste CSV** télécharge toute la collecte visible publiquement sur l’appareil utilisé, même si votre PC est éteint. Le CSV conserve les titres, artistes, liens, auteurs, votes, durées, catégories et demandes sans lien. **Enregistrer les liens TXT** produit une URL YouTube par ligne, sans doublons, playlists comprises. Ces fichiers ne contiennent pas d’audio et ne remplacent pas la sauvegarde privée du studio.

L’export recharge la collecte avant l’enregistrement. Si Internet est indisponible et qu’une liste a déjà été chargée sur cette page, il peut enregistrer cette copie datée en signalant que les changements suivants peuvent manquer. Aucun état courant n’est supposé à partir d’une ancienne copie.

Dans **Espace organisation → Mon accès organisation**, le compte Supabase autorisé `info@bouillon.run` permet de gérer les moments de la soirée depuis le site public. Son mot de passe est celui du compte Supabase, distinct du mot de passe du studio local et de la clé privée du projet. **Voir des idées de moments** propose des pistes sans créer ni publier de catégorie automatiquement. Choisissez un nom et un ordre, puis confirmez **Créer ce moment** ; la case **Proposer ce moment aux invités** contrôle sa disponibilité. Les catégories peuvent être renommées, ordonnées ou archivées ; leur historique est conservé.

Les invités utilisent **Choisir les moments → Enregistrer les moments** uniquement pour leurs propres propositions. L’organisation autorisée peut classer toutes les propositions. Aucun compte d’organisation n’est nécessaire aux invités pour proposer ou voter. Le [guide de l’administration](docs/ADMINISTRATION.md#catégories-et-accès-organisation-sur-le-site-public) explique la création et la confirmation du compte autorisé. Si le mail de confirmation est refusé ou n’arrive pas, le propriétaire du projet peut créer ce seul compte depuis Supabase avec **Auto Confirm User**, en conservant la confirmation globale des autres comptes.

## Récupérer toutes vos musiques avec l’interface

1. Double-cliquez sur **Studio-Fanny.exe** sur votre PC. Dans le navigateur qui s’ouvre, entrez votre mot de passe administrateur. Le site Pages collecte les idées PC éteint ; la préparation audio nécessite ce PC allumé et l’application lancée.
2. Dans **Réglages & diagnostics**, configurez au besoin la connexion avec **Vérifier et enregistrer la connexion**, sans arrêter le studio, puis choisissez **Récupérer les propositions du site public**. Sa clé secrète n’est jamais envoyée à Pages. Cette récupération ne télécharge aucune musique.
3. Dans **Propositions**, cliquez sur **Modifier** pour une demande sans lien. **Rechercher sur YouTube** reprend le titre et l’artiste ; comparez les versions, utilisez **Voir d’autres versions** si nécessaire, puis **Choisir cette version** et **Enregistrer**. **Annuler** conserve la proposition initiale. Cette recherche n’ajoute aucun téléchargement. Validez vos choix, puis utilisez **Tout sélectionner** pour les propositions correspondant au filtre, sur toutes les pages, ou **Sélectionner tous les validés**. Les propositions retirées par leur auteur restent exclues.
4. Cliquez sur **Préparer les fichiers de la sélection**, puis confirmez vos droits d’usage. Cette action lance la préparation sur le PC. Les playlists proposées sont d’abord analysées ; les morceaux disponibles rejoignent la file, avec un rapport des éléments indisponibles ou de toute analyse incomplète. Dans **Téléchargements**, les compteurs distinguent les morceaux en attente, en préparation, prêts et en erreur.
5. Dès que des fichiers sont prêts, choisissez **Récupérer les fichiers prêts en ZIP**. Dans **Packs & exports**, cochez les fichiers voulus puis **Créer le ZIP de X fichiers**. Vous pouvez récupérer un premier lot sans attendre les morceaux en erreur ou encore en préparation. Depuis Propositions, **Récupérer les fichiers déjà prêts** limite le pack aux fichiers prêts de votre sélection.
6. Après création, cliquez sur **Télécharger le ZIP sur cet appareil** pour l’enregistrer sur le PC. Pour un téléphone ou une tablette, choisissez **Partager sur mon Wi-Fi**, sélectionnez la connexion locale du PC, créez le QR code et scannez-le sur l’appareil connecté au même Wi-Fi. Le lien privé expire après dix minutes ; **Fermer le partage** le révoque immédiatement. Gardez le PC et l’application allumés jusqu’à la fin du transfert.

Votre navigateur enregistre le ZIP dans son dossier Téléchargements ou vous demande où le placer. Le téléphone reçoit le ZIP via ce lien Wi-Fi, sans ouvrir l’administration privée. Le site Pages seul ne peut pas préparer les fichiers audio. Un réseau qui isole les appareils peut empêcher le transfert ; dans ce cas, récupérez le ZIP sur le PC puis copiez-le par USB.

Dans **Packs & exports → Packs déjà créés**, retrouvez les archives encore présentes dans cette installation, même après avoir fermé le navigateur ou relancé le studio. Vous pouvez récupérer un ZIP existant sans le reconstruire. Après une restauration sur un autre PC, conservez les ZIP finaux séparément de la sauvegarde ou recréez un pack depuis l’audio restauré.

Pour une playlist entière : **Import de playlists → Analyser → Tout sélectionner ou décocher → Importer et préparer les fichiers**. Une URL vidéo avec `list=` demande « ce morceau » ou « toute la playlist ». Les analyses et la file n’ont pas de plafond de nombre par défaut. Un gros lot se prépare progressivement, un morceau à la fois : prévoyez assez d’espace disque et de temps. Les erreurs ne bloquent pas les autres morceaux ; réessayez après avoir corrigé leur cause. Les fichiers ne sont disponibles qu’après contrôle ffprobe, puis recontrôlés au redémarrage. Vous pouvez aussi importer vos fichiers dans **Bibliothèque locale** ; les originaux restent intacts.

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

La préparation écrit l’audio sur votre PC. Dans **Réglages & diagnostics**, le format **MP3** permet de préparer des fichiers `.mp3` pour le pack ; les fichiers deviennent disponibles après conversion et vérification. Le téléchargement du ZIP l’enregistre sur l’appareil qui ouvre le lien. Après extraction, gardez le dossier entier ensemble. La lecture dans Mixxx est indépendante du serveur web et ne modifie pas sa base de données. Convertir en MP3 n’améliore pas la qualité de la source ; le profil source évite une conversion quand le format convient. Le [guide de l’administration](docs/ADMINISTRATION.md) détaille les gestes dans le studio.

## Continuer sur un autre PC

La collecte publique garde la même adresse pendant l’arrêt, la mise à jour ou le changement de PC. Relancer **Studio-Fanny.exe**, configurer un second PC ou restaurer une sauvegarde ne réinitialise pas Supabase et ne coupe pas les ajouts et votes des invités. Chaque PC reste un studio local : sa connexion au même projet récupère la collecte publique, tandis que ses fichiers audio restent sur son disque.

Pour retrouver aussi vos fichiers prêts, cliquez sur **Sauvegarder mon studio**, copiez le ZIP privé sur le second PC, puis utilisez **Restaurer une sauvegarde**, également disponible au premier lancement. Le studio arrête ses services avant la copie et reprend après sauvegarde s’il était ouvert. La restauration présente l’inventaire puis crée un nouveau dossier ; l’ancienne installation est conservée. Le mot de passe et la connexion privée sauvegardés sont repris ; la file reste suspendue jusqu’à votre décision de reprendre les préparations.

Après restauration, utilisez **Récupérer les propositions du site public** pour recevoir les ajouts et votes postérieurs à la sauvegarde. Si le mode hors ligne était actif, désactivez-le avant cette récupération. Aucun réglage ni décision de modération restauré n’est publié automatiquement : vérifiez-les avant de choisir explicitement **Publier les réglages et la modération sur le site public**. Une sauvegarde contient des secrets et des informations privées ; gardez-la sur un support privé. Un ZIP audio de soirée ne remplace pas cette sauvegarde.

## Hors ligne

Activer **Mode soirée hors ligne** : l’interface, le catalogue, les propositions manuelles et les exports restent utilisables ; recherche et téléchargements externes sont désactivés. Les ressources de l’interface sont locales. Pour les téléphones dans la salle, utiliser le Wi-Fi local et son URL/QR code, distincts du lien Internet Pages. Mixxx utilise les fichiers du pack même si le site s’arrête.

## Configuration et sécurité

Dans la version Windows autonome, la connexion privée se configure directement dans **Réglages & diagnostics** du navigateur sans arrêter les services. Le formulaire natif du premier lancement et **Arrêter le studio → Configurer la connexion** restent des alternatives. Les fichiers personnels sont séparés de l’application dans `%LOCALAPPDATA%\StudioFanny`. Une mise à jour remplace le dossier de l’application et conserve vos données. Après avoir arrêté le studio, **Reprendre une ancienne installation** permet de choisir le dossier de votre installation précédente, sans déplacer ni supprimer ses fichiers. Pour la version source, les valeurs initiales sont dans [.env.example](.env.example) et les données dans `data/`.

Les réglages se modifient dans l’administration ; **0** signifie illimité pour les nombres de propositions et d’éléments de playlist. Les ouvertures et la modération locales s’appliquent au serveur local ; le bouton de publication cloud les applique explicitement au site Pages. Les demandes cloud peuvent être récupérées sans écraser vos corrections locales. Mettre à jour l’application n’efface pas les propositions stockées dans Supabase.

URL YouTube strictement normalisées, requêtes publiques limitées, sessions distinctes, CSRF, votes uniques en base, quotas, chemins confinés, noms Windows compatibles et CSV protégés contre les formules. Les outils sont appelés avec des listes d’arguments, sans shell et sans options fournies par les invités. Les uploads audio sont contrôlés sans protocoles réseau. Ne partagez jamais `.env.local`, les sauvegardes ou une clé Supabase secrète. Le service public ne monte pas l’administration ni les fichiers audio.

Les invités peuvent retirer leurs propres propositions ; les autres propositions restent protégées. Le retrait conserve l’historique, les fichiers audio et les traitements déjà lancés. La modération archive également. **Sauvegarder mon studio** conserve la configuration privée, la base, les photos et l’audio après un arrêt propre. Les exports et les fichiers temporaires ne sont pas inclus ; conservez aussi une copie indépendante du pack.

## Vérifier et déboguer

Dans **Réglages & diagnostics**, cliquez sur **Vérifier les prérequis**. Si vous avez oublié votre mot de passe, utilisez **Arrêter le studio → Changer mon mot de passe** dans la fenêtre de l’application. Le [guide d’installation](docs/INSTALLATION.md) fournit les erreurs courantes et la checklist avant la soirée. Les commandes de développement et les tests du navigateur et de la base cloud sont détaillés dans [le rapport de validation](docs/VALIDATION.md).

Dans la fenêtre de l’application, **Ouvrir le diagnostic** fonctionne même avant la configuration et après une erreur des services. Si la fenêtre ne démarre pas, double-cliquez sur **Diagnostic-Studio.cmd**, à côté de `Studio-Fanny.exe`. Il ouvre un rapport ou les consignes de démarrage dans le Bloc-notes, sans PowerShell ni droits administrateur. Le rapport exclut les mots de passe, clés privées et titres des invités. Il sert au dépannage local et ne modifie pas la collecte publique.

Les tests automatiques utilisent des fournisseurs simulés et des sons générés : ils ne téléchargent pas arbitrairement des musiques commerciales. Les contrôles réseau réels et les contrôles du paquet Windows sont rapportés séparément. Aucun téléchargement réel de 10 000 ou 20 000 musiques ni aucune lecture dans Mixxx n’est revendiqué ici. Essayez le studio et le pack sur votre PC et votre téléphone avant la soirée.

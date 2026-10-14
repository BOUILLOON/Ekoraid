# Pistes d’amélioration

Ces idées complètent les fonctions déjà présentes : recherche, propositions de playlists, photos, aperçu vidéo, votes, retrait personnel, durées, préparation groupée, ZIP et partage sur le Wi-Fi. Elles ne sont pas toutes implémentées.

Les corrections livrées portent sur le démarrage Windows sans console, les doublons, les erreurs de fichier pendant les téléchargements et la récupération des packs après rechargement. Les propositions et votes doivent rester conservés pendant toutes les évolutions ; aucune idée ci-dessous ne nécessite de limiter le nombre total de morceaux des invités.

## Évolutions retenues pour cette livraison

- **Plus de résultats** : un bouton charge le lot suivant quand le fournisseur dispose d’autres résultats.
- **Brouillons locaux** : recherche, lien et demande textuelle restent sur l’appareil après une coupure ou un rechargement, sans envoi automatique.
- **Actualisation discrète** : les cartes existantes sont actualisées en conservant le défilement et le focus ; les nouveaux morceaux se consultent avec un bouton.
- **Repères de versions** : live, reprise, remix et instrumental sont signalés d’après le titre. Ces indications restent à vérifier à l’écoute.
- **Recherche dans le studio** : l’éditeur d’une demande permet de chercher et de choisir sa vidéo, puis de sauvegarder explicitement ce choix.
- **Sauvegarde et restauration** : les boutons de la fenêtre Windows enregistrent une archive privée et la restaurent dans une nouvelle installation. L’ancienne reste intacte et la collecte publique n’est jamais remplacée par une sauvegarde locale.
- **Sauvegarde publique CSV/TXT** : enregistrer toute la collecte et ses liens depuis le site, même quand le PC est éteint.
- **Catégories sur le site public** : l’organisation crée les moments ; les invités classent leurs propres demandes, avec plusieurs moments possibles. Les suggestions restent privées jusqu’à leur création.
- **Connexion du studio à chaud** : vérifier et enregistrer Supabase dans les réglages privés sans arrêter l’application ni la collecte.
- **Historique des packs et transfert partiel** : retrouver les ZIP prêts après rechargement et reprendre leur transfert lorsque le navigateur le permet.
- **Diagnostic sans fenêtre** : `Diagnostic-Studio.cmd`, près de l’exécutable, ouvre un rapport sans mot de passe ni clé privée.

Le [guide d’administration](ADMINISTRATION.md) détaille leur utilisation. Les idées restantes ci-dessous restent des propositions.

## Les prochaines priorités proposées

1. **Objectifs par moment** : afficher la durée de chaque catégorie et viser, par exemple, une heure pour le repas et deux heures pour danser.
2. **Classement groupé** : sélectionner plusieurs morceaux et leur affecter les mêmes moments en une fois, avec confirmation.
3. **Packs par catégorie** : créer automatiquement des M3U8 accueil, repas, danse ou gâteau dans le ZIP, en conservant la liste complète.
4. **Demandes à compléter** : une vue dédiée aux propositions sans lien, durées inconnues et erreurs, pour préparer la soirée plus vite.
5. **Préparation de très gros lots** : estimation d’espace et progression persistante ; ZIP fractionnés pour faciliter leur récupération sur téléphone.
6. **Récupération d’un profil invité** : un code privé facultatif pour retrouver ses droits sur un nouvel appareil, sans donner accès au profil d’autrui par son pseudo.

## Pour les invités

| Idée | Utilité |
| --- | --- |
| Retrouver son profil sur un autre appareil | Un code de récupération privé et facultatif permet de conserver ses propositions et droits de retrait sur son nouveau téléphone. Un pseudo seul ne doit pas donner accès au profil d’autrui. |
| Corriger sa proposition | Modifier le titre ou la version avant validation, avec historique, sans retirer puis ajouter de nouveau. |
| Plus de résultats à la demande | « Voir d’autres versions » après les premiers résultats, sans multiplier les requêtes tant que la personne n’en a pas besoin. |
| Repères pour les versions | Indiquer live, reprise, remix ou instrumental lorsqu’on dispose de cette information, sans choisir automatiquement à la place de l’invité. |
| État compréhensible | Afficher « idée reçue », « validée » ou « fichier prêt », après synchronisation de ces informations depuis le studio. Le site ne connaît actuellement pas toute la file du PC. |
| Actualisation discrète | Conserver le défilement et le focus, avec « 3 nouveaux morceaux » au lieu de reconstruire immédiatement l’écran pendant la consultation. |
| Partage natif sur téléphone | Utiliser le menu de partage du téléphone pour envoyer le site directement vers l’application souhaitée. |
| Affichage facile à lire | Option avec textes plus grands et boutons plus larges, navigation clavier et contrastes vérifiés. |
| Brouillons en cas de coupure | Conserver localement le titre ou le lien en cours et proposer son envoi au retour de la connexion. |
| Message d’accueil personnalisable | Fanny choisit le message d’invitation et les indications utiles sans modifier le code du site. |

## Pour choisir la musique

| Idée | Utilité |
| --- | --- |
| Objectif de durée | Composer une sélection pour une durée cible et voir ce qu’il manque, en signalant les durées encore inconnues. |
| Moments de la soirée — livrés | Création et gestion sur le site public ; les invités classent leurs propres propositions parmi les catégories choisies par l’organisation. |
| Sélection par critères | Choisir rapidement les titres validés selon leurs votes, leur durée ou un moment de la soirée, avec aperçu avant action. |
| Versions proches à vérifier | Signaler les titres potentiellement équivalents sans fusionner automatiquement un original, une reprise et un remix. |
| Plusieurs personnes à l’organisation | Prévoir des rôles distincts pour aider à retrouver les liens et organiser la sélection, avec historique des décisions. |
| Notes privées | Garder une consigne « à passer après le gâteau » ou « vérifier cette version » visible seulement pour l’organisation. |

## Pour préparer et récupérer les fichiers

| Idée | Utilité |
| --- | --- |
| Vues de travail dédiées | Retrouver immédiatement les demandes sans lien, les fichiers prêts, les erreurs et les playlists analysées partiellement. |
| Estimation avant un gros lot | Afficher une estimation de l’espace disque et du temps, actualisée pendant le traitement, sans imposer de plafond de nombre. |
| Erreurs avec action précise | Expliquer quoi faire : choisir une autre version, vérifier Internet ou libérer de l’espace, puis réessayer l’élément concerné. |
| ZIP découpés | Produire plusieurs archives plus petites pour un téléphone ou une collection très volumineuse, avec une liste complète de leur contenu. |
| Rapport de préparation | Présenter les fichiers prêts, les morceaux manquants et la raison de chaque échec, avec export du rapport. |
| Réduction des écarts de volume | Mesurer les niveaux et proposer une correction optionnelle sur une copie, en conservant les fichiers sources. |
| Export pour un autre appareil | Prévoir des profils de pack pour clé USB, téléphone et logiciel de soirée, avec noms et playlists compatibles. |
| Télécommande sur le réseau local | Permettre certaines actions depuis le téléphone avec authentification privée, sans rendre l’administration accessible sur Internet. |

## Pour la soirée

| Idée | Utilité |
| --- | --- |
| Vérification guidée avant le jour J | Tester le ZIP, la lecture hors ligne, la clé USB, le téléphone et le matériel audio avec une courte checklist. |
| Écran pour la salle | Afficher le classement ou un message de Fanny en plein écran ; les informations d’administration restent privées. |
| Repères « à jouer / déjà passé » | Suivre la soirée et éviter de repasser involontairement un morceau. |
| Demandes prioritaires | Marquer quelques incontournables choisis par Fanny sans modifier les votes des invités. |

## Pour la fiabilité et le gratuit

| Idée | Utilité |
| --- | --- |
| Diagnostic de démarrage accessible | Proposer un rapport sans clé privée ni mot de passe, même si la fenêtre du studio ne démarre pas. |
| Mise à jour Windows guidée | Annoncer une nouvelle version, vérifier l’archive et conserver la configuration, la file et les fichiers. |
| Signature d’éditeur | Étudier une signature reconnue par Windows, notamment un programme adapté aux projets open source ; sa disponibilité gratuite et l’acceptation ne sont pas garanties. |
| Synchronisation incrémentale | Transférer uniquement les nouveaux éléments et changements, plutôt que toute la collecte à chaque récupération. |
| Pagination côté serveur | Lire les catalogues volumineux par pages avec recherche, tri et totaux calculés côté serveur. La pagination actuelle limite surtout l’affichage du navigateur. |
| État du service gratuit | Montrer la dernière synchronisation et les limites effectivement atteintes, avec une action utile en cas d’indisponibilité. |
| Protection contre les modifications simultanées | Détecter une décision plus récente avant qu’une ancienne copie du studio ne la remplace. |
| Journée de répétition | Rejouer les parcours invités et organisation dans une collecte de démonstration séparée, sans modifier les données de la fête. |


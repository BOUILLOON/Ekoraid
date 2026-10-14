# Collecte publique gratuite : GitHub Pages + Supabase

GitHub Pages sert l’interface invités ; Supabase conserve les propositions, votes et catégories. **Votre PC peut être éteint pendant la collecte et la gestion des catégories sur le site public.** Fanny peut créer ses catégories et classer les propositions après connexion avec son compte Supabase Auth autorisé. Les invités n’ont pas besoin de compte et peuvent classer leurs propres propositions. Les téléchargements et le pack Mixxx restent sur votre PC. Le jour de la soirée, utilisez les fichiers du pack : le site Internet public a besoin d’Internet.

Le site invités est publié à **https://bouilloon.github.io/Ekoraid/**. Le workflow [de publication](https://github.com/BOUILLOON/Ekoraid/actions/runs/38157177635) a terminé avec succès le 11 octobre 2026, après activation de la source **GitHub Actions** dans Pages. Les étapes ci-dessous permettent de reproduire le déploiement.

Le projet choisi est `https://dpcohlyczczaqovblzin.supabase.co`. Sa configuration **publique** est dans `cloud/public-config.json`. Le SQL et la fonction de recherche ont été déployés sur ce projet et vérifiés en ligne. Le rapport [de validation](../docs/VALIDATION.md) distingue ces contrôles du déploiement Pages et des essais locaux.

**Pour administrer le site déjà publié, suivez le [guide de l’administration](../docs/ADMINISTRATION.md).** L’application Windows vous permet de renseigner la connexion privée dans une fenêtre, sans terminal. Les sections 1 à 3 ci-dessous servent à reproduire le déploiement ; elles ne sont pas à refaire pour utiliser le site de Fanny.

## 1. Préparer Supabase

Un compte Supabase gratuit et un projet de l’offre Free suffisent. Aucun compte n’est demandé aux invités. Si vous avez déjà le projet choisi, ouvrez-le dans [le tableau de bord Supabase](https://supabase.com/dashboard).

1. Ouvrez **SQL Editor → New query**.
2. Collez le contenu complet de [`supabase.sql`](supabase.sql), puis **Run**. Exécutez ensuite [`categories.sql`](categories.sql). Les deux scripts sont réexécutables et conservent les données. Si vous réappliquez le schéma principal plus tard, réappliquez aussi l’extension catégories après lui. Ne créez pas de politique permettant à `anon` de lire ou modifier les tables.
3. Dans **Project Settings → API Keys**, relevez l’URL du projet et la clé **publishable** (`sb_publishable_…`) ou **anon** ancienne génération. Ces deux valeurs sont publiques. Elles seules vont dans `public-config.json` et GitHub Pages.
4. Gardez toute clé **secret** / **service_role**, le mot de passe de base de données et les jetons personnels hors du dépôt et du navigateur invités.

Le SQL crée ses propres tables `playlist25_*` : sessions, sources, propositions, votes, invitations, catégories, adresses organisatrices autorisées et quotas. Toutes ont RLS activé, sans permission de table pour `anon`/`authenticated`. Le public utilise des RPC limitées. Les suggestions de catégories et leur gestion exigent un compte Auth autorisé et confirmé ; les suggestions ne créent aucune catégorie automatiquement. La liste d’adresses autorisées reste privée. Les RPC de snapshot, invitations, autorisation d’adresses et publication depuis le PC sont réservées au rôle privilégié.

Pour un déploiement depuis cette session Codex, un jeton personnel `SUPABASE_ACCESS_TOKEN` autorisé sur ce projet peut être injecté dans les **réglages sécurisés de l’environnement**, jamais envoyé dans le chat ni commité. Le script prévu utilise ce jeton sans afficher les réponses privées :

```powershell
python cloud/deploy_supabase.py
```

Ce jeton de gestion sert au déploiement. Il est distinct de la clé publique du site et de la clé secrète utilisée par le PC pour synchroniser. Le helper applique le schéma principal puis les catégories **dans une transaction commune**, vérifie les 21 fonctions attendues et contrôle la nouvelle RPC anonyme après rechargement du cache Supabase. Ce contrôle ne crée ni compte, ni invité, ni proposition. Une erreur de vérification ne doit pas être présentée comme un site prêt.

```powershell
python cloud/deploy_supabase.py --sql-only
python cloud/deploy_supabase.py --functions-only
```

`--sql-only` conserve l’extension catégories, la liste d’adresses autorisées et l’historique. `--functions-only` déploie uniquement la recherche YouTube et ne touche pas au schéma. Après une migration, une nouvelle RPC peut brièvement répondre 404 pendant le rechargement du cache PostgREST ; le helper fait six vérifications bornées puis signale explicitement une disponibilité non confirmée si nécessaire.

### Accès organisation sur le site public

La connexion **Organisation** est indépendante du mot de passe du studio Windows. Elle utilise l’e-mail et le mot de passe du compte Supabase Auth ; aucun mot de passe n’est transmis dans le chat ni enregistré dans le dépôt. Les JWT restent dans `sessionStorage`, dans l’onglet du navigateur, et sont retirés lors de la déconnexion. Une inscription ou un pseudo invité ne donne aucun droit d’organisation.

Dans Supabase **Authentication → URL Configuration**, définissez **Site URL** sur `https://bouilloon.github.io/Ekoraid/` et autorisez cette URL dans **Redirect URLs**. Pour un autre déploiement, utilisez son URL Pages. Gardez la confirmation des e-mails activée. Le lien de confirmation revient sur le site ; ses jetons sont retirés du fragment de l’URL après récupération par l’adaptateur.

Le propriétaire du projet autorise l’adresse choisie dans **SQL Editor** avec :

```sql
select public.playlist25_approve_organizer('adresse-organisatrice@example.com', true);
```

Cette opération peut précéder la création du compte. Elle est réservée au propriétaire/service_role ; ne placez aucune clé privilégiée dans le site. Pour retirer les droits, appelez la même RPC avec `false`. Le serveur vérifie l’identité `auth.uid()` et l’adresse confirmée dans `auth.users`, puis l’autorisation privée ; il ne fait confiance ni au pseudo ni à une adresse déclarée par le navigateur.

Sur le site, **Organisation → Créer mon accès** permet une inscription explicite avec un mot de passe de 12 caractères minimum. La création du compte demande une confirmation ; elle ne connecte pas automatiquement et ne promet pas la livraison de l’e-mail. L’envoi par défaut de Supabase peut être restreint, et aucun SMTP personnalisé n’a été validé ici. Si le mail est refusé ou n’arrive pas, le propriétaire peut ouvrir **Authentication → Users → Add user → Create new user**, renseigner l’adresse autorisée, choisir le mot de passe dans ce tableau de bord privé et confirmer individuellement ce compte avec **Auto Confirm User**. Cela ne change pas la confirmation globale des autres inscriptions. Connectez-vous ensuite sur le site public. Aucune connexion réelle avec le mot de passe de Fanny n’est revendiquée par les tests automatisés.

Après connexion, créez les moments de votre choix ou utilisez une suggestion pour préremplir le formulaire, puis enregistrez explicitement. Vous pouvez renommer, ordonner et archiver les catégories, ainsi que classer toutes les propositions. L’archivage conserve les anciennes affectations et leurs noms. Une catégorie archivée peut rester sur un morceau déjà classé, ou en être retirée ; elle ne peut pas être nouvellement attribuée. Aucun audio n’est préparé par ces actions publiques.

## 2. Déployer la recherche de morceaux

L’autocomplétion interroge une fonction serveur `music-search`, pas une liste de chansons préremplie. Par défaut elle lit les résultats publics réels de YouTube et propose au maximum cinq vidéos, avec titre, chaîne et durée disponibles. Une chaîne n’est pas présentée comme un artiste certifié. Les directs et résultats de playlists sont écartés. Un clic propose la vidéo choisie ; aucune version live/remix/reprise n’est sélectionnée automatiquement.

Depuis la racine du projet, avec Node.js et le CLI officiel Supabase :

```powershell
npx --yes supabase@2.120.0 login
npx --yes supabase@2.120.0 functions deploy music-search --project-ref dpcohlyczczaqovblzin --workdir cloud --use-api --no-verify-jwt
```

La configuration de la fonction se trouve dans [`supabase/config.toml`](supabase/config.toml). `verify_jwt=false` permet les clés publishable modernes. La fonction applique ses limites par RPC avec une clé publique ; elle n’utilise jamais `service_role`. Ses sorties et accès réseau sont limités à Supabase et aux destinations YouTube/Google fixes.

La recherche renvoie cinq résultats par défaut, puis **Plus de résultats** demande le lot suivant. Le contrat `offset`/`limit` retourne `items`, `has_more` et `next_offset` ; les anciens clients continuent d’utiliser `items`. La version publique parcourt les résultats réellement présents dans la page fournie par YouTube, puis annonce leur fin. Elle n’invente aucune continuation. Une mise à jour de cette fonction seule se déploie avec `cloud/deploy_supabase.py --functions-only`, sans réappliquer le SQL ni réinitialiser la collecte.

YouTube peut bloquer une recherche depuis l’hébergement ou changer son format. Dans ce cas, le site affiche une erreur et conserve **Coller un lien YouTube**, **Proposer un titre** et l’ouverture de YouTube pour choisir le lien. Aucune réponse n’est inventée. Un lien ajouté sans métadonnées est affiché comme « Vidéo YouTube … (titre à vérifier) » ; l’organisateur vérifiera les informations depuis son PC.

Pour rendre la recherche plus stable, vous pouvez activer **YouTube Data API v3**, facultativement, avec son quota gratuit :

1. Créez ou sélectionnez un projet dans [Google Cloud Console](https://console.cloud.google.com/).
2. Dans **APIs et services → Bibliothèque**, activez **YouTube Data API v3**.
3. Dans **Identifiants**, créez une clé API et limitez les API autorisées à **YouTube Data API v3**.
4. Dans Supabase **Edge Functions → Secrets**, ajoutez **YOUTUBE_API_KEY**. Collez sa valeur uniquement dans ce réglage sécurisé, jamais dans `public-config.json`.

Sans cette clé, la recherche publique YouTube est utilisée. Avec cette clé, la fonction utilise les API officielles de recherche et de durée. Le serveur plafonne l’option API à 90 recherches par jour pour rester sous le quota gratuit usuel de 10 000 unités (100 unités par recherche, plus les détails). N’activez pas de quota payant. Vérifiez les quotas actuels dans votre console et la [documentation YouTube](https://developers.google.com/youtube/v3/getting-started#quota). Les requêtes infructueuses peuvent aussi consommer du quota.

## 3. Publier le site invités sur GitHub Pages

Un compte GitHub et un dépôt public permettent d’utiliser Pages gratuitement. Aucun abonnement ni domaine acheté n’est nécessaire. Le site déployé contient seulement `index.html`, la feuille de style, le code invités avec la gestion publique des catégories, l’adaptateur cloud, une illustration locale et la clé publique. Le studio privé sur le PC, SQLite, audio, journaux et `.env.local` sont exclus par une liste de fichiers explicite.

Pour construire le site à partir de la configuration publique déjà fournie :

```powershell
python cloud/build_pages.py --output _site
```

Pour un autre projet, modifiez `cloud/public-config.json` ou utilisez :

```powershell
python cloud/build_pages.py --url https://REFERENCE.supabase.co --public-key CLE_PUBLISHABLE_OU_ANON --output _site
```

Le constructeur refuse les clés `service_role`/secret et un dossier de sortie contenant des fichiers étrangers. Il ne publie rien à lui seul. Il peut réactualiser son propre dossier généré ; utilisez un nouveau dossier vide si vous y avez ajouté d’autres fichiers.

Publication par GitHub Actions :

1. Placez [`pages-workflow.yml`](pages-workflow.yml) dans `.github/workflows/pages.yml` s’il n’y est pas déjà.
2. Dans GitHub **Settings → Pages**, choisissez **Source: GitHub Actions**.
3. Le workflow utilise `public-config.json`. Pour le remplacer sans modifier ce fichier, ajoutez les variables de dépôt **SUPABASE_URL** et **SUPABASE_PUBLIC_KEY** dans **Settings → Secrets and variables → Actions → Variables**. Ce sont des valeurs publiques ; n’y mettez jamais une clé secrète.
4. Ouvrez **Actions → Publier le site invités → Run workflow**.
5. Le job de publication affiche l’URL Pages. Ouvrez-la sur votre téléphone et vérifiez les parcours ci-dessous avant de la partager.

Le workflow est manuel : un commit ne publie pas automatiquement. Si les workflows ne sont pas disponibles, créez un dépôt public réservé au site, envoyez le contenu de `_site` (`index.html` et le dossier `static`, pas le dossier du projet entier), puis activez Pages depuis la branche `main`, dossier `/`.

## 4. Récupérer la collecte sur votre PC

[Téléchargez la dernière version Windows autonome](https://github.com/BOUILLOON/Ekoraid/releases/latest/download/Studio-Fanny-Windows.zip). Faites **Extraire tout**, puis double-cliquez sur **Studio-Fanny.exe** dans le dossier `Studio-Fanny`. Gardez le dossier extrait entier. Le [guide d’installation](../docs/INSTALLATION.md#windows-11--version-autonome-recommandée) détaille le premier démarrage et les validations disponibles pour chaque version.

Au premier démarrage, ou après **Arrêter le studio → Configurer la connexion**, renseignez le projet `https://dpcohlyczczaqovblzin.supabase.co`. Cliquez sur **Ouvrir la page de ma clé Supabase**, copiez sa clé **Secret** ou **service_role** dans le champ privé, puis **Enregistrer et ouvrir le studio**. La connexion est vérifiée avant d’être enregistrée ; une erreur conserve la configuration précédente.

La clé reste sur le PC. Ne la copiez jamais dans le site, les variables publiques du workflow ou une capture d’écran. Les clés secret modernes `sb_secret_…` et les clés JWT service_role anciennes sont prises en charge. Au lancement, le studio récupère la collecte avant d’ouvrir le navigateur, sauf en mode hors ligne, et ne prépare aucun audio automatiquement.

**Pour l’installation depuis les sources uniquement**, complétez le fichier privé `.env.local` déjà créé, en conservant les autres valeurs et les secrets générés :

```dotenv
SUPABASE_URL=https://dpcohlyczczaqovblzin.supabase.co
SUPABASE_SERVICE_ROLE_KEY=VOTRE_CLE_SECRETE_OU_SERVICE_ROLE
PUBLIC_URL=VOTRE_LIEN_GITHUB_PAGES
```

Ce fichier est ignoré par Git ; ne le partagez pas. Pour le site de Fanny, `PUBLIC_URL` est `https://bouilloon.github.io/Ekoraid/`.

1. Ouvrez l’administration locale `http://127.0.0.1:8001` et connectez-vous.
2. Dans **Réglages & diagnostics**, cliquez **Récupérer les propositions du site public**. Cette opération lit un snapshot cohérent des propositions et votes ; elle ne télécharge aucun audio.
3. Modérez les propositions, corrigez les titres et associez les demandes textuelles à une source précise. Les corrections, fichiers audio, votes locaux et états locaux sont conservés lors des récupérations suivantes.
4. Pour appliquer les ouvertures/fermetures, le nombre de propositions par invité, les métadonnées et la modération au site Internet, cliquez explicitement **Publier les réglages et la modération sur le site public**. **0** dans **Propositions par invité** signifie illimité. Une modification locale seule ne change pas le site public.
5. Sélectionnez les sources autorisées, confirmez vos droits d’usage et lancez les téléchargements locaux. Préparez ensuite le pack Mixxx et vérifiez-le avec Internet coupé.

La récupération met à jour les votes cloud, y compris les votes retirés. Les votes faits localement dans la salle restent distincts et sont conservés. Un rapprochement local entre plusieurs propositions est publié en archivant les anciennes entrées publiques et en regroupant les votes, avec un seul vote par session pour le morceau résultant.

La publication ne transfère aucun audio ni secret. L’association d’une demande manuelle à une autre source/URL reste locale ; le titre et l’artiste corrigés sont publiés, mais le lien original de la proposition cloud reste conservé. Les choix de téléchargement et exports restent toujours privés.

Les invités peuvent aussi proposer un **lien de playlist YouTube entière**, conservé comme une seule proposition. Une URL qui comprend une vidéo et une playlist demande le choix **Ce morceau seulement** ou **Toute la playlist**. Lors de **Préparer les fichiers de la sélection**, le studio analyse les playlists choisies avant de préparer les morceaux disponibles, et signale les éléments indisponibles ou toute analyse incomplète. Le site public n’analyse ni ne télécharge ces fichiers à lui seul.

Le total public **Durée connue de toute la playlist** additionne les durées disponibles des morceaux distincts. Les durées manquantes et les playlists non analysées restent à compléter : ce total partiel ne les assimile pas à zéro minute. La préparation locale conserve aussi l’ordre d’origine des playlists pour les exports.

## 5. Invitations et limites

Un jeton aléatoire de 256 bits conservé dans le navigateur identifie l’invité. Seul son SHA-256 est stocké côté serveur. Deux invités portant le même pseudo, ou utilisant le même Wi-Fi, restent distincts. Le public n’obtient ni les jetons/hachages, ni les identifiants des autres invités. La base interdit les votes doublés par session.

Sans compte, changer de navigateur ou effacer son stockage peut contourner la limite « une personne = une session ». Pour un contrôle plus strict, activez les invitations cloud dans SQL Editor :

```sql
select public.playlist25_create_invitations(10);
update public.playlist25_settings set invitation_required=true where singleton;
```

Les codes apparaissent seulement au moment de leur création ; seule leur empreinte est enregistrée. Transmettez chaque code individuellement. Un code ne peut être lié qu’à une session. Activer l’option après le début de la collecte oblige aussi les anciens invités à renseigner un code pour proposer/voter/rechercher. Les codes générés par l’administration **locale** n’ouvrent pas le site cloud : créez les codes cloud avec la requête ci-dessus.

Les propositions sont illimitées par défaut : `max_proposals=0`. Il n’y a pas de plafond cumulatif de nombre d’invités, de propositions ou de votes imposé par l’application. Les actions restent limitées par minute pour éviter les abus : 20 recherches/minute par jeton et 120 globalement, avec des budgets distincts pour les autres actions. Les mutations sont transactionnelles. Les doublons de vidéo et les demandes manuelles exactement identiques proposent un vote ; les titres proches produisent un avertissement sans fusion automatique des versions.

Les réglages avancés peuvent être ajustés dans SQL Editor :

```sql
update public.playlist25_settings
set event_name='La playlist des 25 ans', max_proposals=0
where singleton;
```

GitHub et Supabase appliquent aussi leurs quotas et peuvent suspendre un service gratuit inactif ou dépassant ses limites. Consultez [l’offre Supabase](https://supabase.com/pricing) et [les limites Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits) ; vérifiez l’état du projet avant de partager le lien. Ces services ne doivent jamais être nécessaires à la lecture du pack local pendant la soirée.

## Vérifications et tests

Tests locaux reproductibles, sans téléchargement de musiques :

```powershell
node cloud/test_search.mjs
node cloud/test_adapter.mjs
node cloud/test_categories_adapter.mjs
python cloud/test_sync.py
python cloud/test_sql.py
python cloud/test_categories_sql.py
python -m pytest -q tests/test_deploy_helpers.py tests/test_cloud_routes_deploy.py
```

Les tests SQL utilisent Docker et PostgreSQL 16 jetable, sans port publié. Le premier vérifie les sessions, doublons, invitations, quotas, votes concurrents/retraits, accès privés, publication atomique et fusion. Le test catégories applique aussi le déploiement composé dans une transaction : il vérifie que les propriétaires, votes, adresses autorisées et catégories survivent aux répétitions, que seuls les comptes confirmés/autorisés peuvent gérer les catégories et que deux invités au même pseudo ne peuvent pas modifier les propositions de l’autre. Les conteneurs sont arrêtés à la fin. Le contexte Auth de ce test est simulé ; il ne crée aucun vrai compte et n’envoie aucun e-mail.

Avec Deno installé, les tests de la fonction Edge :

```powershell
deno check cloud/supabase/functions/music-search/index.ts
deno test --allow-env cloud/supabase/functions/music-search/index_test.ts
```

Ces tests contrôlent les structures de réponses YouTube, les destinations fixes, le budget serveur, l’absence de faux résultats et la non-divulgation de la clé API. Ils utilisent des réponses simulées. Ils ne prouvent pas que YouTube acceptera les requêtes depuis votre projet Supabase. Le SQL et la fonction ont aussi été validés localement dans PostgreSQL/Deno ; aucun fonctionnement réel dans Mixxx n’est revendiqué.

Un **test réseau réel séparé**, effectué le 11 octobre 2026 sur le projet indiqué, a confirmé : session et liste publiques HTTP 200 ; tables privées, snapshot et publication administrateur refusés HTTP 401 avec la clé publishable ; recherche « Dalida » HTTP 200 via `youtube-public-search`, sans clé YouTube optionnelle. Cinq vidéos réelles ont été reçues, dont « Dalida - Il venait d’avoir 18 ans (Clip Officiel) » (188 s) et « Dalida - Le temps des fleurs (Official Lyric Video) » (238 s). Les autres résultats comprenaient une interview et une compilation : l’invité doit choisir précisément, sans sélection automatique. Ce test n’a créé aucune proposition, identité ni vote et n’a téléchargé aucun audio. Il confirme la réponse du fournisseur à cette date, pas une disponibilité permanente.

Après publication, sur le lien Pages réel :

- Choisissez un pseudo, proposez un titre accentué, recherchez « Dalida » et choisissez précisément la version souhaitée si le fournisseur répond.
- Ajoutez deux variantes d’un même lien YouTube : la seconde doit proposer un vote.
- Avec un second navigateur portant le même pseudo, votez puis retirez le vote ; les sessions restent indépendantes.
- Vérifiez que liens/manuels fonctionnent si la recherche échoue, puis testez avec votre PC éteint.
- Récupérez la collecte sur le PC, vérifiez les votes, publiez une fermeture et confirmez-la sur le lien public.
- Vérifiez que le site public ne contient aucune bibliothèque audio ni commande de téléchargement. L’espace Organisation public sert à gérer les catégories et exige un compte Auth autorisé.

Pour déboguer : une erreur RPC 404 indique généralement un script SQL manquant ou un cache en cours de rechargement ; les RPC catégories/organisation nécessitent `categories.sql` après le schéma principal. Une recherche 404 indique que `music-search` manque ; une erreur 401/403 de synchronisation indique une clé locale incorrecte ou des droits manquants. Un compte Auth non confirmé ou absent de la liste autorisée ne peut pas organiser l’événement. Une recherche 503 peut venir de YouTube : conserver les parcours lien/manuels et, si souhaité, activer l’API gratuite optionnelle. Consultez les journaux Edge dans Supabase sans publier de secrets.

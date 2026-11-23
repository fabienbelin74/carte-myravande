# Carte collaborative – Myravande

Une page web où l'on survole les cases pour voir leur numéro. Chacun peut la mettre à jour en déposant une capture d'écran de sa carte : tout le reste est automatique.

## Mettre à jour la carte (pour les membres du groupe)

1. Faites une capture d'écran de votre carte du jeu (le zoom n'a pas d'importance, mais gardez des hexagones bien visibles).
2. Nommez le fichier **AAAA-MM-JJ_Pseudo.png**, par exemple `2026-11-24_Kakita.png` (date de la capture, puis votre pseudo).
3. Sur le dépôt GitHub, ouvrez le dossier **captures**, puis **nouvelles**.
4. Cliquez sur **Add file → Upload files**, glissez l'image, puis cliquez sur **Commit changes** (bouton vert).
5. Attendez 2 à 3 minutes, puis rechargez la page de la carte (Ctrl+F5).

Si la capture n'a pas pu être recalée (image trop zoomée, trop peu de carte visible), elle est déplacée dans `captures/refusees` et la carte reste inchangée. Le détail est dans l'onglet **Actions** du dépôt (dernière ligne, rubrique « Rapport »).

En cas de désaccord entre deux captures, la plus récente l'emporte et l'ancienne valeur est gardée dans l'historique de la case (case entourée d'orange avec « Changements »).

## Installation (une seule fois, par le responsable)

1. Créez un compte gratuit sur https://github.com (« Sign up »).
2. Cliquez sur **+ → New repository**. Nom : `carte-myravande`. Choisissez **Public** (voir la remarque ci-dessous), puis **Create repository**.
3. Sur la page du dépôt vide, cliquez sur **uploading an existing file** et glissez **tout le contenu du dossier décompressé** (pas le .zip lui-même). Attention : le dossier caché `.github` doit être envoyé aussi ; si votre navigateur ne le glisse pas, voir « Dépannage » ci-dessous. Cliquez sur **Commit changes**.
4. **Settings → Pages** : sous « Build and deployment », choisissez Source **Deploy from a branch**, Branch **main**, dossier **/docs**, puis **Save**. Au bout d'une ou deux minutes, l'adresse de la carte s'affiche en haut de cette page.
5. **Settings → Actions → General** : dans « Workflow permissions », cochez **Read and write permissions**, puis **Save**.
6. **Settings → Collaborators → Add people** : invitez les membres du groupe (ils doivent avoir un compte GitHub gratuit). C'est gratuit et sans limite de nombre.
7. Test : déposez une capture dans `captures/nouvelles` comme décrit plus haut.

## Tunnels et liaisons

Les tunnels (pointillés) et les liaisons en tirets sont enregistrés dans `carte/carte.json`, rubrique `liens` : une ligne par lien, avec ses deux cases d'extrémité. Ils ne sont pas détectés automatiquement lors d'une mise à jour. Quand une capture montre un nouveau tunnel, demandez à Claude de l'ajouter : il suffit de lui donner la capture.

## À savoir

- **Public ou privé** : avec un compte gratuit, la page web n'est disponible que si le dépôt est public. Tout le monde pourra alors voir la carte et les captures si on lui donne l'adresse, mais seuls les collaborateurs invités peuvent la modifier. Pour garder le dépôt privé avec une page privée, il faut un abonnement payant (GitHub Pro, environ 4 $/mois).
- **Coût** : gratuit. Une mise à jour consomme environ 1 minute des 2 000 minutes mensuelles offertes.
- **Revenir en arrière** : toute modification est conservée. Dans l'onglet **Commits**, on peut annuler une mise à jour.
- **Dates** : si le nom du fichier ne commence pas par la date, la date du jour est utilisée et marquée « estimée ».

## Dépannage

- *Le dossier `.github` n'est pas envoyé* : sur le dépôt, **Add file → Create new file**, tapez `.github/workflows/maj-carte.yml` comme nom, collez le contenu du fichier du même nom, puis **Commit**.
- *Rien ne se passe après un dépôt* : onglet **Actions**. Une croix rouge indique une erreur ; cliquez dessus et copiez le message pour le montrer à Claude.
- *Une zone de la carte est fausse* : redéposez une capture plus récente qui la montre bien.

## Contenu technique (inutile pour l'utilisation courante)

- `carte/` : les données (cases, terrains, historique, mosaïque).
- `docs/` : la page web publiée.
- `maj_carte.py` : lit une capture et la fusionne dans `carte/`.
- `traiter_images.py` : traite les images de `captures/nouvelles`, puis régénère `docs/`.
- `.github/workflows/maj-carte.yml` : lance le traitement à chaque dépôt d'image.

# Website Change Detector

Projet complet de monitoring web pour surveiller `https://www.voyage.benin.bj`, détecter les modifications de contenu ou de structure, historiser les versions et envoyer des alertes email automatiques.

## Fonctionnalités

- Scan automatique toutes les 5 minutes
- Détection des changements HTML, texte, structure, formulaires, boutons, images, CSS et JavaScript
- Fingerprint des assets liés pour mieux repérer les changements CSS, JS et images
- Comparaison entre la dernière version et la nouvelle version avec `DeepDiff`
- Historique complet des versions et des changements dans SQLite
- Alertes email via SMTP Gmail
- Email de confirmation possible meme quand aucun changement n'est detecte
- Dashboard Flask moderne avec lancement manuel de scan
- Logs détaillés dans `logs/website_monitor.log`
- Configuration centralisée via `.env`
- Option pour ignorer les proxies système si l’environnement injecte un proxy invalide

## Structure du projet

```text
Website Change Detector/
├── app.py
├── monitor.py
├── .env.example
├── .github/workflows/website-monitor.yml
├── requirements.txt
├── README.md
├── .env
├── database/
│   └── schema.sql
├── logs/
├── static/
│   ├── css/style.css
│   └── js/app.js
└── templates/
    ├── base.html
    └── dashboard.html
```

## Installation

### 1. Créer et activer un environnement virtuel

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 2. Installer les dépendances

```powershell
pip install -r requirements.txt
```

### 3. Configurer `.env`

Renseigne au minimum les variables SMTP Gmail :

```env
EMAIL_FROM=votre-adresse@gmail.com
SMTP_USER=votre-adresse@gmail.com
SMTP_PASSWORD=votre-mot-de-passe-app-gmail
EMAIL_TO=lordelesly@gmail.com
```

Ne publie jamais `.env` sur GitHub. Utilise `.env.example` comme modèle local.

## Configuration SMTP Gmail

Pour Gmail, il faut utiliser un **mot de passe d’application** :

1. Active la validation en 2 étapes sur ton compte Google.
2. Va dans la gestion du compte Google.
3. Ouvre `Sécurité`.
4. Crée un `Mot de passe des applications`.
5. Copie ce mot de passe dans `SMTP_PASSWORD`.

Configuration utilisée :

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=votre-adresse@gmail.com
SMTP_PASSWORD=votre-mot-de-passe-app-gmail
EMAIL_FROM=votre-adresse@gmail.com
EMAIL_TO=lordelesly@gmail.com
IGNORE_SYSTEM_PROXIES=true
EMAIL_ON_NO_CHANGE=false
```

## Lancement du projet

### Démarrer le dashboard Flask

```powershell
python app.py
```

Le dashboard sera disponible sur :

```text
http://127.0.0.1:5000
```

### Lancer un scan manuel en ligne de commande

```powershell
python monitor.py --once
```

### Lancer le monitoring continu en CLI

```powershell
python monitor.py --loop
```

## Mise en ligne avec GitHub Actions

Cette version permet de lancer le scan automatiquement toutes les 5 minutes depuis GitHub Actions, sans laisser ton PC allumé.

### 1. Pousser le projet sur GitHub

Initialise le dépôt puis pousse le code :

```powershell
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/TON_COMPTE/TON_REPO.git
git push -u origin main
```

### 2. Ajouter les secrets GitHub

Dans GitHub :

`Repository > Settings > Secrets and variables > Actions > New repository secret`

Ajoute ces secrets :

- `TARGET_URL`
- `EMAIL_TO`
- `EMAIL_FROM`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`

Valeurs recommandées :

```text
TARGET_URL=https://www.voyage.benin.bj
EMAIL_TO=leslyaikpe@gmail.com
EMAIL_FROM=votre-adresse@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=votre-adresse@gmail.com
SMTP_PASSWORD=votre-mot-de-passe-app-gmail
```

### 3. Activer le workflow

Le workflow est déjà prêt dans :

`/.github/workflows/website-monitor.yml`

Il fonctionne :

- toutes les `5 minutes`
- à la demande avec `Run workflow`
- avec `EMAIL_ON_NO_CHANGE=true` pour envoyer aussi un email quand le site n'a pas change

### 4. Historique et persistance

Le workflow réutilise :

- `database/website_monitor.db`

Quand un changement utile est détecté, l'état est commité automatiquement dans le dépôt pour que les scans suivants puissent continuer la comparaison.

### 5. Limite importante

GitHub Actions fait très bien le scan et l’envoi d’email, mais ne garde pas ton dashboard Flask “en ligne” en continu. Pour avoir le dashboard accessible sur Internet, il faudra plus tard déployer Flask sur un hébergeur.

## Fonctionnement interne

1. Le système récupère le HTML courant du site surveillé.
2. Il extrait un snapshot structuré :
   - textes
   - titres
   - formulaires
   - boutons
   - images
   - feuilles CSS
   - scripts JS
   - structure HTML
3. Il compare ce snapshot avec la dernière version stockée.
4. Si une différence est détectée :
   - une nouvelle version est enregistrée
   - un diff détaillé est sauvegardé
   - une alerte email est envoyée
   - le dashboard affiche immédiatement le changement

## Base de données

La base SQLite est créée automatiquement dans :

```text
database/website_monitor.db
```

Tables principales :

- `versions` : historique complet des snapshots
- `changes` : changements détectés avec résumé et diff
- `scans` : journal de chaque scan

## Logs

Les logs applicatifs sont écrits dans :

```text
logs/website_monitor.log
```

## Mode production

Le fichier `.env` est déjà préparé avec :

```env
APP_ENV=production
FLASK_DEBUG=false
ENABLE_SCHEDULER=true
```

Pour un lancement plus stable en production sous Windows :

```powershell
waitress-serve --host=127.0.0.1 --port=5000 app:app
```

## Commandes terminal récapitulatives

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python monitor.py --once
python app.py
```

## Remarques

- Le premier scan crée une version de référence.
- Les scans suivants comparent la nouvelle version à la dernière sauvegardée.
- Si le site change très souvent côté contenu dynamique, il peut être utile d’ajuster la logique de normalisation dans `monitor.py`.
- En mode GitHub Actions, `RECORD_SCANS_WITHOUT_CHANGES=false` évite de créer un commit toutes les 5 minutes quand rien ne change.
- Mets `EMAIL_ON_NO_CHANGE=true` si tu veux recevoir aussi un email de routine quand aucun changement n'est detecte.

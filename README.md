# Chaffo code

Chaffo code est un petit coding agent en CLI. Il utilise un modele local via Ollama, par defaut `gemma4:e2b`, et peut lire/modifier des fichiers ou lancer des commandes dans un workspace.

Le projet est volontairement simple pour rester modifiable par un junior :

- pas de dependance Python externe ;
- un client HTTP Ollama minimal ;
- une boucle agentique avec plan automatique ;
- quelques outils faciles a comprendre ;
- autorisations de session pour eviter les confirmations repetitives.

## Prerequis

1. Installer Ollama : https://docs.ollama.com/
2. Recuperer ou lancer Gemma 4 :

```powershell
ollama run gemma4:e2b
```

L'API Ollama locale repond normalement sur :

```text
http://localhost:11434/api
```

## Installation du projet

Depuis ce dossier :

```powershell
python -m pip install -e .
```

Ensuite, le CLI est disponible :

```powershell
chaffo-code --help
```

Tu peux aussi le lancer sans installation editable :

```powershell
python -m chaffo_code --help
```

## Verifier Ollama

```powershell
chaffo-code --check
```

Lister les modeles locaux :

```powershell
chaffo-code --models
```

## Utilisation interactive

```powershell
chaffo-code --workspace .
```

Puis :

```text
chaffo> liste les fichiers Python
chaffo> ajoute un README pour ce projet
chaffo> lance les tests
```

## Utilisation en une commande

```powershell
chaffo-code "explique la structure du projet" --workspace .
```

## Plans automatiques

Pour chaque demande, Chaffo code commence par demander au modele un plan court.
Il execute ensuite les taches une par une :

```text
> Plan
  1. Inspecter les fichiers du projet
  2. Modifier le fichier concerne
  3. Lancer une verification

> Tache 1/3
  Inspecter les fichiers du projet
```

Cette approche rend les demandes complexes plus lisibles et plus faciles a suivre.
Pour une demande simple, le plan peut contenir une seule tache.

## Autorisations

Par defaut, Chaffo code utilise le mode `session`.

Cela signifie :

- la lecture de fichiers ne demande pas d'autorisation ;
- la premiere ecriture demande une autorisation, puis les autres ecritures sont autorisees pour la session ;
- la premiere commande d'un meme type demande une autorisation, puis cette commande est autorisee pour la session.

Exemples :

```powershell
chaffo-code --permission-mode session
```

Pour demander confirmation a chaque action sensible :

```powershell
chaffo-code --permission-mode ask
```

Pour autoriser automatiquement les ecritures et commandes, utile dans un dossier de test :

```powershell
chaffo-code "cree un script hello.py" --workspace . --yes
```

`--yes` est equivalent a `--permission-mode auto`.

## Affichage

Le CLI utilise des couleurs ANSI et un affichage plus proche d'un coding agent moderne :

- bannier d'accueil ;
- plan visible ;
- progression par tache ;
- affichage des outils appeles ;
- resultats d'outils compacts en mode verbose.

Pour desactiver les couleurs :

```powershell
chaffo-code --no-color
```

## Changer de modele

```powershell
chaffo-code --model gemma4:e4b
```

ou avec une variable d'environnement :

```powershell
$env:CHAFFO_MODEL = "gemma4:e4b"
chaffo-code
```

## Architecture

```text
chaffo_code/
    cli.py            Point d'entree CLI
    config.py         Configuration partagee
    ollama_client.py  Appels HTTP vers Ollama
    agent.py          Boucle modele -> outils -> modele
    tools.py          Outils fichiers et commandes
    ui.py             Affichage CLI et couleurs
```

## Comment fonctionne la boucle agentique

1. Le CLI recoit une demande utilisateur.
2. `ChaffoAgent` demande un plan au modele.
3. Le plan est affiche dans le terminal.
4. Chaque tache est executee dans l'ordre.
5. Pour chaque tache, le modele peut appeler des outils.
6. Python execute les outils autorises.
7. Le resultat est ajoute aux messages avec le role `tool`.
8. Le modele est rappele jusqu'a terminer la tache.
9. Une synthese finale est affichee.

Cette approche suit le pattern "coding agent / harness" : le harness controle la boucle, les outils, les confirmations et le workspace.

## Outils disponibles

| Outil | Role |
| --- | --- |
| `list_files` | Liste les fichiers du workspace. |
| `read_file` | Lit un fichier avec numeros de ligne. |
| `write_file` | Cree ou remplace un fichier. |
| `replace_in_file` | Remplace un texte exact dans un fichier. |
| `run_command` | Lance une commande dans le workspace. |

## Securite

Chaffo code est un projet pedagogique, pas un sandbox parfait.

Les protections incluses :

- l'agent ne peut agir que dans le workspace choisi ;
- les ecritures et commandes demandent une autorisation de session par defaut ;
- quelques commandes dangereuses sont bloquees ;
- les sorties d'outils trop longues sont tronquees.

Pour une vraie utilisation production, il faudrait ajouter :

- une sandbox plus stricte ;
- une allowlist de commandes ;
- une revue de diff avant ecriture ;
- des tests automatiques ;
- une journalisation plus complete.

## Modifier le projet

Points d'entree utiles :

- Pour changer le prompt systeme : `chaffo_code/agent.py`
- Pour ajouter un outil : `chaffo_code/tools.py`
- Pour changer les options CLI : `chaffo_code/cli.py`
- Pour modifier l'appel Ollama : `chaffo_code/ollama_client.py`
- Pour changer l'apparence du terminal : `chaffo_code/ui.py`

### Ajouter un outil

Dans `tools.py` :

1. Ajouter une methode, par exemple `count_lines`.
2. Ajouter son schema dans `_build_tools`.
3. Relancer le CLI.

Le modele verra automatiquement le nouvel outil.

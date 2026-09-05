# Veille TUNEPS — drapeaux, banderoles, oriflammes, guirlandes

Surveille automatiquement le portail des marchés publics tunisiens
[tuneps.tn](https://www.tuneps.tn/portail) et envoie un e-mail dès qu'un
appel d'offres ou une consultation correspond au métier : drapeaux,
oriflammes, banderoles, guirlandes, fanions, kakémonos, impression sur tissu.

---

## Installation (Linux)

```bash
cd tuneps-scrapper
./install.sh --timer
```

Le script crée un environnement virtuel `.venv`, installe les trois
dépendances (`requests`, `PyYAML`, `python-dotenv`), lance les tests, puis
installe un minuteur `systemd` **utilisateur** — pas besoin d'être root.

Sans `--timer`, l'installation se fait sans planification et la veille se
lance à la main.

> **Le PC a redémarré ?** Il n'y a rien à refaire pour les e-mails — voir
> [Après un redémarrage du PC](#après-un-redémarrage-du-pc).

### Configurer l'e-mail

Ouvrez `.env` (créé automatiquement) :

```ini
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=votre.adresse@gmail.com
SMTP_PASSWORD=xxxxxxxxxxxxxxxx      # mot de passe d'application, 16 caractères
MAIL_TO=papa@example.com,vous@example.com
```

Avec Gmail, il faut un **mot de passe d'application** : activez la validation
en deux étapes sur le compte, puis créez-en un sur
<https://myaccount.google.com/apppasswords>. Le mot de passe habituel du
compte est refusé par Google depuis 2022.

Vérifiez :

```bash
./veille test-email
```

---

## Utilisation

```bash
./veille run                 # analyse + e-mail (mode nominal)
./veille run --no-email      # analyse, écrit seulement le rapport HTML
./veille run --months-back 6 # remonter 6 mois en arrière
./veille run --mode keywords # recherche serveur sur tout l'historique
./veille status              # état de la base, dernières exécutions
./veille check "…"           # tester le dictionnaire sur un intitulé
./veille doctor              # diagnostiquer le réseau et réparer la chaîne TLS
./veille web                 # interface locale de suivi (tableau + onglets)
./veille web --lan           # ... accessible aussi depuis les autres PC du réseau
```

Chaque exécution écrit `reports/dernier_rapport.html` (ouvrable dans un
navigateur) en plus de l'e-mail. La base `data/tuneps.db` mémorise ce qui a
déjà été signalé : **un avis n'est jamais notifié deux fois**.

Le premier lancement remonte tout le mois courant et le mois précédent —
c'est normal, ensuite seules les nouveautés arrivent.

### L'interface de suivi

```bash
./veille web
```

Ouvre `http://127.0.0.1:8765` dans le navigateur : un tableau de tous les avis
retenus, avec trois onglets.

| Onglet | Contenu |
|---|---|
| **En cours** | tout ce qui n'a pas encore été traité, **trié par échéance la plus proche** |
| **Soumis** | les avis pour lesquels l'offre a été déposée |
| **Écartés** | les faux positifs |

Chaque ligne porte deux boutons : **Soumis** et **Écarter**. Ne rien cliquer
laisse l'avis en cours — c'est le comportement par défaut, il n'y a rien à
faire pour qu'un dossier reste dans la liste principale. Un avis déplacé par
erreur se récupère depuis son onglet avec « Remettre en cours », ou
immédiatement avec « Annuler » dans le bandeau qui apparaît en bas.

Le compte à rebours (J-14, J-3, dépassé) se calcule à l'ouverture ; en dessous
de 7 jours il passe en orange, en dessous de 3 en rouge. La barre d'outils
filtre par texte, par niveau de correspondance et par source, et peut masquer
les délais dépassés. Chaque en-tête de colonne trie.

Le bouton « Actualiser depuis TUNEPS » relance une analyse sans quitter la
page — pratique pour vérifier tout de suite après avoir vu passer un e-mail.

Les décisions sont enregistrées dans `data/tuneps.db`, la même base que la
veille : elles survivent à la fermeture du navigateur et un avis écarté ne
remonte jamais, même si TUNEPS le republie. Par défaut la page n'est servie
que sur `127.0.0.1` — rien n'est exposé au réseau, rien n'est hébergé
ailleurs ; voir ci-dessous pour l'ouvrir aux autres postes. L'interface ne
tourne que tant que la commande est ouverte — Ctrl-C l'arrête.

### Y accéder depuis un autre PC du réseau

Par défaut la page n'écoute que sur cette machine. Pour l'ouvrir aux autres
postes du bureau :

```bash
./veille web --lan
```

Le programme affiche alors l'adresse à taper sur les autres machines :

```
Interface : http://127.0.0.1:8765/     (cette machine)
            http://192.168.1.24:8765/  (depuis les autres postes)
```

Pour que ce soit permanent, mettez-le dans `config.yaml` — c'est aussi ce que
lit le service systemd, donc l'interface repart ouverte après un redémarrage :

```yaml
web:
  host: 0.0.0.0
  port: 8765
```

#### Mettez un mot de passe

Dès que le service est ouvert au réseau, **n'importe quel appareil connecté au
même Wi-Fi** — un téléphone d'invité, l'ordinateur d'un visiteur — peut lire la
liste et cocher « soumis » ou « écarté ». Dans `.env` :

```ini
WEB_PASSWORD=choisissez-quelque-chose
```

Le navigateur demande alors les identifiants à la première ouverture, puis les
retient. L'identifiant n'est pas vérifié, seul le mot de passe compte : mettez
ce que vous voulez dans le premier champ. Le programme prévient au démarrage
quand il est ouvert sans mot de passe.

#### Ouvrir le port dans le pare-feu

Si la page ne répond pas depuis l'autre poste alors qu'elle marche en local,
c'est presque toujours le pare-feu :

```bash
# ufw (Ubuntu, Debian)
sudo ufw allow from 192.168.1.0/24 to any port 8765 proto tcp

# firewalld (Fedora, RHEL)
sudo firewall-cmd --permanent --add-port=8765/tcp --zone=home
sudo firewall-cmd --reload
```

Adaptez `192.168.1.0/24` à votre réseau — `ip -4 addr` vous donne le vôtre.
Restreindre à votre sous-réseau vaut mieux qu'ouvrir le port à tout le monde.

#### Ce qu'il ne faut pas faire

**Ne redirigez pas ce port depuis votre box Internet.** Le service parle en
HTTP simple, sans chiffrement : le mot de passe circulerait en clair et la
page serait exposée à tout Internet. Pour y accéder de l'extérieur, passez
par un VPN (WireGuard, Tailscale) — la machine reste alors sur son réseau
privé et rien n'est publié.

#### Dépannage

| Symptôme depuis l'autre PC | Cause probable |
|---|---|
| la page ne charge pas du tout | pare-feu, ou `host` resté à `127.0.0.1` |
| la page s'affiche mais les boutons ne font rien | vous n'avez pas rechargé après la mise à jour (Ctrl-Shift-R) |
| `403 requête refusée` | vous avez ouvert la page par une adresse et le navigateur en annonce une autre — utilisez l'URL exacte affichée au démarrage |
| le navigateur redemande le mot de passe en boucle | `WEB_PASSWORD` a changé côté serveur ; videz les identifiants enregistrés |

### Planification

Le minuteur tourne à 07h30, 12h30 et 17h30.

```bash
systemctl --user list-timers tuneps-veille.timer   # prochain déclenchement
systemctl --user start tuneps-veille.service       # lancer tout de suite
journalctl --user -u tuneps-veille.service -n 50   # journal
```

Pour changer les horaires : éditez `OnCalendar=` dans
`~/.config/systemd/user/tuneps-veille.timer` puis
`systemctl --user daemon-reload && systemctl --user restart tuneps-veille.timer`.

Pour que la veille tourne même session fermée :
`sudo loginctl enable-linger $USER`.

---

## Après un redémarrage du PC

**Pour la veille par e-mail : rien à faire.** Le minuteur systemd a été activé
une fois pour toutes par `./install.sh --timer`. Il se relance à chaque
démarrage, et `Persistent=true` lui fait rattraper une exécution manquée si
le PC était éteint à l'heure prévue. Vous n'avez aucune commande à taper.

**Pour l'interface de suivi : une commande.**

```bash
cd ~/documents/personal_proj/tuneps-scrapper
./veille web
```

C'est un programme au premier plan : il s'arrête avec le terminal et ne
revient pas tout seul. (Pour l'ouvrir automatiquement au démarrage, voir
« Ouvrir l'interface au démarrage » plus bas.)

### Vérifier que tout est bien armé — 30 secondes

```bash
cd ~/documents/personal_proj/tuneps-scrapper

systemctl --user is-enabled tuneps-veille.timer    # attendu : enabled
systemctl --user list-timers tuneps-veille.timer   # attendu : une date dans NEXT
./veille status                                    # les dernières exécutions
```

| Ce que vous lisez | Ce que ça veut dire |
|---|---|
| `enabled` + une date dans `NEXT` | tout va bien, rien à faire |
| `disabled` ou `Failed to get unit file state` | le minuteur n'existe plus → relancez `./install.sh --timer` |
| `NEXT` vide ou `n/a` | le minuteur existe mais n'est pas démarré → `systemctl --user start tuneps-veille.timer` |
| `./veille status` sans exécution récente | lancez `./veille run` à la main et lisez le message d'erreur |

### Si le minuteur a disparu

```bash
./install.sh --timer
```

Le script est rejouable sans risque : il ne recrée `.env` que s'il est absent,
ne touche jamais `data/tuneps.db`, et vos avis « soumis » et « écartés » sont
conservés. Il réinstalle simplement l'environnement virtuel et le minuteur.

### Si rien ne tourne quand la session est fermée

Par défaut, systemd arrête les services d'un utilisateur dès qu'il se
déconnecte. Vérifiez :

```bash
loginctl show-user $USER --property=Linger    # attendu : Linger=yes
sudo loginctl enable-linger $USER             # si ce n'est pas le cas
```

### Le PC doit être allumé

Un minuteur ne réveille pas une machine éteinte ou en veille. Ce n'est pas
grave : chaque analyse balaie **le mois courant et le mois précédent**, pas
« depuis la dernière fois ». Un PC resté fermé une semaine ne fait rien
manquer — au réveil, la première exécution rattrape tout. Vous perdez de la
réactivité, pas des avis. Il faudrait rester éteint plus d'un mois pour
créer un trou, et dans ce cas `./veille run --months-back 3` le comble.

Sur un portable, fermer le capot déclenche la mise en veille. Pour que la
veille continue quand il est branché :

```bash
sudo nano /etc/systemd/logind.conf
# HandleLidSwitchExternalPower=ignore
sudo systemctl restart systemd-logind
```

### Ouvrir l'interface au démarrage (optionnel)

```bash
./install.sh --web-service
```

Installe un service utilisateur qui garde `./veille web` en fonctionnement en
permanence : l'adresse `http://127.0.0.1:8765` répond alors dès l'ouverture de
la session, sans terminal ouvert.

```bash
systemctl --user status tuneps-web.service    # état
systemctl --user stop tuneps-web.service      # arrêter
systemctl --user disable tuneps-web.service   # ne plus démarrer automatiquement
```

---

## Passer par GitHub (PC → Pi)

C'est la bonne façon de déployer sur le Pi : on pousse depuis le PC, on tire
sur le Pi, et les mises à jour suivantes sont un `git pull`.

### Avant le premier commit

**`.env` contient le mot de passe d'application Gmail.** Il est exclu par
`.gitignore`, mais si jamais il partait, le supprimer ensuite ne suffirait
pas : il resterait dans l'historique Git, et il faudrait révoquer le mot de
passe chez Google. Un garde-fou `pre-commit` est donc installé par
`./install.sh` — il refuse tout commit contenant `.env`, une base `.db`, un
certificat, ou un mot de passe écrit en clair dans `config.yaml`.

**Faites le dépôt privé.** Pas seulement à cause des secrets : `keywords.py`
est la liste des termes sur lesquels vous vous positionnez, et les exclusions
disent quels marchés vous laissez passer. Pour un concurrent, c'est de
l'information utile.

### Sur le PC

**1. Créez d'abord le dépôt sur GitHub**, avant toute commande git :
<https://github.com/new> → **Private**, et surtout **rien de coché** (ni
README, ni .gitignore, ni licence). Un dépôt pré-rempli créerait un commit
initial et le premier `push` serait rejeté.

**2. Vérifiez que GitHub vous reconnaît en SSH :**

```bash
ssh -T git@github.com
```

Vous devez lire `Hi <votre-pseudo>! You've successfully authenticated`. Si
c'est `Permission denied (publickey)`, aucune clé n'est enregistrée — voir
« La clé SSH » plus bas.

**3. Puis, seulement ensuite :**

```bash
cd ~/documents/personal_proj/tuneps-scrapper
git init && git branch -m main
./install.sh                       # (ré)installe le garde-fou pre-commit
git add -A
git status                         # VÉRIFIEZ : .env ne doit PAS apparaître
git commit -m "Veille TUNEPS"
git remote add origin git@github.com:<vous>/tuneps-scrapper.git
git push -u origin main
```

Si `git status` montre `.env`, arrêtez-vous : le `.gitignore` n'a pas été pris
en compte.

### « ERROR: Repository not found »

GitHub renvoie ce message **aussi bien quand le dépôt n'existe pas que quand
il ne sait pas qui vous êtes** — c'est volontaire, pour ne pas révéler
l'existence d'un dépôt privé à un inconnu. Une seule commande tranche :

```bash
ssh -T git@github.com
```

| Réponse | Cause | Correction |
|---|---|---|
| `Hi <pseudo>! You've successfully authenticated` | l'authentification marche : le dépôt n'existe pas, ou le nom est faux | créez-le sur <https://github.com/new>, puis vérifiez `git remote -v` (casse comprise : `Loorrca` ≠ `loorrca` pour le chemin) |
| `Permission denied (publickey)` | aucune clé SSH enregistrée sur votre compte | voir ci-dessous |

### La clé SSH

```bash
ls ~/.ssh/id_*.pub                                   # en avez-vous une ?
ssh-keygen -t ed25519 -C "pc-$(whoami)" -N ""        # sinon, créez-la
cat ~/.ssh/id_ed25519.pub
```

Collez le contenu dans <https://github.com/settings/keys> → *New SSH key*.
Attention : c'est ici une **clé de compte** (elle vous représente), à ne pas
confondre avec la *deploy key* du Pi, qui ne donne accès qu'à ce dépôt.

Vous pouvez aussi rester en HTTPS, sans clé — GitHub demandera alors un
*personal access token* en guise de mot de passe :

```bash
git remote set-url origin https://github.com/<vous>/tuneps-scrapper.git
```



Ce qui part dans le dépôt : le code, les tests, `config.yaml`, `.env.example`.
Ce qui reste sur chaque machine : `.env`, `data/`, `reports/`, `.venv/`.

### Sur le Raspberry Pi

Le dépôt étant privé, le Pi a besoin d'un accès en lecture. Une **clé de
déploiement** vaut mieux qu'un jeton personnel : elle ne donne accès qu'à ce
dépôt, en lecture seule, et se révoque en un clic.

```bash
ssh-keygen -t ed25519 -C "pi-veille" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

Collez la clé dans GitHub → votre dépôt → *Settings* → *Deploy keys* →
*Add deploy key* (laissez « Allow write access » décoché). Puis :

```bash
git clone git@github.com:<vous>/tuneps-scrapper.git
cd tuneps-scrapper
cp .env.example .env && nano .env   # identifiants — saisis ici, jamais copiés du dépôt
./install.sh --all
```

### Mises à jour ensuite

```bash
# sur le PC
git add -A && git commit -m "nouveaux mots-clés" && git push

# sur le Pi
cd ~/tuneps-scrapper && git pull
systemctl --user restart tuneps-web.service    # seulement si le code web a changé
```

Le minuteur relit le code à chaque exécution : rien à redémarrer pour lui.
`git pull` ne touche jamais `.env` ni `data/` — vos décisions « soumis » et
« écarté » sur le Pi survivent à toutes les mises à jour.

### Si un secret a fuité malgré tout

Ne vous contentez pas de supprimer le fichier et de recommiter : l'historique
le contient toujours, et un dépôt privé peut devenir public par erreur.

1. Révoquez le mot de passe d'application sur
   <https://myaccount.google.com/apppasswords> et créez-en un nouveau.
2. Changez `WEB_PASSWORD` s'il était concerné.
3. Ensuite seulement, nettoyez l'historique (`git filter-repo`) si vous y tenez.

L'ordre compte : la révocation est ce qui protège réellement, le nettoyage de
l'historique n'est que du rangement.

---

## L'installer sur un Raspberry Pi

C'est le meilleur endroit pour cette veille : allumé en permanence, silencieux,
et il consomme moins qu'une ampoule. **Installation normale, pas de conteneur.**
Docker n'apporterait rien ici — trois dépendances Python pures, aucune
bibliothèque système, aucune compilation — et coûterait de la mémoire, des
écritures sur la carte SD et des reconstructions d'image lentes en ARM à chaque
correction. Les unités systemd font déjà le travail d'isolation utile.

Mesuré : **36 Mo de mémoire au pic**, 300 Ko de base pour 400 avis, environ
200 Mo de trafic par mois. Un Pi 4 est très largement dimensionné.

Dans l'ordre — les deux premiers points ne se rattrapent pas après coup.

```bash
# 1. Paquets système
sudo apt update && sudo apt install -y python3-venv git sqlite3

# 2. Fuseau horaire — AVANT d'installer quoi que ce soit de planifié
sudo timedatectl set-timezone Africa/Tunis
timedatectl | grep "Time zone"

# 3. Linger : sans lui, rien ne tourne quand personne n'est connecté
sudo loginctl enable-linger $USER

# 4. Le code
git clone git@github.com:<vous>/tuneps-scrapper.git
cd tuneps-scrapper

# 5. Identifiants — saisis ici, jamais copiés depuis le dépôt
cp .env.example .env
nano .env            # SMTP_USERNAME, SMTP_PASSWORD, MAIL_TO, WEB_PASSWORD

# 6. Le Pi n'a pas d'écran : ouvrir la page au réseau
nano config.yaml     # dans la section « web » : host: 0.0.0.0

# 7. Installation des deux services
./install.sh --all
```

`--all` installe le minuteur (07h30, 12h30, 17h30) **et** le service de
l'interface web, et les démarre tout de suite.

### Vérifier

```bash
./veille doctor            # réseau + chaîne TLS de TUNEPS
./veille test-email        # un message doit arriver
./veille run --no-email    # première analyse
systemctl --user list-timers tuneps-veille.timer     # prochain déclenchement
systemctl --user status tuneps-web.service           # doit être « active (running) »
hostname -I                # l'adresse à taper sur les autres postes
```

Puis, depuis un autre PC : `http://<ip-du-pi>:8765`.

> Les tests lancés par `install.sh` sautent d'eux-mêmes la partie JavaScript
> des comptes à rebours quand ni Node ni Playwright n'est installé — c'est le
> cas sur un Pi, et c'est normal : la moitié Python est vérifiée, la moitié
> navigateur l'est sur votre poste de développement.

### Deux réglages à ne pas oublier

**1. Le fuseau horaire.** Un Pi fraîchement installé est souvent en UTC. Tous
les comptes à rebours (« J-2 », « dépassé ») et les horaires du minuteur sont
calculés en heure locale : sur un Pi en UTC, ils seraient décalés d'une heure
par rapport à la Tunisie.

```bash
timedatectl                                  # vérifier
sudo timedatectl set-timezone Africa/Tunis   # corriger
```

**2. Le linger.** Un Pi tourne sans session graphique ouverte. Sans cela,
systemd arrête les services utilisateur et **plus rien ne s'exécute** :

```bash
sudo loginctl enable-linger $USER
```

C'est l'oubli le plus courant : tout semble installé, et rien ne part.

### Accès depuis les autres postes

Le Pi n'ayant pas d'écran, c'est le mode réseau qui sert. Dans `config.yaml` :

```yaml
web:
  host: 0.0.0.0
  port: 8765
```

puis `WEB_PASSWORD=...` dans `.env`, et `systemctl --user restart tuneps-web`.
La page répond alors sur `http://<ip-du-pi>:8765`. Donnez une adresse fixe au
Pi dans votre box (bail DHCP réservé), sinon l'adresse changera un jour et le
marque-page de votre père ne marchera plus.

### Carte SD et sauvegarde

Les écritures sont minimes (quelques centaines de kilo-octets par jour) et le
journal est plafonné à 4 Mo par rotation, donc l'usure n'est pas un sujet. En
revanche `data/tuneps.db` contient tout l'historique et vos décisions
« soumis » / « écarté » : c'est la seule chose irremplaçable. Une copie
hebdomadaire suffit :

```bash
crontab -e
0 3 * * 0  sqlite3 ~/tuneps-scrapper/data/tuneps.db ".backup '/home/pi/sauvegardes/tuneps-$(date +\%F).db'"
```

(`.backup` de sqlite3 est sûr même si la veille écrit au même moment ; une
simple copie de fichier ne l'est pas.)

### Mettre à jour

```bash
cd ~/tuneps-scrapper && git pull
systemctl --user restart tuneps-web.service
```

Le minuteur relit le code à chaque exécution, il n'y a rien à redémarrer pour
lui. La base et le `.env` ne sont jamais touchés.

---

## Comment ça marche

TUNEPS expose une API JSON publique, sans authentification, que le portail
Angular utilise lui-même :

| Ressource | Endpoint |
|---|---|
| Appels d'offres | `POST /api2/portail/bid/master/data` |
| Consultations | `POST /api2/portail/vSpShopMaster/data` |

Le corps de requête est l'objet de recherche de l'application :

```json
{"dataSearch": [{"key": "shopNmFr", "value": "%drapeau%", "specificSearch": "like"}],
 "listSort": [], "listCol": []}
```

> Piège : `"specificSearch": "LIKE"` en majuscules n'est **pas** un LIKE —
> c'est une égalité stricte, sensible à la casse. Seul `"like"` en minuscules
> accepte les jokers `%` et ignore la casse.

Il n'existe pas d'opérateur de comparaison de dates. En revanche le numéro
d'avis encode l'année et le mois (`20260900572`, `S20260900001`) : un `like`
sur `202609%` renvoie donc tout le mois en quelques centaines de lignes.
C'est ce qui permet une veille légère — environ 4 requêtes et moins de
2 Mo par exécution, au lieu des 150 Mo qu'imposerait le téléchargement
complet des 300 000 avis de l'historique.

Deux modes :

- **`sweep`** (défaut) — récupère le mois courant + les mois précédents, puis
  applique le dictionnaire **en local**, avec tolérance aux fautes. C'est le
  seul mode qui rattrape les intitulés mal orthographiés.
- **`keywords`** — interroge le serveur terme par terme sur tout l'historique.
  Plus rapide et exhaustif dans le temps, mais aveugle aux fautes de frappe.
  Utile pour un rattrapage initial.

### Le certificat de TUNEPS

`www.tuneps.tn` ne renvoie pas sa chaîne de certificats complète : il envoie
son certificat final sans l'intermédiaire qui le relie à une autorité racine
connue. Les navigateurs masquent le défaut — quand un maillon manque, Chrome
va le télécharger tout seul à l'adresse inscrite dans l'extension
*Authority Information Access* du certificat. Python, curl et OpenSSL ne le
font pas, d'où :

```
SSLError: certificate verify failed: unable to get local issuer certificate
```

`tuneps/tlsfix.py` fait ce que fait le navigateur : il lit le certificat
présenté, y trouve l'adresse de l'émetteur, télécharge les maillons
manquants et écrit un magasin de confiance local dans
`data/tuneps-ca.pem`. **La vérification TLS reste active** — on comble un
trou, on ne désactive pas le contrôle. C'est automatique et fait une seule
fois ; ensuite le magasin est réutilisé.

En dernier recours seulement, `verify_ssl: false` dans `config.yaml`
désactive complètement le contrôle. À éviter : plus rien ne garantit alors
que vous parlez bien à TUNEPS.

### Le dictionnaire

`tuneps/keywords.py`, en quatre niveaux :

| Niveau | Effet |
|---|---|
| `CORE_FR` / `CORE_AR` / `CORE_EN` | un seul terme suffit → **correspondance forte** |
| `SECONDARY` | métier de l'impression ; il en faut deux, ou un + un terme de contexte → **à vérifier** |
| `DEMOTE` | fait passer une correspondance forte en « à vérifier » (signalétique métallique, enseignes lumineuses, tampons) |
| `EXCLUSIONS` | annule complètement la détection |

Les intitulés sont normalisés avant comparaison : minuscules, accents latins
retirés, diacritiques arabes retirés, `أ إ آ → ا`, `ة → ه`, `ى → ي`.

### Pièges réels traités

Repérés en analysant les avis 2026 effectivement publiés :

- **`إعلامية` (informatique) contient `أعلام` (drapeaux).** « اقتناء معدات
  إعلامية » revient constamment sur TUNEPS. Résolu par un appariement au
  niveau du mot, avec gestion des préfixes arabes (`ال`, `بال`, `وال`…),
  plus une exclusion explicite.
- **`نباتات زينة` = plantes ornementales**, pas décoration textile. Exclu.
- **`خزينة` (trésorerie) contient `زينة` (décoration).** Résolu par la même
  frontière de mot.
- **`زينة ضوئية` / enseignes lumineuses** = travaux d'électricien →
  rétrogradé en « à vérifier », pas supprimé.
- **Tampons, cachets, panneaux de signalisation** → rétrogradés, sauf si
  l'avis mentionne du tissu (`tissu`, `bâche`, `قماش`, `خياطة`) : dans ce cas
  la correspondance forte est maintenue.
- **Fautes de frappe réelles** : `drapeux`, `drappeaux`, `Avquisition
  drapeaux`, `guirlands`, `BANDROLLES`, `Drapeaux Tunisiee` — toutes
  rattrapées par l'appariement flou.

### Réglages fins

Dans `config.yaml` :

```yaml
fuzzy_threshold: 0.86   # 0.90 = strict, 0.80 = tolérant (plus de bruit)
months_back: 1
extra_keywords: []      # vos propres termes, déclencheurs à eux seuls
extra_exclusions: []    # faux amis à écarter
```

Pour tester l'effet d'un réglage sans rien envoyer :

```bash
./veille check "acquisition de drapaux et guirelandes"
```

---

## Structure

```
tuneps-scrapper/
├── veille                  lanceur (utilise .venv automatiquement)
├── scripts/pre-commit      garde-fou Git : bloque .env et la base
├── install.sh              installation + minuteur systemd
├── run_tests.sh            les trois suites de tests
├── config.yaml             réglages
├── .env                    identifiants e-mail (jamais partagé)
├── tuneps/
│   ├── client.py           API TUNEPS, fenêtrage mensuel
│   ├── keywords.py         le dictionnaire — c'est ici qu'on ajuste
│   ├── matcher.py          normalisation FR/AR/EN + appariement flou
│   ├── store.py            SQLite : mémoire des avis déjà signalés
│   ├── report.py           rendu HTML de l'e-mail et du rapport
│   ├── notify.py           envoi SMTP + notification de bureau
│   ├── webapp.py           serveur local de l'interface de suivi
│   ├── webui.html          la page (tableau, onglets, filtres)
│   ├── tlsfix.py           complète la chaîne de certificats tronquée de TUNEPS
│   └── cli.py              ligne de commande
├── tests/
│   ├── corpus.py           85 intitulés réels étiquetés à la main
│   ├── test_matcher.py     dictionnaire et fautes de frappe
│   ├── test_corpus.py      taux de détection sur données réelles
│   ├── test_pipeline.py    parsing → matching → base → HTML
│   ├── test_tls.py         rejoue la panne de certificat et sa réparation
│   └── test_web.py         serveur local : onglets, persistance, refus tiers
├── data/                   base SQLite + journal
└── reports/                rapports HTML horodatés
```

## Dépannage

| Symptôme | Cause probable |
|---|---|
| `Authentification SMTP refusée` | mot de passe d'application manquant ou validation en deux étapes non activée |
| Aucun avis trouvé, exécution instantanée | TUNEPS injoignable — voir `data/tuneps.log` |
| `CERTIFICATE_VERIFY_FAILED` / `unable to get local issuer certificate` | chaîne TLS incomplète côté TUNEPS — réparée automatiquement, voir plus bas ; sinon `./veille doctor` |
| Trop de faux positifs | montez `fuzzy_threshold` à `0.90`, ou ajoutez le terme gênant dans `extra_exclusions` |
| Un avis a été manqué | `./veille check "l'intitulé"` montre ce que le moteur a vu ; ajoutez le terme dans `extra_keywords` |

Le portail est un service public : la veille reste volontairement légère
(quelques requêtes espacées, trois fois par jour). Inutile de la lancer
toutes les cinq minutes — les avis y sont publiés au fil de la journée
ouvrable et les délais de dépôt se comptent en semaines.

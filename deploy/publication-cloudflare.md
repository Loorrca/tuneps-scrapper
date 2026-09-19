# Publier l'interface sur `tuneps.texbanner.com`

Notes propres à l'installation réelle du Pi. Le README donne la marche à suivre
pas à pas ; ce fichier explique *pourquoi* c'est fait ainsi, pour le jour où il
faudra y revenir.

## Ce qui tourne sur le Pi

| Quoi | Comment | Écoute |
|---|---|---|
| Boutique Tex Banner | Docker, pile `docker-compose.pi.yml` dans `/home/pi/texbanner` | `app` sur `3000:3000` |
| Base de la boutique | conteneur `postgres:16-alpine` | interne |
| Tunnel Cloudflare | conteneur `cloudflare/cloudflared:latest` | aucun port |
| Veille TUNEPS | **natif**, service systemd utilisateur | `0.0.0.0:8765` |

Rien n'écoute sur 80 ni 443 : tout le trafic public entre par le tunnel.

## Le tunnel est « géré à distance »

Le conteneur démarre sur `command: tunnel --no-autoupdate run` avec un
`TUNNEL_TOKEN`, et `docker inspect` ne montre aucun montage. C'est un tunnel
*remotely-managed* : **il n'existe aucun `config.yml`, nulle part.** Les règles
d'ingress sont stockées chez Cloudflare et se modifient dans le tableau de bord
Zero Trust (*Networks → Tunnels → le tunnel → Public Hostname*).

Conséquences pratiques :

- inutile de chercher `/etc/cloudflared/config.yml` : ajouter un sous-domaine se
  fait à la souris, pas dans un fichier ;
- la commande `cloudflared tunnel route dns …` est inutile aussi — le
  tableau de bord crée l'enregistrement DNS proxifié tout seul ;
- le binaire `cloudflared` n'est pas installé sur le Pi et n'a pas à l'être ;
- en revanche l'ingress n'est pas versionné. Ce fichier est la seule trace
  écrite de ce qui est configuré : **tenir la liste ci-dessous à jour.**

Hôtes publics configurés :

| Nom public | Service | Remarque |
|---|---|---|
| `texbanner.com` | `http://app:3000` | résolution par nom de service Docker |
| `tuneps.texbanner.com` | `http://host.docker.internal:8765` | sort du réseau Docker vers le Pi |

## Pourquoi `host.docker.internal` et pas `127.0.0.1`

La veille TUNEPS tourne **en natif** sur le Pi, pas dans la pile Docker (choix
délibéré, voir la note de déploiement). Le conteneur cloudflared est sur le
réseau bridge `texbanner_default`. Donc :

- `127.0.0.1:8765` → la boucle locale **du conteneur**. Il n'y a rien dessus.
  C'est l'erreur naturelle, et elle donne un `502 Bad Gateway`.
- `172.17.0.1:8765` → la passerelle du bridge Docker *par défaut*. Ce conteneur
  n'est pas sur ce réseau-là : sa passerelle est celle de `texbanner_default`.
- `192.168.1.201:8765` → marche, mais code en dur l'adresse LAN du Pi dans une
  configuration qu'on oubliera. Le jour où le bail DHCP change, panne muette.
- `host.docker.internal:8765` → Docker le résout vers l'hôte quel que soit le
  réseau et l'adresse. **C'est ce qu'on utilise**, à condition d'ajouter au
  service `cloudflared` de `docker-compose.pi.yml` :

  ```yaml
  extra_hosts:
    - "host.docker.internal:host-gateway"
  ```

  Sans cette ligne le nom ne résout pas sous Linux (il est fourni d'office sous
  Docker Desktop, pas sur un Pi).

Cela ne fonctionne que parce que l'interface écoute sur `0.0.0.0:8765` et non
sur la boucle locale — l'accès réseau local et l'accès par le tunnel dépendent
tous deux de ce réglage.

### Vérifier la route sans deviner

L'image `cloudflared` n'a pas de shell : on teste depuis un conteneur jetable
placé sur le même réseau.

```bash
docker run --rm --network texbanner_default \
  --add-host host.docker.internal:host-gateway \
  alpine wget -qS -O /dev/null http://host.docker.internal:8765/ 2>&1 | head -3
```

`401 Unauthorized` = **succès** : la route est bonne, c'est le mot de passe de
l'application qui répond. Un `wget: can't connect` ou un blocage = la route est
mauvaise (ligne `extra_hosts` absente, conteneur non recréé, ou pare-feu du Pi
qui bloque le sous-réseau Docker vers 8765).

## Réglages de l'application

Dans le `.env` **du Pi**, jamais dans `config.yaml` (suivi par Git, et le hook
de pre-commit refuse un mot de passe en clair) :

```ini
WEB_PASSWORD=...
WEB_PUBLIC_HOST=tuneps.texbanner.com
```

`WEB_PUBLIC_HOST` fait deux choses :

1. le nom est accepté comme origine légitime par `_guard()`, donc les boutons
   écrivent bien depuis la page publiée même si un intermédiaire réécrit
   l'en-tête `Host` ;
2. `webapp.serve()` **refuse de démarrer** si ce nom est défini alors que
   `WEB_PASSWORD` est vide. Publier sans mot de passe n'est jamais un choix :
   c'est une ligne oubliée. Le service s'arrête plutôt que d'exposer la base.

## La limite des 100 secondes

Cloudflare coupe toute requête dont la réponse tarde plus de ~100 s (**HTTP
524**). La limite est au bord du réseau : ni le tunnel ni l'application ne
peuvent la relever.

**« Importer depuis TUNEPS » en mode profond prend 2 h 30 : cela ne peut pas
aboutir à travers le domaine.** D'où la décision de conserver l'accès réseau
local. Les imports longs se lancent :

- depuis `http://192.168.1.201:8765` sur le réseau du bureau, ou
- en ligne de commande sur le Pi, dans un `tmux` ou `screen`.

Le correctif propre serait de faire de l'import une tâche de fond (identifiant
de job + interrogation périodique) ; ce n'est pas encore écrit. Tout le reste de
l'interface répond en quelques dizaines de millisecondes.

## Cloudflare Access devant

Zero Trust → *Access* → *Applications* → *Add an application* → **Self-hosted**,
nom `Veille TUNEPS`, hôte public `tuneps` + `texbanner.com`. Politique : *Allow*
→ *Include* → *Emails* → les adresses autorisées. Cloudflare envoie un code à
usage unique ; la session est ensuite mémorisée pour la durée choisie.

Gratuit jusqu'à 50 utilisateurs. Sans cette étape, n'importe qui sur Internet
atteint le serveur Python et n'a plus que le mot de passe à franchir — or c'est
un serveur de la bibliothèque standard, pas un frontal durci.

Le mot de passe de l'application **reste actif derrière Access**, volontairement :
deux serrures indépendantes, au cas où une politique Access serait un jour mal
reconfigurée. Voir deux demandes d'identité à la suite est donc normal.

## Attention : d'où vient `docker-compose.pi.yml`

Si ce fichier provient du dépôt `texbanner-shop`, l'ajout de `extra_hosts` doit
être fait **dans le dépôt** puis redéployé — sinon la prochaine mise à jour de la
pile écrase la modification et `tuneps.texbanner.com` tombe en `502` sans raison
apparente. Si le fichier n'est maintenu qu'à la main sur le Pi, en garder une
copie ailleurs.

# RULES.md — règles fondateur (à respecter à chaque session)

1. **Après chaque déploiement en staging (code OU données), fournir
   systématiquement les liens de test** avant toute promotion :
   - **https://staging.confinia.io** : la démo + l'API candidate en même
     origine (`/api/...`) : un seul login couvre tout, le footer affiche la
     version candidate ;
   - **https://staging.api.confinia.io** : l'API candidate seule (curl) ;
   - identifiants basic auth : utilisateur `confinia`, mot de passe dans
     `deploy/secrets.env` (`STAGING_*`) : jamais dans le repo ;
   - le staging sert toujours la **couleur passive** (code candidat et/ou
     données candidates), le public reste sur la couleur active ;
   - rappeler les commandes de suite : `./deploy/deploy-api.sh promote`
     (ou `rollback`), `./deploy/stacks.sh promote <couleur>`.

2. **Toute publication de la démo part sur LES DEUX surfaces** : le miroir
   VM (`www.confinia.io`, servi immédiatement après rsync) ET GitHub Pages
   (`make demo-publish` → https://confinia.github.io/, la cible de
   time-slider.confinia.io et de tous les liens publiés depuis le premier
   partage #maplibre). Vérifier Pages après publication (propagation ~1 min).

Autres règles opérationnelles (détaillées dans `DEV.md`) : rendu mobile
vérifié par captures avant toute publication front ; adresse admin caddy
unique par instance en réseau hôte ; `--no-deps` sur toute commande
podman-compose ciblée ; jamais de source de données contenant U+FFFD.

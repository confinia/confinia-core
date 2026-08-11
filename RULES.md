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

3. **Tout changement passe par une issue GitHub et une pull request** :
   ouvrir l'issue (motivation, source, périmètre : anonymiser toute personne
   extérieure), développer sur une branche, ouvrir la PR avec `Closes #N`,
   la merger **en rebase** seulement après validation en staging (décision du
   fondateur, 2026-08-11 ; squash et merge commit sont désormais désactivés sur
   le dépôt, le choix n'est donc plus possible). Les commits directs sur `main`
   sont réservés aux documents de process (RULES, TODO).
   ⚠️ Conséquence directe : en rebase, **chaque commit de la branche atterrit
   tel quel sur `main`**. L'hygiène de branche cesse d'être cosmétique — plus de
   « fix typo » ni de « wip » : on nettoie avant de merger. En contrepartie
   l'historique reste linéaire et chaque commit est bissectable.

4. **Anglais partout dans le public** : commentaires de code et docs markdown
   du repo en ANGLAIS (exceptions : RULES.md et TODO.md, docs de process).

5. **Jamais de posture business dans le repo public** : stratégie, seuils
   financiers, règles internes → `business/` ou mémoire, jamais un fichier suivi.

6. **Chaque issue expose un test (unitaire ou bout-en-bout), rejoué après
   chaque déploiement** : toute issue livrée ajoute au moins un test qui
   exerce son comportement ; ces tests tournent en CI ET sont rejoués contre
   le déploiement LIVE après chaque promotion (suite post-deploy
   `tests/smoke_prod.py`, à lancer sur staging puis sur la prod promue).
   Un test *skipped* ne vaut pas validation.

7. **Ouvrir une PR en mode BROUILLON dès le début d'une issue** pour suivre
   l'avancement avant la réussite : `gh pr create --draft` sur la branche de
   travail, puis la passer en "ready"/merger seulement une fois les tests
   verts. La PR trace le travail en cours, pas seulement le résultat.

8. **Ne JAMAIS toucher au Caddyfile de la PLATEFORME** — ni sur la VM
   (`debian@confinia:~/projects/platform`), ni via le repo
   https://github.com/confinia/platform. **Le fondateur s'en charge lui-même,
   exclusivement.** Si un changement plateforme est nécessaire, le décrire au
   fondateur (bloc de config proposé + pourquoi) et attendre qu'il l'applique.
   (Le Caddyfile applicatif `deploy/caddy/Caddyfile` de ce repo reste, lui,
   modifiable normalement — c'est un fichier suivi de confinia-core.)

9. **Tout travail passe par une issue ET une PR GitHub, sans exception** :
   c'est la seule trace complète (le pourquoi dans l'issue, le comment dans la
   PR, la validation dans la CI). Y compris les corrections d'une ligne et les
   documents de process : ce qui est commité directement sur `main` échappe à
   la revue, à la CI, et devient invisible six mois plus tard.

10. **Ne jamais réécrire un fichier existant en entier ; l'éditer.** Une
   réécriture depuis une copie périmée supprime silencieusement ce que quelqu'un
   d'autre a ajouté entre-temps : git n'y voit aucun conflit, la CI ne dit rien.
   C'est ainsi que le crédit COGugaison a disparu pendant plusieurs jours
   (PR #23 puis #20, issue #92). Garde-fous en place : le job CI `docs-guard`
   refuse toute suppression de ligne dans les fichiers protégés sans mention
   explicite, et `tests/test_credits_static.py` verrouille les engagements pris
   envers des personnes.

11. **Toujours exposer un lien cliquable en citant une issue ou une PR.**
   Dans tout ce que lit le fondateur (comptes rendus, résumés), écrire
   `[#94](https://github.com/confinia/confinia-core/issues/94)` plutôt que
   `#94` : un numéro nu coûte une recherche manuelle, un lien coûte un clic.
   Dans les messages de commit et les corps de PR, GitHub crée le lien tout
   seul, le numéro nu y reste donc suffisant. (issue #97)
12. **À chaque avancée d'une PR, dire où l'essayer.** Pas seulement quand elle
   est terminée : à chaque point d'étape, répondre explicitement à la question
   « où puis-je jouer avec ? » parmi quatre réponses possibles :
   - **nulle part** — rien d'observable (doc, process), ou code mergé mais non
     déployé ; dans ce cas, préciser ce qui le déploierait ;
   - **sandbox** — https://sandbox.confinia.io (basic auth, Polar en mode test,
     aucun impact comptable) ;
   - **staging** — https://staging.confinia.io / https://staging.api.confinia.io
     (basic auth, couleur passive) ;
   - **production** — l'URL publique.

   L'URL donnée doit être celle qui **montre le changement**, pas la racine du
   site. « Mergé » n'est pas « déployé » : la PR #95 avait été annoncée livrée
   alors que l'API servait encore l'ancienne réponse. (issue #101)
13. **Passer par staging avant la production, et savoir quand staging
   n'existe pas.** Rien ne part en production sans avoir été exercé sur
   **staging.confinia.io**, puis validé par le fondateur : sa relecture fait
   partie du flux, pas de la politesse.
   ⚠️ **Les fichiers statiques n'ont pas de staging** : `./demo` et
   `./deploy/site` sont montés dans le même caddy pour www et pour staging, donc
   un simple rsync vers le miroir **modifie la production immédiatement**. Pour
   eux, la vérification se fait AVANT le rsync (servis localement, contrôlés par
   leur comportement), et le compte rendu dit franchement que c'est déjà en
   ligne. (issue #107 ; le 2026-08-03 un rsync « pour tester » a mis la carte
   de www hors service, sous un chemin bloqué par notre propre filtre)

Autres règles opérationnelles (détaillées dans `DEV.md`) : rendu mobile
vérifié par captures avant toute publication front ; adresse admin caddy
unique par instance en réseau hôte ; `--no-deps` sur toute commande
podman-compose ciblée ; jamais de source de données contenant U+FFFD.

14. **Tenir `ISSUES.md` à jour à chaque fois qu'on touche à une issue ou à une
   PR.** GitHub dit si une issue est ouverte ; il ne dit pas si le travail est
   *déployé*. Le tracker donne, pour chaque issue ouverte, son stade réel
   (issue créée / PR ouverte / PR mergée / bloquée) et l'endroit où l'essayer
   selon les quatre réponses de la règle 12. « Mergé » n'est pas « livré » :
   la PR #95 avait été annoncée livrée alors que l'API servait encore
   l'ancienne réponse, et #99 s'est refermée toute seule alors que la migration
   avait été annulée. (issue #124)

15. **À la fin de chaque action, proposer la prochaine issue ou PR à traiter.**
   Une seule recommandation, pas un menu : une liste classée est une façon de
   ne pas trancher. Dire **pourquoi maintenant** (« bloquée par X, et X vient
   d'être livré » est la bonne forme), de quoi elle dépend, et si cette
   dépendance est une action du fondateur. Quand une autre priorité se défend,
   la donner en une ligne avec son arbitrage, pour que le fondateur puisse
   décider autrement en connaissance de cause.
   **Pourquoi :** le backlog mélange une exposition de sécurité, un manque de
   doc, une fonctionnalité data et une migration bloquée. Sans consigne, on
   reprend ce qu'on vient de toucher — et une issue de sécurité reste ouverte
   une semaine pendant qu'on en ferme trois cosmétiques. L'état sur lequel
   s'appuyer est `ISSUES.md` (règle 14). (issue #140)
